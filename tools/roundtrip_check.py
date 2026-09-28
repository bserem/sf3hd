#!/usr/bin/env python3
"""roundtrip_check - decode a repacked .ppg and compare it to the source PNGs.

Isolates repack/rebuild bugs from upscaler artifacts: run ppg_repack at 1x from
work/decoded, then check every chunk comes back pixel-identical.
"""
import argparse
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_inspect import walk_chunks                      # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402
from verify_decoded import read_png                      # noqa: E402

DEC = os.path.join("work", "decoded")


def render(f, c, base, banks):
    W, H, uw = c["width"], c["height"], c["width_units"]
    f.seek(base + 16)
    trans = f.read(c["transNums"] * 3)
    f.seek(base + c["data_offset"])
    payload = f.read(c["file_size"] - c["data_offset"])
    raw = zlib.decompress(payload) if (c["compress_raw"] & 3) == 2 else payload
    mode = c["pixel_raw"] & 3
    if mode == 3:
        # payload is BGRA (see to_bgra32 in ppg_repack.py); swap back for compare
        b = bytearray(raw)
        b[0::4], b[2::4] = raw[2::4], raw[0::4]
        return W, H, bytes(b)
    if mode != 1:
        raise SystemExit("unhandled pixel mode %d" % mode)
    out = bytearray(W * H * 4)
    for k in range(c["transNums"]):
        pofs, ip, cxy = trans[k * 3], trans[k * 3 + 1], trans[k * 3 + 2]
        sx, sy = ip % uw, ip // uw
        xs, ys = (cxy >> 4) + 1, (cxy & 0xF) + 1
        bank = banks[pofs]
        for y in range(sy * 16, min((sy + ys) * 16, H)):
            for x in range(sx * 16, min((sx + xs) * 16, W)):
                o = (y * W + x) * 4
                out[o:o + 4] = bytes(bank[raw[y * W + x]])
    return W, H, bytes(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("afs")
    ap.add_argument("ppg", help="repacked .ppg file")
    ap.add_argument("stem")
    ap.add_argument("--ref", help="reference PNG dir (default work/decoded/<stem>)")
    args = ap.parse_args()

    man = json.load(open(os.path.join(DEC, args.stem, "manifest.json")))
    entries, _ = parse_index(args.afs)
    pe = next(x for x in entries if x["name"] == man["palette"])
    with open(args.afs, "rb") as f:
        f.seek(pe["offset"])
        rawpal = f.read(pe["size"])
    banks = [unpack_1555(rawpal[b * 128:(b + 1) * 128], 64, swap_rb=True)
             for b in range(len(rawpal) // 128)]

    ref = args.ref or os.path.join(DEC, args.stem)
    size = os.path.getsize(args.ppg)
    bad = same = 0
    with open(args.ppg, "rb") as f:
        n = 0
        for c in walk_chunks(f, 0, size):
            if c["kind"] == "pEND":
                break
            if c["kind"] != "pTEX":
                continue
            W, H, px = render(f, c, c["chunk_offset"], banks)
            rw, rh, rp = read_png(os.path.join(ref, "chunk_%02d.png" % n))
            n += 1
            if (rw, rh) != (W, H):
                print("chunk %d: %dx%d vs ref %dx%d" % (n - 1, W, H, rw, rh))
                bad += 1
                continue
            # compare RGB on drawn pixels and the drawn/undrawn mask itself
            diff = sum(1 for i in range(0, len(px), 4)
                       if (bool(px[i + 3]) != bool(rp[i + 3])
                           or (px[i + 3] and px[i:i + 3] != rp[i:i + 3])))
            if diff:
                print("chunk %d: %d/%d pixels differ" % (n - 1, diff, W * H))
                bad += 1
            else:
                same += 1
    print("\n%s: %d chunks identical, %d differ" % (args.stem, same, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
