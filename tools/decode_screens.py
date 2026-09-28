#!/usr/bin/env python3
"""decode_screens - decode standalone UI/menu PPG screens from a local SF33RD.AFS.

These are the entries loaded by AFS index rather than through a texture group:
the title, select, win, rank, warning and logo screens. They use the same PPG
container as the stages, but their chunks are independent textures rather than
slots on a background grid, so there is no mask/layer assembly -- each pTEX
chunk is decoded on its own.

Format, transcribed from 3sx:
  * chunk walk + 4-byte align .......... PPGFile.c:957-996
  * pixel mode / pitch / formARGB ...... ppgSetupContextFromPPG, PPGFile.c:1098-1205
  * embedded palette chunks ............ ppgSetupPalChunk, PPGFile.c:485-590
        payload starts at pPAL + 16, holds `palettes` banks of
        `colors_per_palette` entries, each `entry_bytes` wide
  * trans-table per-sub-rect banks ..... PPGFile.c:239-370 (same as the stages)

Output: work/decoded/<stem>/chunk_NN.png plus a manifest, gitignored and local.
Derived pixels stay local.
"""

import argparse
import json
import math
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_inspect import walk_chunks                      # noqa: E402
from ppg_decode_test import write_png                    # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402

OUT_ROOT = os.path.join("work", "decoded")
SHEET_ROOT = os.path.join("work", "screens-preview")

# Screens whose banks are not in the file: the engine loads them into ColorRAM
# from a separate entry and the trans table's pofs indexes it directly, exactly
# as the stages index bgNN0.bin. Opening.ppg's pofs reaches 85 and title.bin
# holds 88 banks (color_file[84], color3rd.c).
EXTERNAL_PALETTE = {"Opening.ppg": "title.bin"}

# AFS entry name -> output stem. Index is resolved from the archive's own table,
# never hardcoded; the name is the key because the index table is the authority.
SCREENS = [
    "Rank1.ppg", "Rank2.ppg", "Select.ppg", "SelectNew.ppg",
    "Win.ppg", "Win1.ppg", "Win2.ppg", "Win3.ppg",
    "Ake3rd.ppg", "scrscrn.ppg", "Warning.ppg", "Warninge.ppg",
    "CapLogo.ppg", "Opening.ppg", "TitleTM.ppg", "Title_R.ppg",
]


def change_endian(raw, depth, dend_flag, col4):
    """ppgChangeDataEndian, PPGFile.c. 16- and 32-bit data is stored big-endian
    and byte-swapped on load unless the "already little-endian" flag is set
    (pixel & 4 for a pTEX, c_mode & 4 for a pPAL). 8bpp and 4bpp planes are
    single bytes and return early, so index data is never touched.

    Skipping this is not a subtle error: it scrambles the alpha bit and both
    colour channels. It is invisible to a re-encode round trip, because the
    inverse of a wrong read is a matching wrong write.
    """
    if depth in (0, 1) or dend_flag:
        return raw
    if col4:
        return struct.pack("<%dI" % (len(raw) // 4),
                           *struct.unpack(">%dI" % (len(raw) // 4), raw))
    return struct.pack("<%dH" % (len(raw) // 2),
                       *struct.unpack(">%dH" % (len(raw) // 2), raw))


def inflate(payload, codec, expect, tag, notes):
    """ppgDecompress, PPGFile.c:408-438. koCmpr = compress & 3."""
    try:
        raw = zlib.decompress(payload) if codec == 2 else payload[:expect]
    except zlib.error as exc:
        notes.append("%s: inflate failed (%s)" % (tag, exc))
        return None
    if len(raw) != expect:
        notes.append("%s: payload %d bytes, expected %d" % (tag, len(raw), expect))
        return None
    return raw


def select_palette(rec_depth, max_index, ordinal, same_depth, pals, notes, tag):
    """Choose which pPAL and bank a texture chunk uses.

    The engine does not store this: ppgSetupPalChunk takes the palette ordinal
    from the caller (sc_sub.c:218-221 binds four scrscrn palettes to four draw
    lists), so the pairing is caller state. These rules recover it from the file
    where the file makes it unambiguous, and say so when it does not.
    """
    usable = [i for i, p in enumerate(pals) if p["colors_per_palette"] > max_index]
    if not usable:
        notes.append("%s: uses index %d, no palette in the file is that wide"
                     % (tag, max_index))
        return None, None, "none"
    if len(usable) == 1:
        i = usable[0]
        if pals[i]["palettes"] == same_depth and same_depth > 1:
            # One palette holding exactly one bank per chunk: Ake3rd.ppg.
            return i, ordinal, "bank-per-chunk"
        return i, 0, "only-palette"
    if len(usable) == same_depth:
        # One palette per chunk, in order: Warning.ppg / Warninge.ppg.
        return usable[ordinal], 0, "palette-per-chunk"
    notes.append("%s: %d palettes could apply; decoded with the first. The real "
                 "choice is made at draw time, not stored in the file."
                 % (tag, len(usable)))
    return usable[0], 0, "ambiguous"


def read_banks(f, base, pal, tag, notes):
    """Decode one pPAL chunk into a list of banks of (r,g,b,a)."""
    if pal["entry_bytes"] != 2:
        notes.append("%s: %d-byte palette entries not handled" % (tag, pal["entry_bytes"]))
        return None
    if pal["formARGB"] != 0x1555:
        notes.append("%s: palette formARGB 0x%04X not handled" % (tag, pal["formARGB"]))
        return None
    items = pal["colors_per_palette"]
    total = pal["palettes"] * items * pal["entry_bytes"]
    f.seek(base + 16)
    raw = inflate(f.read(pal["file_size"] - 16), pal["compress_raw"] & 3, total, tag, notes)
    if raw is None:
        return None
    raw = change_endian(raw, pal["entry_bytes"], pal["endian_flag"],
                        pal["formARGB"] == 0x8888)
    # Texture palettes are ARGB1555 in file order; unlike the raw ColorRAM
    # bgNN0.bin files there is no R/B swap here (ppgSetupContextFromPPL).
    return [unpack_1555(raw[b * items * 2:(b + 1) * items * 2], items, swap_rb=False)
            for b in range(pal["palettes"])]


def indices(raw, w, h, depth):
    """Unpack the pixel plane to one byte per pixel."""
    if depth == 1:
        return raw
    # 4bpp: pitch is width/2 and the low nibble is the left pixel
    # (ppgSetupContextFromPPG sets pitch = width / 2 for mode 0).
    out = bytearray(w * h)
    out[0::2] = bytes(b & 0xF for b in raw)
    out[1::2] = bytes(b >> 4 for b in raw)
    return bytes(out)


def paint(idx, w, h, banks, trans, tag, notes, base_bank=0):
    """Map indices to RGBA. With a trans table each sub-rect picks its own bank."""
    rgba = bytearray(w * h * 4)
    if not trans:
        bank = banks[base_bank]
        for i, v in enumerate(idx):
            rgba[i * 4:i * 4 + 4] = bytes(bank[v % len(bank)])
        return bytes(rgba), w * h
    uw = w // 16
    covered = 0
    for k in range(len(trans) // 3):
        pofs, ipoint, cofsxy = trans[k * 3], trans[k * 3 + 1], trans[k * 3 + 2]
        if pofs >= len(banks):
            notes.append("%s: trans rec %d wants bank %d, file has %d"
                         % (tag, k, pofs, len(banks)))
            return None, 0
        bank = banks[pofs]
        sx, sy = ipoint % uw, ipoint // uw
        xs, ys = (cofsxy >> 4) + 1, (cofsxy & 0xF) + 1
        for y in range(sy * 16, min((sy + ys) * 16, h)):
            a, b = y * w + sx * 16, y * w + min((sx + xs) * 16, w)
            for i in range(a, b):
                rgba[i * 4:i * 4 + 4] = bytes(bank[idx[i] % len(bank)])
            covered += max(0, b - a)
    return bytes(rgba), covered


def contact_sheet(path, tiles, gap=8):
    """One PNG per screen showing every chunk in order.

    A screen's chunks are separate textures with no stored layout, so this is a
    contact sheet for identifying content, not a reconstruction of the screen.
    Transparency is drawn as a checkerboard so it reads as "nothing here"
    rather than as black artwork.
    """
    if not tiles:
        return
    cw = max(w for _, w, _, _ in tiles)
    ch = max(h for _, _, h, _ in tiles)
    cols = math.ceil(math.sqrt(len(tiles)))
    rows = math.ceil(len(tiles) / cols)
    sw, sh = cols * cw + (cols + 1) * gap, rows * ch + (rows + 1) * gap
    buf = bytearray(sw * sh * 4)
    for y in range(sh):                                   # checkerboard backdrop
        for x in range(sw):
            v = 0x66 if ((x >> 3) + (y >> 3)) & 1 else 0x4C
            buf[(y * sw + x) * 4:(y * sw + x) * 4 + 4] = bytes((v, v, v, 255))
    for i, (_, w, h, rgba) in enumerate(tiles):
        ox = gap + (i % cols) * (cw + gap)
        oy = gap + (i // cols) * (ch + gap)
        for y in range(h):
            row = rgba[y * w * 4:(y + 1) * w * 4]
            d = ((oy + y) * sw + ox) * 4
            for x in range(w):                            # composite over the board
                if row[x * 4 + 3]:
                    buf[d + x * 4:d + x * 4 + 4] = row[x * 4:x * 4 + 4]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_png(path, sw, sh, bytes(buf))


def read_external_banks(f, entries, name, notes):
    e = entries[name]
    f.seek(e["offset"])
    raw = f.read(e["size"])
    return [unpack_1555(raw[b*128:(b+1)*128], 64, swap_rb=False)
            for b in range(len(raw) // 128)]


def decode_entry(f, entry, out, keep_notes, sheet=True, entries=None):
    notes = []
    base, stem = entry["offset"], entry["name"].split(".")[0]
    chunks = list(walk_chunks(f, base, entry["size"]))
    tex = [c for c in chunks if c["kind"] == "pTEX"]
    pals = [c for c in chunks if c["kind"] == "pPAL"]

    external = EXTERNAL_PALETTE.get(entry["name"])
    ext_banks = (read_external_banks(f, entries, external, notes)
                 if external and entries and external in entries else None)
    palettes = []
    for n, p in enumerate(pals):
        palettes.append(read_banks(f, base + p["chunk_offset"], p,
                                   "%s pPAL %d" % (stem, n), notes))

    # Ordinal of each indexed chunk among the chunks of its own pixel depth.
    depth_ordinal, seen = {}, {}
    for n, c in enumerate(tex):
        if c["bitdepth"] in (0, 1):
            depth_ordinal[n] = seen.get(c["bitdepth"], 0)
            seen[c["bitdepth"]] = depth_ordinal[n] + 1

    tiles = []
    # "kind" tells the rest of the pipeline this is a screen: independent
    # textures to copy through, not a stage grid to assemble into pages.
    man = {"kind": "screen",
           "ppg": entry["name"], "ppg_entry": entry["index"],
           "palette_chunks": len(pals),
           "palette_banks": [p["palettes"] for p in pals],
           "palette_colors": [p["colors_per_palette"] for p in pals],
           "chunks": []}
    written = 0
    for n, c in enumerate(tex):
        tag = "%s chunk %02d" % (stem, n)
        w, h, depth = c["width"], c["height"], c["bitdepth"]
        cb = base + c["chunk_offset"]
        f.seek(cb + 16)
        trans = f.read(c["transNums"] * 3)
        f.seek(cb + c["data_offset"])
        payload = f.read(c["file_size"] - c["data_offset"])
        raw = inflate(payload, c["compress_raw"] & 3, c["pitch"] * h, tag, notes)
        if raw is not None:
            raw = change_endian(raw, depth, c["endian_flag"],
                                c["formARGB"] == 0x8888)

        record = {"n": n, "width": w, "height": h,
                  "width_units": c["width_units"], "height_units": c["height_units"],
                  "pixel_raw": c["pixel_raw"], "pixel_mode": c["pixel_mode"],
                  "compress_raw": c["compress_raw"],
                  "formARGB": "0x%04X" % c["formARGB"], "transNums": c["transNums"],
                  "trans": trans.hex(), "chunk_offset": c["chunk_offset"],
                  "file_size": c["file_size"]}
        man["chunks"].append(record)
        if raw is None:
            record["decoded"] = False
            continue

        if depth == 2:
            if c["formARGB"] != 0x1555:
                notes.append("%s: direct formARGB 0x%04X not handled" % (tag, c["formARGB"]))
                record["decoded"] = False
                continue
            # ARGB1555 with no R/B swap: ppgSetupContextFromPPG puts r at shift 10.
            px = unpack_1555(raw, w * h, swap_rb=False)
            rgba = b"".join(bytes(v) for v in px)
        elif depth in (0, 1):
            idx = indices(raw, w, h, depth)
            if ext_banks is not None:
                rgba, covered = paint(idx, w, h, ext_banks, trans, tag, notes, 0)
                if rgba is None:
                    record["decoded"] = False
                    continue
                record.update(palette_source=external, covered_pct=round(
                    100.0 * covered / (w * h), 1))
                os.makedirs(out, exist_ok=True)
                write_png(os.path.join(out, "chunk_%02d.png" % n), w, h, rgba)
                record["decoded"] = True
                tiles.append((n, w, h, rgba))
                written += 1
                continue
            if not pals:
                # SelectNew.ppg still has no known palette source.
                notes.append("%s: indexed with no palette in the file (external "
                             "palette, unresolved)" % tag)
                record["decoded"] = False
                continue
            pal_n, bank_n, rule = select_palette(
                depth, max(idx), depth_ordinal[n], seen[depth], pals, notes, tag)
            if pal_n is None or palettes[pal_n] is None:
                record["decoded"] = False
                continue
            banks = palettes[pal_n]
            record.update(palette_chunk=pal_n, palette_bank=bank_n, palette_rule=rule)
            rgba, covered = paint(idx, w, h, banks, trans, tag, notes, bank_n)
            if rgba is None:
                record["decoded"] = False
                continue
            record["covered_pct"] = round(100.0 * covered / (w * h), 1)
        else:
            notes.append("%s: %d-byte pixels not handled" % (tag, depth))
            record["decoded"] = False
            continue

        os.makedirs(out, exist_ok=True)
        write_png(os.path.join(out, "chunk_%02d.png" % n), w, h, rgba)
        record["decoded"] = True
        tiles.append((n, w, h, rgba))
        written += 1

    if written:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "manifest.json"), "w") as mf:
            json.dump(man, mf, indent=1)
        if sheet:
            # Kept out of the chunk folder so check_upscaled.py still gates a
            # folder that contains exactly the chunks and nothing else.
            contact_sheet(os.path.join(SHEET_ROOT, stem + ".png"), tiles)
    keep_notes.extend(notes)
    return len(tex), len(pals), written, notes


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("afs")
    ap.add_argument("--only", help="comma-separated entry names or stems")
    ap.add_argument("--no-sheet", action="store_true",
                    help="skip the per-screen contact sheet in " + SHEET_ROOT)
    args = ap.parse_args()

    want = set(args.only.split(",")) if args.only else None
    entries, _ = parse_index(args.afs)
    by_name = {e["name"]: e for e in entries if e["name"]}
    rows = []
    with open(args.afs, "rb") as f:
        for name in SCREENS:
            stem = name.split(".")[0]
            if want and name not in want and stem not in want:
                continue
            if name not in by_name:
                rows.append((stem, 0, 0, 0, "MISSING"))
                continue
            notes = []
            ntex, npal, written, notes = decode_entry(
                f, by_name[name], os.path.join(OUT_ROOT, stem), notes,
                sheet=not args.no_sheet, entries=by_name)
            rows.append((stem, ntex, npal, written,
                         "OK" if written == ntex else
                         "PARTIAL" if written else "SKIP"))
            print("%-10s %3d pTEX %2d pPAL %3d decoded  %s"
                  % (stem, ntex, npal, written, rows[-1][4]), flush=True)
            for n in notes[:4]:
                print("           ! " + n, flush=True)
            if len(notes) > 4:
                print("           ! ... %d more like the above" % (len(notes) - 4),
                      flush=True)

    print("\n%-10s %6s %6s %8s %s" % ("screen", "pTEX", "pPAL", "decoded", "status"))
    for r in rows:
        print("%-10s %6s %6s %8s %s" % r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
