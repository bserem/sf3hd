#!/usr/bin/env python3
"""cg_roundtrip_check - decode a repacked plNN.bin and compare it to the source PNGs.

The character equivalent of roundtrip_check.py. Rebuilds every frame in the
manifest out of the repacked file exactly the way cg_decode_all built it from
the AFS - same piece order, same per-piece flip, same palette bank - and diffs
it against the PNG the repack was fed.

A clean result means the whole character chain is reversible: cut, un-flip,
quantise, re-swizzle, LZ-encode, table rebuild.
"""
import argparse
import json
import os
import struct
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402
from cg_decode_tile import TEXGROUP, WH_PIXELS, lz_ext_p6_fx, unswizzle  # noqa: E402

DEC = os.path.join("work", "decoded")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("afs", help="only used for the palette")
    ap.add_argument("repacked", help="rebuilt plNN.bin")
    ap.add_argument("--cg", default="pl02.bin")
    ap.add_argument("--ref", help="frame PNG dir (default work/decoded/<stem>)")
    ap.add_argument("--to-tex", type=lambda v: int(v, 0),
                    help="override to_tex (default: the texgrpdat value)")
    args = ap.parse_args()

    stem = args.cg.split(".")[0]
    cfg = TEXGROUP[args.cg]
    ref = args.ref or os.path.join(DEC, stem)
    man = json.load(open(os.path.join(DEC, stem, "manifest.json")))

    entries, _ = parse_index(args.afs)
    byname = {e["name"]: e for e in entries}
    with open(args.afs, "rb") as f:
        pe = byname[cfg["pal"]]
        f.seek(pe["offset"])
        rawpal = f.read(pe["size"])
    banks = [unpack_1555(rawpal[b * 128:(b + 1) * 128], 64, swap_rb=False)
             for b in range(len(rawpal) // 128)]

    d = open(args.repacked, "rb").read()
    to_tex = args.to_tex if args.to_tex is not None else cfg["to_tex"]
    ntex = struct.unpack_from("<I", d, to_tex)[0] // 4
    tofs = struct.unpack_from("<%dI" % ntex, d, to_tex)

    cache = {}

    def tile(code):
        if code not in cache:
            tp = to_tex + tofs[code]
            whb = d[tp]
            wh = (whb & 3) + 1
            if wh not in WH_PIXELS:
                cache[code] = None
            else:
                side = WH_PIXELS[wh]
                raw, _ = lz_ext_p6_fx(d[tp + 1:], (wh * wh) << 6)
                cache[code] = (side, unswizzle(raw, side))
        return cache[code]

    same = diff = missing = 0
    worst = []
    for fr in man["frames"]:
        p = os.path.join(ref, "frame_%03d.png" % fr["n"])
        if not os.path.isfile(p):
            missing += 1
            continue
        W, H = fr["w"], fr["h"]
        buf = bytearray(W * H * 4)
        for pc in fr["pieces"]:
            t = tile(pc["code"])
            if t is None:
                continue
            side, pix = t
            bank = banks[pc["pal"]]
            fx = bool(pc["attr"] & 0x8000)
            fy = bool(pc["attr"] & 0x4000)
            dw, dh = pc["dw"], pc["dh"]
            for yy in range(dh):
                oy = pc["y"] + yy
                if oy >= H:
                    continue
                sy = dh - 1 - yy if fy else yy
                for xx in range(dw):
                    ox = pc["x"] + xx
                    if ox >= W:
                        continue
                    sx = dw - 1 - xx if fx else xx
                    c = bank[pix[sy * side + sx]]
                    if not c[3]:
                        continue
                    o = (oy * W + ox) * 4
                    buf[o:o + 4] = bytes(c)
        want = Image.open(p).convert("RGBA").tobytes()
        if bytes(buf) == want:
            same += 1
        else:
            diff += 1
            n = sum(1 for k in range(0, len(want), 4) if buf[k:k + 4] != want[k:k + 4])
            worst.append((n, fr["n"], W * H))

    print("%s: %d frames identical, %d differ, %d missing"
          % (stem, same, diff, missing))
    for n, fno, tot in sorted(worst, reverse=True)[:8]:
        print("  frame %3d: %d/%d px differ (%.2f%%)" % (fno, n, tot, 100.0 * n / tot))
    return 1 if diff else 0


if __name__ == "__main__":
    sys.exit(main())
