#!/usr/bin/env python3
"""cg_decode_frame - composite ONE character animation frame from its piece list.

One `cg_number` selects exactly one piece list (mtrans.c:400-412,
`n = wk->cg_number`), so a piece list IS a whole frame - there is no multi-list
compositing the way stages have BG layers.

Piece offsets are **cumulative deltas**, not absolute positions (mtrans.c:457-467):

    x = y = 0.0f;
    while (count--) {
        if (attr & 0x8000) { x += trsptr->x; } else { x -= trsptr->x; }
        if (attr & 0x4000) { y -= trsptr->y; } else { y += trsptr->y; }

so unflipped, x accumulates NEGATED and y accumulates positive.

Each tile is stored in a square slot of `wh*8` px but only its top-left
`dw x dh` is real (mtrans.c:470-471):

    dw = (texptr->wh & 0xE0) >> 2;
    dh = (texptr->wh & 0x1C) * 2;

verified: across 300 tiles, 0 of 54080 pixels outside dw x dh are non-transparent.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_decode_test import write_png                    # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402
from cg_decode_tile import lz_ext_p6_fx, unswizzle, WH_PIXELS, TEXGROUP  # noqa: E402

OUT_DIR = os.path.join("work", "upscaled_test")


def load(afs, cg):
    entries, _ = parse_index(afs)
    byname = {e["name"]: e for e in entries}
    cfg = TEXGROUP[cg]
    with open(afs, "rb") as f:
        e = byname[cg]
        f.seek(e["offset"])
        d = f.read(e["size"])
        pe = byname[cfg["pal"]]
        f.seek(pe["offset"])
        raw = f.read(pe["size"])
    # swap_rb=False: character palettes are ARGB1555, unlike bgNN0.bin. See below.
    banks = [unpack_1555(raw[b * 128:(b + 1) * 128], 64, swap_rb=False)
             for b in range(len(raw) // 128)]
    return d, banks, cfg["to_tex"]


def frame(d, totex, li, flip=0):
    base = struct.unpack_from("<%dI" % (struct.unpack_from("<I", d, 0)[0] // 4), d, 0)[li]
    count = struct.unpack_from("<H", d, base)[0]
    out, X, Y = [], 0, 0
    for k in range(count):
        px, py, attr, code = struct.unpack_from("<hhHH", d, base + 2 + k * 8)
        X = X + px if (flip & 1) else X - px
        Y = Y + py if (flip & 2) else Y - py
        tp = totex + struct.unpack_from("<I", d, totex + code * 4)[0]
        whb = d[tp]
        wh = (whb & 3) + 1
        if wh not in WH_PIXELS:
            raise SystemExit("list %d piece %d: unsupported wh=%d" % (li, k, wh))
        side = WH_PIXELS[wh]
        dw, dh = min((whb & 0xE0) >> 2, side), min((whb & 0x1C) * 2, side)
        pix = unswizzle(lz_ext_p6_fx(d[tp + 1:], (wh * wh) << 6)[0], side)
        # Both accumulators run negated relative to a top-down raster, and the
        # accumulated y is the tile's TOP edge here. In game coordinates
        # seqsStoreChip treats y as the bottom (v[0].y = y, v[1].y = y - h,
        # mtrans.c:1606-1610), but mlt_obj_matrix transforms before rasterizing and
        # characters are positioned from the ground up, so the axis is inverted.
        # Established empirically: searching {field order, x sign, y sign, y edge,
        # dw/dh order} over frames 1/2/300/700, this is the only combination that
        # yields a SINGLE connected component on all four (100%); the next best
        # leaves 4-24% of pixels detached.
        out.append((X, Y, dw, dh, side, pix, attr & 3, attr))
    return out


def render(pieces, banks, path):
    x0 = min(p[0] for p in pieces)
    y0 = min(p[1] for p in pieces)
    W = max(p[0] + p[2] for p in pieces) - x0
    H = max(p[1] + p[3] for p in pieces) - y0
    buf = bytearray(W * H * 4)
    drawn = 0
    for X, Y, dw, dh, side, pix, pal, pattr in pieces:
        bank = banks[pal]
        eff = pattr & 0xC000          # per-piece mirror, mtrans.c:484-493
        for yy in range(dh):
            sy = dh - 1 - yy if (eff & 0x4000) else yy
            for xx in range(dw):
                sx = dw - 1 - xx if (eff & 0x8000) else xx
                c = bank[pix[sy * side + sx]]
                if not c[3]:
                    continue
                o = ((Y - y0 + yy) * W + (X - x0 + xx)) * 4
                buf[o:o + 4] = bytes(c)
                drawn += 1
    write_png(path, W, H, buf)
    return W, H, drawn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("afs")
    ap.add_argument("--cg", default="pl02.bin")
    ap.add_argument("--list", type=int, nargs="+", default=[1])
    ap.add_argument("--flip", type=int, default=0, help="1=h, 2=v, 3=both")
    args = ap.parse_args()

    d, banks, totex = load(args.afs, args.cg)
    os.makedirs(OUT_DIR, exist_ok=True)
    for li in args.list:
        p = frame(d, totex, li, args.flip)
        stem = args.cg.split(".")[0]
        path = os.path.join(OUT_DIR, "12_%s_frame%03d.png" % (stem, li))
        W, H, drawn = render(p, banks, path)
        print("list %-4d %2d pieces -> %s  %dx%d  %d drawn px (%.0f%% of bbox)"
              % (li, len(p), os.path.basename(path), W, H, drawn,
                 100.0 * drawn / (W * H)))


if __name__ == "__main__":
    main()
