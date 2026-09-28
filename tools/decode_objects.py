#!/usr/bin/env python3
"""decode_objects - decode object/effect texture groups (efNN.bin) to frames.

These groups hold the screen furniture that is neither a fighter nor a stage:
the player select portraits and face icons, the "PLAYER SELECT" lettering, and
the various effects. They use the same CG container as plNN.bin, with two
differences that this tool handles:

  * No `to_chd`. That field is character-only (fighter init data). Sprite
    composition lives at file offset 0 for every group -- texgroup.c:141-142
    sets trans_table = ldadr, texture_table = ldadr + to_tex -- and a pattern
    resolves as (mtrans.c:181-184):

        n      = pattern - texgrpdat[group].num_of_1st
        trsbas = trans_table + ((u32*)trans_table)[n]
        count  = *trsbas ; pieces follow

  * No plNNpl.bin palette. The banks live in ColorRAM, filled from default.bin:
    color_file[20] (color3rd.c:566) is a type-2 load placing 480 banks of 64
    colours at ColorRAM bank 0x20. On disk these are ARGB1555:
    palCreateGhost defines source R at bit 10 and B at bit 0. The engine
    converts to its internal format; decoding file bytes must not swap R/B.

Cache 13 has mode 0x2012: mode 18 dispatches to mlt_obj_trans_cp3, and
its ext flag selects mlt_obj_trans_cp3_ext (not mlt_obj_trans_ext).
That function adds (piece.attr & 0x1FF) to the object's colcd; seqsStoreChip
masks the result to nine bits. Select portraits use my_col_code = 0x90
(eff38.c); other objects can override the base with --bank.

Output: work/decoded/<stem>/ (gitignored). Derived pixels stay local.
"""
import argparse
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from cg_decode_tile import WH_PIXELS, lz_ext_p6_fx, unswizzle   # noqa: E402
from ppg_decode_test import write_png                    # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402
from decode_screens import contact_sheet, SHEET_ROOT     # noqa: E402

DEC = os.path.join("work", "decoded")

COLORRAM_FILE = "default.bin"
COLORRAM_BASE = 32          # color_file[20].data, init_trans_color_ram case 2

# texgrpdat[] in texgroup_data.c. num_of_1st is the first pattern number of the
# group; to_tex is where its texture table starts.
OBJGROUP = {
    "ef02.bin":     {"to_tex": 0x20B9C, "num_of_1st": 27104, "grp": 98,
                     "bank": 144, "note": "player select objects (Japanese)"},
    "ef02_usa.bin": {"to_tex": 0x20CA4, "num_of_1st": 27104, "grp": 23,
                     "bank": 144, "note": "player select objects (non-Japanese)"},
}


def colorram(afs, entries):
    """ColorRAM banks 32..511 as loaded from default.bin."""
    e = entries[COLORRAM_FILE]
    with open(afs, "rb") as f:
        f.seek(e["offset"])
        raw = f.read(e["size"])
    banks = [unpack_1555(raw[b * 128:(b + 1) * 128], 64, swap_rb=False)
             for b in range(len(raw) // 128)]
    return {COLORRAM_BASE + i: b for i, b in enumerate(banks)}, e


def piece_palette(base_bank, attr, piece_bank=True):
    """mlt_obj_trans_cp3_ext palt, then seqsStoreChip's ColorRAM mask."""
    return (base_bank + (attr & 0x1FF if piece_bank else 0)) & 0x1FF


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("afs")
    ap.add_argument("--group", required=True, choices=sorted(OBJGROUP))
    ap.add_argument("--bank", type=lambda value: int(value, 0),
                    help="ColorRAM base bank (default: the group's col_code)")
    palette_mode = ap.add_mutually_exclusive_group()
    palette_mode.add_argument("--piece-bank", dest="piece_bank", action="store_true",
                              help="add the piece's 9-bit palette offset (default)")
    palette_mode.add_argument("--flat-bank", dest="piece_bank", action="store_false",
                              help="diagnostic: use one bank for every piece")
    ap.set_defaults(piece_bank=True)
    ap.add_argument("--start", type=int, default=0, help="first pattern to decode")
    ap.add_argument("--limit", type=int, help="decode only the first N patterns")
    ap.add_argument("--sheet-count", type=int, default=96,
                    help="frames sampled into the contact sheet (0 = none)")
    args = ap.parse_args()

    cfg = OBJGROUP[args.group]
    base_bank = args.bank if args.bank is not None else cfg["bank"]
    entries, _ = parse_index(args.afs)
    byname = {e["name"]: e for e in entries if e["name"]}
    if COLORRAM_FILE not in byname:
        sys.exit("%s not in the archive; cannot resolve object palettes" % COLORRAM_FILE)
    ram, pal_entry = colorram(args.afs, byname)

    e = byname[args.group]
    with open(args.afs, "rb") as f:
        f.seek(e["offset"])
        d = f.read(e["size"])

    totex = cfg["to_tex"]
    npat = struct.unpack_from("<I", d, 0)[0] // 4
    toff = struct.unpack_from("<%dI" % npat, d, 0)
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

    stem = args.group.split(".")[0]
    out = os.path.join(DEC, stem)
    os.makedirs(out, exist_ok=True)
    man = {"kind": "object", "cg": args.group, "cg_entry": e["index"],
           "palette": COLORRAM_FILE, "palette_entry": pal_entry["index"],
           "colorram_base": COLORRAM_BASE, "base_bank": base_bank,
           "to_tex": totex, "num_of_1st": cfg["num_of_1st"],
           "group": cfg["grp"], "palette_format": "ARGB1555",
           "palette_rule": "cp3-ext-9bit" if args.piece_bank else "flat", "frames": []}
    notes, skipped, missing_bank = [], 0, set()
    tiles_out = []

    for li in range(args.start, min(npat, args.start + args.limit)
                    if args.limit is not None else npat):
        pbase = toff[li]
        count = struct.unpack_from("<H", d, pbase)[0]
        if count == 0 or count > 200:
            skipped += 1
            continue
        pieces, X, Y, bad = [], 0, 0, False
        for k in range(count):
            px, py, attr, code = struct.unpack_from("<hhHH", d, pbase + 2 + k * 8)
            X -= px
            Y -= py
            t = tile(code)
            if t is None:
                notes.append("pattern %d piece %d: unsupported tile %d" % (li, k, code))
                bad = True
                break
            dw, dh, side, pix = t
            pieces.append([X, Y, dw, dh, code, attr, side, pix])
        if bad or not pieces:
            continue
        x0 = min(p[0] for p in pieces)
        y0 = min(p[1] for p in pieces)
        W = max(p[0] + p[2] for p in pieces) - x0
        H = max(p[1] + p[3] for p in pieces) - y0
        if W <= 0 or H <= 0 or W > 2048 or H > 2048:
            skipped += 1
            continue
        buf = bytearray(W * H * 4)
        drawn = 0
        for X, Y, dw, dh, code, attr, side, pix in pieces:
            # Mode 18 + ext uses the full 9-bit per-piece palette offset.
            bank_no = piece_palette(base_bank, attr, args.piece_bank)
            bank = ram.get(bank_no)
            if bank is None:
                missing_bank.add(bank_no)
                continue
            fx = bool(attr & 0x8000)
            fy = bool(attr & 0x4000)
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
        if not drawn:
            skipped += 1
            continue
        write_png(os.path.join(out, "frame_%03d.png" % li), W, H, buf)
        man["frames"].append({
            "n": li, "w": W, "h": H, "drawn": drawn, "ox": x0, "oy": y0,
            # pieces entries are [X, Y, dw, dh, code, attr, side, pix]
            "pieces": [{"code": p[4],
                        "pal": piece_palette(base_bank, p[5], args.piece_bank),
                        "x": p[0] - x0, "y": p[1] - y0, "dw": p[2], "dh": p[3],
                        "slot": p[6], "attr": p[5]} for p in pieces],
        })
        tiles_out.append((li, W, H, bytes(buf)))

    with open(os.path.join(out, "manifest.json"), "w") as mf:
        json.dump(man, mf)

    n = len(man["frames"])
    print("%s: %d of %d patterns decoded, %d skipped (empty/oversized), %d notes"
          % (stem, n, npat, skipped, len(notes)))
    if missing_bank:
        print("  ! ColorRAM banks outside default.bin: %s"
              % sorted(missing_bank)[:8])
    for note in notes[:5]:
        print("  ! " + note)
    if n:
        ws = [fr["w"] for fr in man["frames"]]
        hs = [fr["h"] for fr in man["frames"]]
        print("frame size range: %dx%d .. %dx%d" % (min(ws), min(hs), max(ws), max(hs)))
        if args.sheet_count:
            step = max(1, len(tiles_out) // args.sheet_count)
            sample = tiles_out[::step][:args.sheet_count]
            path = os.path.join(SHEET_ROOT, stem + ".png")
            contact_sheet(path, sample)
            print("contact sheet: %s (%d of %d frames)" % (path, len(sample), n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
