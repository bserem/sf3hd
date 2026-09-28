#!/usr/bin/env python3
"""ppg_repack - rebuild a stage .ppg from (possibly upscaled) chunk PNGs.

Generalized across every stage. Reads work/decoded/<stem>/manifest.json for the
original header fields, trans records and chunk order, and a directory of
chunk_NN.png. That directory defaults to work/tiles/<stem> - the output of
tools/cut_pages.py, which cuts the upscaled pages back onto the 128px grid -
falling back to work/upscaled/<stem> (a legacy tile-at-a-time upscale) and then
work/decoded/<stem> (1x, un-upscaled).

Character manifests in work/decoded (frame_NNN.png + "frames") are skipped:
there is no .ppg to rebuild for them yet.

Output formats:

  --mode indexed-hd
      Requires the patched renderer. Keeps original transfer records and stores
      2x–4x indexed pixels, marked with formARGB 0x480N (density N). Supports
      external palette_source metadata, including Opening/title.bin.

  --mode direct32   (default, recommended)
      Rewrites each pTEX as pixel mode 3 / PSMCT32 / formARGB 0x8888, transNums 0.
      ppgSetupTexChunk_2nd sets the CI flag from the file's own pixel field
      (`if ((ppg->pixel & 3) < 2) hnof->b16[1] |= 0x4000;`, PPGFile.c:718-720), so a
      mode-3 chunk automatically stops using a palette -- no engine change needed.
      Undrawn area is carried as alpha 0, which frag_direct.glsl discards, matching
      the trans table's "only these sub-rects are drawn" behaviour exactly.
      No scale ceiling beyond width/height being u8 in 16px units (max 4080px).

      REQUIRES an engine fix. flPS2ConvertContext halves alpha for 32-bit sources
      (GS convention: 0x80 == opaque, flps2vram.c:1115-1131) but the PC renderers
      upload PSMCT32 as plain 8-bit RGBA, so opaque texels would draw at ~50%
      coverage and every stage would look washed out. See "the GS alpha
      convention" in docs/format-notes.md. No stock asset hits that path.

  --mode indexed
      Keeps 8bpp + the trans table, re-quantizing each sub-rect against its own
      64-colour bank in bgNN0.bin. Preserves the original palette animation hooks,
      but caps at 2x (iPoint is u8 over a width_units grid, so units <= 256, and
      cofsXY nibbles cap a sub-rect at 16 units) and cannot represent colours
      outside the source banks -- an upscaler's new intermediate tones are lost.

Non-pTEX chunks (the one pPAL in stage07) are copied verbatim in stream order.
Rewrite/extra chunks have no grid slot but keep their exact stream position.
"""
import argparse
import io
import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_inspect import walk_chunks                      # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402
from verify_decoded import read_png                      # noqa: E402

DEC = os.path.join("work", "decoded")
TILES = os.path.join("work", "tiles")
UPS = os.path.join("work", "upscaled")
OUT = os.path.join("work", "repacked")


def src_root(stem, override):
    """work/tiles, then work/upscaled, then the 1x decode."""
    if override:
        return override
    for root in (TILES, UPS):
        if os.path.isdir(os.path.join(root, stem)):
            return root
    return DEC


def align4(n):
    return (n + 3) & ~3


def build_tex(width_units, height_units, pixel, form, trans, payload, codec=2):
    """PPGFileHeader (structs.h:1289-1298) + trans table + payload, all BE."""
    body = trans + (zlib.compress(payload, 9) if codec == 2 else payload)
    size = 0x10 + len(body)
    head = struct.pack(">4sIBBBBHH", b"pTEX", size, width_units, height_units,
                       codec, pixel, form, len(trans) // 3)
    return head + body


def to_bgra32(px, w, h):
    """RGBA8 rows -> PSMCT32 payload.

    formARGB 0x8888 declares B at bit 0, G at 8, R at 16, A at 24 (PPGFile.c
    0x8888 case), i.e. **BGRA** bytes in little-endian memory -- confirmed by
    flPS2ConvertTextureFromContext's PSMCT32 case (flps2vram.c:1030-1047), whose
    destination overrides rs=0 / bs=0x10 to produce RGBA for the GL upload. So the
    conversion is exactly an R/B swap and the source must be BGRA.

    We also set bit 0x4 in the pixel field so ppgChangeDataEndian returns early
    (PPGFile.c:655-657) instead of byte-swapping every u32. Writing RGBA without
    that bit rotates the channels to (A,B,G,R): an orange (245,144,77) sky renders
    pink (255,77,144) with alpha taken from the red channel, which is what the
    first 4x build did."""
    b = bytearray(px)
    b[0::4], b[2::4] = px[2::4], px[0::4]
    return bytes(b)


def quantize(px, w, h, banks, trans, uw, scale):
    """8bpp indices, each sub-rect quantized against its own bank."""
    idx = bytearray(w * h)
    caches = {}
    for k in range(len(trans) // 3):
        pofs, ipoint, cofsxy = trans[k * 3], trans[k * 3 + 1], trans[k * 3 + 2]
        sx, sy = ipoint % uw, ipoint // uw
        xs, ys = (cofsxy >> 4) + 1, (cofsxy & 0xF) + 1
        bank = banks[pofs]
        cache = caches.setdefault(pofs, {})
        for y in range(sy * 16 * scale, min((sy + ys) * 16 * scale, h)):
            for x in range(sx * 16 * scale, min((sx + xs) * 16 * scale, w)):
                o = (y * w + x) * 4
                key = bytes(px[o:o + 4])
                v = cache.get(key)
                if v is None:
                    if not px[o + 3]:
                        v = next((i for i, c in enumerate(bank) if not c[3]), 0)
                    else:
                        best, bd = 0, 1 << 30
                        for i, c in enumerate(bank):
                            if not c[3]:
                                continue
                            d = ((c[0] - px[o]) ** 2 + (c[1] - px[o + 1]) ** 2
                                 + (c[2] - px[o + 2]) ** 2)
                            if d < bd:
                                best, bd = i, d
                        v = best
                    cache[key] = v
                idx[y * w + x] = v
    return bytes(idx)


def scale_trans(trans, uw_old, scale):
    """Rescale trans records into the scaled 16px-unit grid."""
    out = bytearray()
    uw_new = uw_old * scale
    for k in range(len(trans) // 3):
        pofs, ipoint, cofsxy = trans[k * 3], trans[k * 3 + 1], trans[k * 3 + 2]
        sx, sy = (ipoint % uw_old) * scale, (ipoint // uw_old) * scale
        xs, ys = ((cofsxy >> 4) + 1) * scale, ((cofsxy & 0xF) + 1) * scale
        ip = sy * uw_new + sx
        if ip > 255:
            raise SystemExit("indexed mode: iPoint %d > 255 at scale %dx; use "
                             "--mode direct32" % (ip, scale))
        if xs > 16 or ys > 16:
            raise SystemExit("indexed mode: sub-rect %dx%d units > 16 at scale %dx; "
                             "use --mode direct32" % (xs, ys, scale))
        out += bytes((pofs, ip, ((xs - 1) << 4) | (ys - 1)))
    return bytes(out)


def load_banks(afs, entries, name):
    e = next(x for x in entries if x["name"] == name)
    with open(afs, "rb") as f:
        f.seek(e["offset"])
        raw = f.read(e["size"])
    return [unpack_1555(raw[b * 128:(b + 1) * 128], 64, swap_rb=True)
            for b in range(len(raw) // 128)]


def repack(afs, entries, stem, srcdir, mode, outdir):
    man = json.load(open(os.path.join(DEC, stem, "manifest.json")))
    e = next(x for x in entries if x["name"] == man["ppg"])
    banks = None
    if mode in ("indexed", "indexed-hd"):
        sources = {c.get("palette_source") for c in man["chunks"]}
        palette = man.get("palette") or (next(iter(sources)) if len(sources) == 1 else None)
        if not palette:
            raise SystemExit("indexed packing requires an unambiguous external palette source")
        if any((c["pixel_raw"] & 3) != 1 for c in man["chunks"]):
            raise SystemExit("indexed packing supports only 8bpp source textures")
        banks = load_banks(afs, entries, palette)

    with open(afs, "rb") as f:
        raw_chunks = list(walk_chunks(f, e["offset"], e["size"]))
        blobs = {}
        for c in raw_chunks:
            if c["kind"] not in ("pTEX", "pEND") and not c["kind"].startswith("!"):
                f.seek(e["offset"] + c["chunk_offset"])
                blobs[c["chunk_offset"]] = f.read(align4(c["file_size"]))

    scale = None
    stream = bytearray()
    passthrough = []
    n = 0
    for c in raw_chunks:
        if c["kind"] == "pEND":
            break
        if c["kind"] != "pTEX":
            stream += blobs[c["chunk_offset"]]
            continue
        mc = man["chunks"][n]
        n += 1
        if not mc["transNums"]:
            # No trans table means the palette is caller state that the file does
            # not carry, so decode_stages could not resolve it and the PNG is
            # blank. Re-encoding that erases the art -- stage07's three Akane
            # chunks vanished this way. Copy the original chunk through instead.
            f2 = open(afs, "rb")
            f2.seek(e["offset"] + c["chunk_offset"])
            stream += f2.read(align4(c["file_size"]))
            f2.close()
            passthrough.append(mc["n"])
            continue
        p = os.path.join(srcdir, "chunk_%02d.png" % mc["n"])
        if not os.path.isfile(p):
            raise SystemExit("%s: missing %s" % (stem, p))
        w, h, px = read_png(p)
        if w % mc["width"] or h % mc["height"] or (w // mc["width"]) != (h // mc["height"]):
            raise SystemExit("%s chunk %d: %dx%d is not an integer scale of %dx%d"
                             % (stem, mc["n"], w, h, mc["width"], mc["height"]))
        s = w // mc["width"]
        if scale is None:
            scale = s
        elif s != scale:
            raise SystemExit("%s chunk %d: scale %dx, stage is %dx -- one factor per "
                             "stage" % (stem, mc["n"], s, scale))
        if w % 16 or h % 16:
            raise SystemExit("%s chunk %d: %dx%d not a multiple of 16"
                             % (stem, mc["n"], w, h))
        trans = bytes.fromhex(mc["trans"])
        if mode == "direct32":
            # pixel 3 = PSMCT32, | 4 = payload already little-endian
            stream += build_tex(w // 16, h // 16, 3 | 4, 0x8888, b"",
                                to_bgra32(px, w, h))
        elif mode == "indexed-hd":
            if s not in (2, 3, 4) or w // 16 > 255 or h // 16 > 255:
                raise SystemExit("indexed-hd requires 2x, 3x or 4x and dimensions <= 4080")
            # 0x480N marks a logical transfer grid at 1/N texture density.
            # Requires the matching PPGFile.c renderer extension.
            stream += build_tex(w // 16, h // 16, mc["pixel_raw"] & ~0x04,
                                0x4800 | s, trans,
                                quantize(px, w, h, banks, trans, mc["width_units"], s))
        else:
            if s > 2:
                raise SystemExit("indexed mode caps at 2x (got %dx); use "
                                 "--mode direct32" % s)
            nt = scale_trans(trans, mc["width_units"], s)
            stream += build_tex(w // 16, h // 16, mc["pixel_raw"] & ~0x04,
                                int(mc["formARGB"], 16), nt,
                                quantize(px, w, h, banks, nt, w // 16, 1))
        stream += b"\x00" * (align4(len(stream)) - len(stream))
    stream += b"pEND"

    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, man["ppg"])
    open(out, "wb").write(stream)
    if passthrough:
        print("%-9s kept %d chunk(s) at 1x (no trans table, palette is caller "
              "state): %s" % (stem, len(passthrough), passthrough))
    return out, scale, len(stream), e["size"], n


def is_stage(manp):
    """True for a stage manifest; character manifests carry "frames" instead."""
    if not os.path.isfile(manp):
        return False
    return "chunks" in json.load(open(manp))


def verify(path, expect):
    """Re-walk the produced stream; confirm it terminates exactly at EOF."""
    data = open(path, "rb").read()
    f = io.BytesIO(data)
    got = [c for c in walk_chunks(f, 0, len(data))]
    tex = [c for c in got if c["kind"] == "pTEX"]
    end = [c for c in got if c["kind"] == "pEND"]
    tail = len(data) - (end[0]["chunk_offset"] + 4) if end else -1
    return len(tex), bool(end), tail, len(tex) == expect and end and tail == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("afs")
    ap.add_argument("--stages", help="comma-separated stems (default: all)")
    ap.add_argument("--src", help="source PNG root (default work/tiles, falling "
                                  "back to work/upscaled then work/decoded)")
    ap.add_argument("--mode", choices=("direct32", "indexed", "indexed-hd"), default="direct32")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    entries, _ = parse_index(args.afs)
    stems = (args.stages.split(",") if args.stages
             else sorted(d for d in os.listdir(DEC)
                         if is_stage(os.path.join(DEC, d, "manifest.json"))))
    print("%-9s %-6s %5s %11s %11s %6s %s"
          % ("stage", "mode", "scale", "old bytes", "new bytes", "chunks", "stream"))
    for stem in stems:
        src = os.path.join(src_root(stem, args.src), stem)
        out, scale, new, old, n = repack(args.afs, entries, stem, src, args.mode,
                                         args.out)
        ntex, has_end, tail, ok = verify(out, n)
        print("%-9s %-6s %4dx %11d %11d %6d %s"
              % (stem, args.mode, scale, old, new, n,
                 "OK" if ok else "BAD (tex=%d end=%s tail=%d)" % (ntex, has_end, tail)))


if __name__ == "__main__":
    main()
