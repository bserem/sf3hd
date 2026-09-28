#!/usr/bin/env python3
"""cg_decode_all - decode EVERY animation frame of one character.

Writes work/decoded/<cg>/frame_NNN.png plus a manifest.json recording, per frame,
each piece's rectangle in frame-local coordinates. The manifest is what lets a
repack re-cut tiles out of an upscaled frame.

Frames are the right upscale unit rather than tiles: the atlas has zero padding, so
an isolated tile gives the upscaler no edge context, while 88% of tiles are
referenced exactly once (mean 1.26 refs) so re-cutting costs only ~26% more tile
entries than the stock texture table.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_decode_test import write_png                    # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402
from cg_decode_tile import lz_ext_p6_fx, unswizzle, WH_PIXELS, TEXGROUP  # noqa: E402

DEC = os.path.join("work", "decoded")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("afs")
    ap.add_argument("--cg", required=True, choices=sorted(TEXGROUP),
                    help="character archive entry to decode")
    ap.add_argument("--flip", type=int, default=0)
    args = ap.parse_args()

    cfg = TEXGROUP[args.cg]
    entries, _ = parse_index(args.afs)
    byname = {e["name"]: e for e in entries}
    with open(args.afs, "rb") as f:
        e = byname[args.cg]
        f.seek(e["offset"])
        d = f.read(e["size"])
        pe = byname[cfg["pal"]]
        f.seek(pe["offset"])
        raw = f.read(pe["size"])
    banks = [unpack_1555(raw[b * 128:(b + 1) * 128], 64, swap_rb=False)
             for b in range(len(raw) // 128)]
    totex = cfg["to_tex"]
    nlists = struct.unpack_from("<I", d, 0)[0] // 4
    toff = struct.unpack_from("<%dI" % nlists, d, 0)

    cache = {}

    def tile(code):
        if code not in cache:
            tp = totex + struct.unpack_from("<I", d, totex + code * 4)[0]
            whb = d[tp]
            wh = (whb & 3) + 1
            if wh not in WH_PIXELS:
                cache[code] = None
            else:
                side = WH_PIXELS[wh]
                cache[code] = (min((whb & 0xE0) >> 2, side),
                               min((whb & 0x1C) * 2, side), side,
                               unswizzle(lz_ext_p6_fx(d[tp + 1:], (wh * wh) << 6)[0],
                                         side))
        return cache[code]

    stem = args.cg.split(".")[0]
    out = os.path.join(DEC, stem)
    os.makedirs(out, exist_ok=True)
    man = {"cg": args.cg, "cg_entry": e["index"], "palette": cfg["pal"],
           "palette_entry": pe["index"], "to_tex": totex, "flip": args.flip,
           "frames": []}
    notes, ok, skipped, placeholder = [], 0, 0, 0

    for li in range(nlists):
        base = toff[li]
        count = struct.unpack_from("<H", d, base)[0]
        if count == 0 or count > 200:
            skipped += 1
            continue
        pieces, X, Y, bad = [], 0, 0, False
        for k in range(count):
            px, py, attr, code = struct.unpack_from("<hhHH", d, base + 2 + k * 8)
            X = X + px if (args.flip & 1) else X - px
            Y = Y + py if (args.flip & 2) else Y - py
            t = tile(code)
            if t is None:
                notes.append("frame %d piece %d: unsupported tile %d" % (li, k, code))
                bad = True
                break
            dw, dh, side, pix = t
            pieces.append([X, Y, dw, dh, code, attr & 3, side, pix, attr])
        if bad:
            continue
        if all(p[4] == 0 for p in pieces):
            # 214 lists reference ONLY tile code 0, using it as a null. They are
            # placeholder slots, not animation frames: there are just 22 distinct
            # piece-lists among all 214, and each renders as the same tile tiled
            # over and over. NOTE tile 0 is NOT blank -- it is 745/768 opaque px of
            # real art -- so these render as a repeated fragment if you keep them.
            placeholder += 1
            continue
        x0 = min(p[0] for p in pieces)
        y0 = min(p[1] for p in pieces)
        W = max(p[0] + p[2] for p in pieces) - x0
        H = max(p[1] + p[3] for p in pieces) - y0
        buf = bytearray(W * H * 4)
        drawn = 0
        for X, Y, dw, dh, code, pal, side, pix, pattr in pieces:
            bank = banks[pal]
            # Per-piece mirroring: seqsStoreChip gets palo | ((piece_attr ^ obj_attr)
            # & 0xC000) (mtrans.c:492) and swaps s/t on those bits
            # (mtrans.c:1638-1650), mirroring the tile content in place. 0x8000 = h,
            # 0x4000 = v. obj_attr is flptbl[...] = 0 for the unflipped case.
            fx = bool((pattr ^ (0x8000 if args.flip & 1 else 0)) & 0x8000)
            fy = bool((pattr ^ (0x4000 if args.flip & 2 else 0)) & 0x4000)
            for yy in range(dh):
                for xx in range(dw):
                    sx = dw - 1 - xx if fx else xx
                    sy = dh - 1 - yy if fy else yy
                    c = bank[pix[sy * side + sx]]
                    if not c[3]:
                        continue
                    o = ((Y - y0 + yy) * W + (X - x0 + xx)) * 4
                    buf[o:o + 4] = bytes(c)
                    drawn += 1
        write_png(os.path.join(out, "frame_%03d.png" % li), W, H, buf)
        man["frames"].append({
            # origin = accumulated coords of the bbox corner, BEFORE normalisation.
            # Frames can share identical pixels but differ only in anchor (17 such
            # pairs in pl02), so this must round-trip or sprites shift on repack.
            "n": li, "w": W, "h": H, "drawn": drawn, "ox": x0, "oy": y0,
            "pieces": [{"code": p[4], "pal": p[5], "x": p[0] - x0, "y": p[1] - y0,
                        "dw": p[2], "dh": p[3], "slot": p[6], "attr": p[8]}
                       for p in pieces],
        })
        ok += 1

    with open(os.path.join(out, "manifest.json"), "w") as mf:
        json.dump(man, mf)
    print("%s: %d frames written, %d placeholder lists (code 0 only) excluded, "
          "%d empty/oversized, %d notes"
          % (stem, ok, placeholder, skipped, len(notes)))
    for n in notes[:10]:
        print("  ! " + n)
    ws = [fr["w"] for fr in man["frames"]]
    hs = [fr["h"] for fr in man["frames"]]
    print("frame size range: %dx%d .. %dx%d" % (min(ws), min(hs), max(ws), max(hs)))


if __name__ == "__main__":
    main()
