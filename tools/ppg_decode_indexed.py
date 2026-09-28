#!/usr/bin/env python3
"""ppg_decode_indexed - decode ONE indexed (pPAL-backed) pTEX chunk to a PNG.

Output goes to work/upscaled_test/ (gitignored). Derived pixel data stays local.

Path transcribed from 3sx:
  texture  ppgSetupTexChunk_3rd            PPGFile.c:1064-1090
  palette  ppgSetupPalChunk                PPGFile.c:484-588
  formats  ppgSetupContextFromPPG/PPL      PPGFile.c:1169-1205 / 1098-1104
  colours  pplColorModeWidth[4]            PPGFile.c:33, used at PPGFile.c:537
  endian   ppgChangeDataEndian             PPGFile.c:654-673, calls at :1080, :566

No swizzle at any bit depth: flPS2ConvertTextureFromContext handles SCE_GS_PSMT4 and
SCE_GS_PSMT8 with a straight flMemcpy (flps2vram.c:975-985), so index bytes are linear
exactly like the 16bpp case. Palettes go through flPS2ConvertContext with type=1
(flps2vram.c:164), but type=1 only switches to linear addressing that ignores pitch --
for a height==1 palette that is identical to normal order (flps2vram.c:1078-1114).
So no CLUT index reordering either.
"""

import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_inspect import walk_chunks                      # noqa: E402
from ppg_decode_test import write_png, OUT_DIR           # noqa: E402

PAL_HEADER_SIZE = 16


def inflate(payload, codec, expected, what):
    if codec == 2:
        raw = zlib.decompress(payload)
    elif codec in (0, 3):
        raw = payload[:expected]
    else:
        raise SystemExit(f"{what}: LZ77 (codec 1) not implemented")
    if len(raw) != expected:
        raise SystemExit(f"{what}: got {len(raw)} bytes, expected {expected}")
    return raw


def byteswap16(buf):
    b = bytearray(buf)
    b[0::2], b[1::2] = buf[1::2], buf[0::2]
    return bytes(b)


def unpack_1555(buf, count, swap_rb=False):
    """ARGB1555 -> list of (r,g,b,a) 8-bit. Shifts from PPGFile.c:1198-1210.

    swap_rb reads the value as ABGR1555 instead. Raw ColorRAM-style palette files
    (bgNN0.bin) are in that order -- see palConvSrcToRam, color3rd.c:426-441, whose
    whole job is swapping R and B between the on-disk order and ColorRAM."""
    out = []
    for v in struct.unpack(f"<{count}H", buf[:count * 2]):
        a = (v >> 15) & 1
        r = (v >> 10) & 0x1F
        g = (v >> 5) & 0x1F
        b = v & 0x1F
        if swap_rb:
            r, b = b, r
        out.append(((r << 3) | (r >> 2), (g << 3) | (g >> 2),
                    (b << 3) | (b >> 2), 255 if a else 0))
    return out


def clut_unswizzle(pal):
    """PS2 GS 256-entry CLUT order: within each block of 32, entries 8-15 swap with
    16-23. Provided only as a diagnostic toggle -- 3sx does NOT do this (see docstring)."""
    out = list(pal)
    for base in range(0, len(pal), 32):
        for i in range(8):
            out[base + 8 + i], out[base + 16 + i] = pal[base + 16 + i], pal[base + 8 + i]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Decode one indexed pTEX chunk to PNG.")
    ap.add_argument("afs")
    ap.add_argument("--entry", type=int, required=True)
    ap.add_argument("--chunk", type=int, default=0, help="which indexed pTEX in the entry")
    ap.add_argument("--pal-chunk", type=int, default=0, help="which pPAL in the entry")
    ap.add_argument("--pal-index", type=int, default=0,
                    help="which sub-palette inside that pPAL")
    ap.add_argument("--pal-entry", type=int, default=None,
                    help="AFS entry holding RAW palette data instead of a pPAL "
                         "(e.g. bgNN0.bin for stageNN.ppg)")
    ap.add_argument("--pal-bank", type=int, default=0,
                    help="which 64-colour bank in the raw palette file "
                         "(stage BGs use bank 0: bgPalCodeOffset=0x12C, bg.c:258-260)")
    ap.add_argument("--pal-argb", action="store_true",
                    help="read raw palette file as ARGB1555 instead of the default "
                         "ABGR1555 (raw ColorRAM files are ABGR)")
    ap.add_argument("--clut-unswizzle", action="store_true",
                    help="diagnostic: apply PS2 GS CLUT entry reorder")
    ap.add_argument("--opaque", action="store_true", help="force alpha=255")
    ap.add_argument("--suffix", default="")
    args = ap.parse_args(argv)

    entries, _ = parse_index(args.afs)
    e = entries[args.entry]
    print(f"entry {e['index']}: {e['name']}  offset 0x{e['offset']:X}")

    with open(args.afs, "rb") as f:
        chunks = list(walk_chunks(f, e["offset"], e["size"]))
        texes = [c for c in chunks
                 if c["kind"] == "pTEX" and c["pixel_mode"].endswith("indexed")]
        pals = [c for c in chunks if c["kind"] == "pPAL"]
        if not texes:
            raise SystemExit("entry has no indexed pTEX chunks")
        t = texes[args.chunk]
        if args.pal_entry is None and not pals:
            raise SystemExit("entry has no pPAL chunk; use --pal-entry "
                             "(see the palette-resolution section of format-notes.md)")
        p = pals[args.pal_chunk] if args.pal_entry is None else None
        print(f"  pTEX #{args.chunk} @+0x{t['chunk_offset']:X}: "
              f"{t['width']}x{t['height']} {t['pixel_mode']} {t['compress']} "
              f"pixel_raw=0x{t['pixel_raw']:02X}")
        if p is not None:
            print(f"  pPAL #{args.pal_chunk} @+0x{p['chunk_offset']:X}: "
                  f"{p['palettes']} x {p['colors_per_palette']} colors "
                  f"formARGB=0x{p['formARGB']:04X} c_mode=0x{p['c_mode_raw']:02X} "
                  f"{p['compress']}")
            if p["formARGB"] != 0x1555:
                raise SystemExit(f"palette formARGB 0x{p['formARGB']:04X} unhandled")

        if t["pixel_mode"] != "8bpp indexed":
            raise SystemExit("only the 8bpp path is validated so far")

        f.seek(e["offset"] + t["chunk_offset"] + t["data_offset"])
        tex_payload = f.read(t["file_size"] - t["data_offset"])
        # trans table: transNums 3-byte records directly after the 16-byte header.
        # ppgWriteQuadUseTrans, PPGFile.c:319-331 -- each record is
        #   [0] palette offset added to the caller's base -> phan[tran[0] + pal]
        #   [1] iPoint  -> sx = iPoint % width_units, sy = iPoint / width_units
        #   [2] cofsXY  -> xs = (cofsXY >> 4) + 1, ys = (cofsXY & 0xF) + 1
        # all in 16-pixel units. This is the per-sub-rectangle palette map.
        f.seek(e["offset"] + t["chunk_offset"] + PAL_HEADER_SIZE)
        tran = f.read(t["transNums"] * 3)

        if p is not None:
            # Palette payload starts right after the 16-byte header: PPGFile.c:534.
            f.seek(e["offset"] + p["chunk_offset"] + PAL_HEADER_SIZE)
            pal_payload = f.read(p["file_size"] - PAL_HEADER_SIZE)
        else:
            # Raw ColorRAM-style palette file: init_trans_color_ram type 2,
            # color3rd.c:262-278. Flat u16 ARGB1555, 64 colours per bank.
            pe = entries[args.pal_entry]
            f.seek(pe["offset"])
            raw_pal = f.read(pe["size"])
            print(f"  raw palette from entry {args.pal_entry} ({pe['name']}): "
                  f"{pe['size']} bytes = {pe['size'] // 128} banks of 64 colours")

    # --- texture: indices, linear, never byteswapped (bitdepth 1 -> early return)
    idx = inflate(tex_payload, t["compress_raw"] & 3, t["unswizzled_size"], "pTEX")
    print(f"  indices: {len(idx)} bytes = {t['width']}x{t['height']} OK "
          f"({len(set(idx))} distinct)")

    if p is not None:
        # --- palette: PPGFile.c:550 mltSize = bitdepth * (total * col_items)
        ncol = p["colors_per_palette"]
        pal_bytes = inflate(pal_payload, p["compress_raw"] & 3,
                            p["entry_bytes"] * p["palettes"] * ncol, "pPAL")
        # ppgChangeDataEndian(.., ppl->c_mode & 4, ..) -- PPGFile.c:566
        if not p["endian_flag"]:
            pal_bytes = byteswap16(pal_bytes)
            print("  palette c_mode & 4 clear: byteswapped (stored big-endian)")
        stride = ncol * p["entry_bytes"]        # PPGFile.c:578
        sub = pal_bytes[args.pal_index * stride:(args.pal_index + 1) * stride]
    else:
        # Raw ColorRAM files are little-endian u16: init_trans_color_ram type 2
        # copies them with no byteswap (color3rd.c:262-272).
        ncol = 64                               # ColorRAM[512][64], color3rd.h:8
        sub = raw_pal[args.pal_bank * 128:(args.pal_bank + 1) * 128]
        print(f"  bank {args.pal_bank}, little-endian u16, "
              f"{'ARGB' if args.pal_argb else 'ABGR'}1555")
    pal = unpack_1555(sub, ncol, swap_rb=(p is None and not args.pal_argb))
    if args.clut_unswizzle:
        pal = clut_unswizzle(pal)
        print("  [diagnostic] PS2 CLUT entry reorder applied")

    used = max(idx)
    print(f"  max index used = {used} (palette has {ncol} entries)"
          f"{'  OVERFLOW' if used >= ncol else ''}")

    w, h = t["width"], t["height"]
    rgba = bytearray(w * h * 4)
    if p is None and t["transNums"]:
        # Per-sub-rectangle palettes from the trans table.
        banks = [unpack_1555(raw_pal[b * 128:(b + 1) * 128], 64, swap_rb=not args.pal_argb)
                 for b in range(len(raw_pal) // 128)]
        uw = t["width_units"]
        print(f"  trans table: {t['transNums']} sub-rect(s), "
              f"banks used {sorted({tran[k * 3] for k in range(t['transNums'])})}")
        for k in range(t["transNums"]):
            pofs, ipoint, cofsxy = tran[k * 3], tran[k * 3 + 1], tran[k * 3 + 2]
            sx, sy = ipoint % uw, ipoint // uw
            xs, ys = (cofsxy >> 4) + 1, (cofsxy & 0xF) + 1
            bank = banks[pofs]
            for y in range(sy * 16, min((sy + ys) * 16, h)):
                for x in range(sx * 16, min((sx + xs) * 16, w)):
                    o = (y * w + x) * 4
                    rgba[o:o + 4] = bytes(bank[idx[y * w + x]])
    else:
        for i, v in enumerate(idx):
            rgba[i * 4:i * 4 + 4] = bytes(pal[v])
    if args.opaque:
        rgba[3::4] = b"\xff" * (w * h)

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = os.path.splitext(e["name"])[0] or f"entry{e['index']}"
    out = os.path.join(OUT_DIR, f"{stem}_{args.chunk:03d}{args.suffix}.png")
    write_png(out, w, h, rgba)
    print(f"  wrote {out}  ({w}x{h} RGBA8)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
