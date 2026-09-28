#!/usr/bin/env python3
"""cg_decode_tile - decode ONE character CG tile to a PNG.

Addressing, all from 3sx:
  texgrpdat[3] = { apfn 1468 (pl02.bin), to_tex 0x11C7C, to_chd 0x178F38 }
                                            texgroup_data.c:22-25
  trans_table = file base, texture_table = base + to_tex  texgroup.c:140-141
  n = *(u32*)trans_table / 4                              mtrans.c:2170
  piece list i at trans_table[((u32*)trans_table)[i]]:
      u16 count, then TileMapEntry{s16 x, s16 y, u16 attr, u16 code}
                                                          structs.h:1582-1587
  texptr = texture_table + ((u32*)texture_table)[code]    mtrans.c:2185
  TEX { u8 wh; u8 dat[]; }                                structs.h:1154-1157
  wh = (texptr->wh & 3) + 1;  size = (wh*wh) << 6         mtrans.c:2187-2188
  pixels = lz_ext_p6_fx(texptr + 1, size)                 mtrans.c:1935-1983
  palt = attr & 3                                         mtrans.c:2191

Every index the LZ emits is 0-63 (literals are masked to 0x3F, and the nibble
opcode ORs a 2-bit bank flag into 4 bits), i.e. exactly one 64-colour CPS3 bank.
With Player_Color = 0 and player id 0, init_trans_color_ram type 1
(color3rd.c:180-206) puts col[0][0] in ColorRAM[0], so palt 0 is the default
costume bank = the first 64 u16 of plNNpl.bin.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_decode_test import write_png                    # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402

OUT_DIR = os.path.join("work", "upscaled_test")

# texgrpdat[], texgroup_data.c. Only the characters we have looked at.
# to_tex/to_chd are compiled into the engine, not read from the file, so a
# repack that changes either one needs texgroup_data.c updated to match.
TEXGROUP = {
    "pl00.bin": {"to_tex": 0x33784, "to_chd": 0x2CF158, "pal": "pl00pl.bin", "grp": 1},
    "pl01.bin": {"to_tex": 0x1C6D0, "to_chd": 0x24EDE0, "pal": "pl01pl.bin", "grp": 2},
    "pl02.bin": {"to_tex": 0x11C7C, "to_chd": 0x178F38, "pal": "pl02pl.bin", "grp": 3},
    "pl03.bin": {"to_tex": 0x1C080, "to_chd": 0x1FAA0C, "pal": "pl03pl.bin", "grp": 4},
    "pl04.bin": {"to_tex": 0x1B088, "to_chd": 0x1E1D54, "pal": "pl04pl.bin", "grp": 5},
    "pl05.bin": {"to_tex": 0x1C79C, "to_chd": 0x2BCE28, "pal": "pl05pl.bin", "grp": 6},
    "pl06.bin": {"to_tex": 0x26AC0, "to_chd": 0x31392C, "pal": "pl06pl.bin", "grp": 7},
    "pl07.bin": {"to_tex": 0x250C0, "to_chd": 0x25D908, "pal": "pl07pl.bin", "grp": 8},
    "pl08.bin": {"to_tex": 0x22BD4, "to_chd": 0x1A0CCC, "pal": "pl08pl.bin", "grp": 9},
    "pl09.bin": {"to_tex": 0x219D0, "to_chd": 0x205694, "pal": "pl09pl.bin", "grp": 10},
    "pl10.bin": {"to_tex": 0x1C89C, "to_chd": 0x216E64, "pal": "pl10pl.bin", "grp": 11},
    "pl11.bin": {"to_tex": 0x118DC, "to_chd": 0x17C0F8, "pal": "pl11pl.bin", "grp": 12},
    "pl12.bin": {"to_tex": 0x13AD4, "to_chd": 0x18B378, "pal": "pl12pl.bin", "grp": 13},
    "pl13.bin": {"to_tex": 0x21104, "to_chd": 0x215C68, "pal": "pl13pl.bin", "grp": 14},
    "pl14.bin": {"to_tex": 0x1C594, "to_chd": 0x26DFC8, "pal": "pl14pl.bin", "grp": 15},
    "pl16.bin": {"to_tex": 0x234C8, "to_chd": 0x26C73C, "pal": "pl16pl.bin", "grp": 16},
    "pl17.bin": {"to_tex": 0x2B63C, "to_chd": 0x2837C4, "pal": "pl17pl.bin", "grp": 17},
    "pl18.bin": {"to_tex": 0x363AC, "to_chd": 0x37F0B4, "pal": "pl18pl.bin", "grp": 18},
    "pl19.bin": {"to_tex": 0x20114, "to_chd": 0x21B47C, "pal": "pl19pl.bin", "grp": 19},
    "pl20.bin": {"to_tex": 0x1E9EC, "to_chd": 0x1A4F50, "pal": "pl20pl.bin", "grp": 20},
}

# side length in pixels for each `wh` value; size = (wh*wh) << 6
WH_PIXELS = {1: 8, 2: 16, 4: 32}

# ppgMakeConvTableTexDC, PPGFile.c:864-887. Despite living behind an
# `if (tch->be != 0)` guard that mlt_obj_trans_init appears to leave clear, this
# table IS applied to character CG: decoding without it yields a byte stream with
# no 2-D structure at any stride, and applying it cuts the transparency-mask
# perimeter by 69% across 399 tiles. See docs/format-notes.md.
_SEED = [0x0000, 0x0002, 0x0008, 0x000A, 0x0020, 0x0022, 0x0028, 0x002A,
         0x0080, 0x0082, 0x0088, 0x008A, 0x00A0, 0x00A2, 0x00A8, 0x00AA,
         0x0200, 0x0202, 0x0208, 0x020A, 0x0220, 0x0222, 0x0228, 0x022A,
         0x0280, 0x0282, 0x0288, 0x028A, 0x02A0, 0x02A2, 0x02A8, 0x02AA]
_SEED_ADD = [0x0000, 0x0004, 0x0010, 0x0014, 0x0040, 0x0044, 0x0050, 0x0054,
             0x0100, 0x0104, 0x0110, 0x0114, 0x0140, 0x0144, 0x0150, 0x0154]


def _make_conv_table():
    t = [0] * 1024
    for i in range(16):
        for j in range(32):
            t[j + i * 64] = _SEED[j] + _SEED_ADD[i]
        for j in range(32):
            t[j + i * 64 + 32] = t[j + i * 64] + 1
    return t


DCTEX_LINEAR = _make_conv_table()


def unswizzle(pix, side):
    """Morton -> linear. PPGFile.c:772-816 (8x8, 16x16) and :801-816 (32x32)."""
    out = bytearray(side * side)
    if side == 32:
        for k in range(1024):
            out[k] = pix[DCTEX_LINEAR[k]]
    else:
        for i in range(side):
            for j in range(side):
                out[i * side + j] = pix[DCTEX_LINEAR[j + (i << 5)]]
    return bytes(out)


def lz_ext_p6_fx(src, length):
    """mtrans.c:1935-1983, verbatim."""
    dst = bytearray()
    i = 0
    while len(dst) < length:
        tmp = src[i]
        i += 1
        op = tmp & 0xC0
        if op == 0x00:
            dst.append(tmp)
        elif op == 0x40:
            tmp &= 0x3F
            p = len(dst) - (tmp >> 2) - 1
            for _ in range((tmp & 3) + 2):
                dst.append(dst[p])
                p += 1
        elif op == 0x80:
            tmp = ((tmp & 0x3F) << 8) | src[i]
            i += 1
            p = len(dst) - (tmp >> 6) - 1
            for _ in range((tmp & 0x3F) + 2):
                dst.append(dst[p])
                p += 1
        else:
            flg = tmp & 0x30
            for _ in range((tmp & 0xF) + 2):
                dst.append(flg | (src[i] >> 4))
                dst.append(flg | (src[i] & 0xF))
                i += 1
    return bytes(dst), i


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("afs")
    ap.add_argument("--cg", default="pl02.bin")
    ap.add_argument("--list", type=int, default=1, help="piece list index")
    ap.add_argument("--piece", type=int, default=0, help="piece within the list")
    ap.add_argument("--all-pieces", action="store_true",
                    help="dump every piece of the list")
    ap.add_argument("--bank", type=int, default=None,
                    help="override palette bank (default: piece attr & 3)")
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
        rawpal = f.read(pe["size"])

    # palette: COL.col[0][bank][64], u16 LE, ABGR1555 (palConvSrcToRam)
    # NOTE swap_rb=False: character palettes are ARGB1555, the OPPOSITE of the
    # ABGR1555 used by bgNN0.bin. Proof: bank 0 of pl02pl.bin unswapped gives a
    # 7-step skin ramp at 0x22-0x28 ((247,198,148) -> (148,49,16)) and crimson at
    # 0x20-0x21 (Ryu's headband); swapped it renders all of that as blue.
    banks = [unpack_1555(rawpal[b * 128:(b + 1) * 128], 64, swap_rb=False)
             for b in range(len(rawpal) // 128)]

    n = struct.unpack_from("<I", d, 0)[0] // 4
    toff = struct.unpack_from("<%dI" % n, d, 0)
    textbl = cfg["to_tex"]
    ntex = struct.unpack_from("<I", d, textbl)[0] // 4
    print("%s: %d piece lists, texture_table at 0x%X with %d entries"
          % (args.cg, n, textbl, ntex))

    base = toff[args.list]
    count = struct.unpack_from("<H", d, base)[0]
    pieces = [struct.unpack_from("<hhHH", d, base + 2 + k * 8) for k in range(count)]
    print("list %d at 0x%X: %d pieces" % (args.list, base, count))
    for k, (x, y, attr, code) in enumerate(pieces):
        print("  piece %2d  x=%5d y=%5d attr=0x%04X code=%d" % (k, x, y, attr, code))

    todo = range(count) if args.all_pieces else [args.piece]
    os.makedirs(OUT_DIR, exist_ok=True)
    for k in todo:
        x, y, attr, code = pieces[k]
        if code >= ntex:
            print("  piece %d: code %d out of range" % (k, code))
            continue
        tp = textbl + struct.unpack_from("<I", d, textbl + code * 4)[0]
        whb = d[tp]
        wh = (whb & 3) + 1
        if wh not in WH_PIXELS:
            print("  piece %d: unsupported wh=%d (byte 0x%02X)" % (k, wh, whb))
            continue
        side = WH_PIXELS[wh]
        size = (wh * wh) << 6
        px, used = lz_ext_p6_fx(d[tp + 1:], size)
        px = unswizzle(px, side)
        bank = banks[args.bank if args.bank is not None else (attr & 3)]
        hi = max(px)
        rgba = bytearray(size * 4)
        for i, v in enumerate(px):
            rgba[i * 4:i * 4 + 4] = bytes(bank[v])
        p = os.path.join(OUT_DIR, "10_%s_list%d_piece%02d_code%d.png"
                         % (args.cg.split(".")[0], args.list, k, code))
        write_png(p, side, side, rgba)
        opaque = sum(1 for i in range(3, len(rgba), 4) if rgba[i])
        print("  piece %2d -> %s  %dx%d  wh=%d  %d LZ bytes -> %d px  "
              "max index %d  %d/%d opaque"
              % (k, os.path.basename(p), side, side, wh, used, size, hi,
                 opaque, size))


if __name__ == "__main__":
    main()
