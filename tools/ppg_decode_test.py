#!/usr/bin/env python3
"""ppg_decode_test - decode ONE pTEX chunk to a PNG, for format validation.

Output goes to work/upscaled_test/ which is gitignored. Derived pixel data must
never leave the local work/ tree.

Decode path transcribed from 3sx (ppgSetupTexChunk_3rd, PPGFile.c:1064-1090):
    1. locate chunk, read PPGFileHeader                      (structs.h:1289-1298)
    2. payload  = chunk + (transNums * 3 + 0x10)             (PPGFile.c:1072-1074)
    3. inflate  = ppgDecompress(compress & 3, ...)           (PPGFile.c:408-438)
    4. expected = pitch * height, pitch from the context     (PPGFile.c:1075, 1169-1205)
    5. endian   = ppgChangeDataEndian(.., pixel & 4, formARGB == 0x8888, bitdepth)
                                                             (PPGFile.c:654-673, 1080)
    6. the buffer is then handed to flCreateTextureHandle as a LINEAR bitmap of
       `pitch` bytes per row -- no swizzle. See notes below and format-notes.md.

NOT SWIZZLED: ppgMakeConvTableTexDC()/dctex_linear (PPGFile.c:864-887) is a 32x32
Morton table used only by ppgRenewDotDataSeqs (PPGFile.c:780-848), which is reached
only from njReLoadTexturePartNumG in the Dreamcast Ninja shim (dc_ghost.c:282-285).
It never touches PPG file payloads.
"""

import argparse
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index          # noqa: E402
from ppg_inspect import walk_chunks          # noqa: E402

OUT_DIR = os.path.join("work", "upscaled_test")


def write_png(path, width, height, rgba):
    """Minimal RGBA8 PNG writer (stdlib zlib only)."""
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                                   # filter type 0 (None)
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def argb1555_to_rgba(buf, width, height):
    """formARGB 0x1555: a<<15 | r<<10 | g<<5 | b. Field shifts/lengths from
    ppgSetupContextFromPPG, PPGFile.c:1198-1210 (rs=0xA rl=5, gs=5, bs=0, as=0xF al=1)."""
    out = bytearray(width * height * 4)
    px = struct.unpack(f"<{width * height}H", buf[:width * height * 2])
    alpha_hist = {0: 0, 1: 0}
    for i, v in enumerate(px):
        a = (v >> 15) & 1
        r = (v >> 10) & 0x1F
        g = (v >> 5) & 0x1F
        b = v & 0x1F
        alpha_hist[a] += 1
        o = i * 4
        out[o] = (r << 3) | (r >> 2)      # 5 -> 8 bit, replicate high bits
        out[o + 1] = (g << 3) | (g >> 2)
        out[o + 2] = (b << 3) | (b >> 2)
        out[o + 3] = 255 if a else 0
    return out, alpha_hist


def main(argv=None):
    ap = argparse.ArgumentParser(description="Decode one pTEX chunk to PNG.")
    ap.add_argument("afs")
    ap.add_argument("--entry", type=int, default=0)
    ap.add_argument("--chunk", type=int, default=0, help="which pTEX in the entry")
    ap.add_argument("--opaque", action="store_true",
                    help="force alpha=255 (to inspect RGB when alpha looks inverted)")
    args = ap.parse_args(argv)

    entries, _ = parse_index(args.afs)
    e = entries[args.entry]
    print(f"entry {e['index']}: {e['name']}  offset 0x{e['offset']:X}")

    with open(args.afs, "rb") as f:
        texes = [c for c in walk_chunks(f, e["offset"], e["size"])
                 if c["kind"] == "pTEX"]
        if args.chunk >= len(texes):
            raise SystemExit(f"entry has only {len(texes)} pTEX chunks")
        c = texes[args.chunk]
        print(f"pTEX #{args.chunk} @+0x{c['chunk_offset']:X}: "
              f"{c['width']}x{c['height']} {c['pixel_mode']} "
              f"{c['compress']} formARGB=0x{c['formARGB']:04X} "
              f"pixel_raw=0x{c['pixel_raw']:02X} transNums={c['transNums']}")

        if c["pixel_mode"] != "16bpp direct" or c["formARGB"] != 0x1555:
            raise SystemExit("this validation tool only handles 16bpp / 0x1555")

        payload_start = e["offset"] + c["chunk_offset"] + c["data_offset"]
        payload_len = c["file_size"] - c["data_offset"]
        f.seek(payload_start)
        payload = f.read(payload_len)

    expected = c["unswizzled_size"]                      # pitch * height
    raw = zlib.decompress(payload)                       # compress & 3 == 2
    print(f"payload 0x{payload_len:X} -> inflated {len(raw)} bytes "
          f"(expected {expected}) {'OK' if len(raw) == expected else 'MISMATCH'}")
    if len(raw) != expected:
        raise SystemExit("size mismatch: header interpretation is wrong")

    # ppgChangeDataEndian, PPGFile.c:654-673, called at PPGFile.c:1080 as
    # ppgChangeDataEndian(buf, size, ppg->pixel & 4, ppg->formARGB == 0x8888, bitdepth).
    # depth == 2 and dendL == 0 -> byteswap every u16, i.e. pixels are stored
    # big-endian on disk. depth 0/1 (indexed) is skipped entirely.
    if c["endian_flag"]:
        print("pixel & 4 set: data already little-endian, no swap needed")
    else:
        print("pixel & 4 clear: byteswapping u16 pixels (stored big-endian)")
        b = bytearray(raw)
        b[0::2], b[1::2] = raw[1::2], raw[0::2]
        raw = bytes(b)

    rgba, alpha_hist = argb1555_to_rgba(raw, c["width"], c["height"])
    total = c["width"] * c["height"]
    print(f"alpha bit: {alpha_hist[1]} set ({alpha_hist[1]*100//total}%), "
          f"{alpha_hist[0]} clear")
    if args.opaque:
        rgba[3::4] = b"\xff" * total

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = os.path.splitext(e["name"])[0] or f"entry{e['index']}"
    out = os.path.join(OUT_DIR, f"{stem}_{args.chunk:03d}.png")
    write_png(out, c["width"], c["height"], rgba)
    print(f"wrote {out}  ({c['width']}x{c['height']} RGBA8)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
