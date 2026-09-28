#!/usr/bin/env python3
"""decode_stages - batch-decode every stage background from a local SF33RD.AFS.

Reuses the pipeline validated on stage00 (see docs/format-notes.md):
  * pTEX chunk walk + zlib payload        ppg_inspect / PPGFile.c:957-996
  * trans-table per-sub-rect palette      PPGFile.c:239-370  ("The trans table")
  * bgNN0.bin palette, LE + ABGR1555      color3rd.c:262-278, 426-441
  * sparse tile placement via bitmask     bg.c:299-312, bg_data.c:378, bg.c:623-625

Output: work/decoded/<stage>/ (gitignored). Derived pixels stay local.
"""

import argparse
import json
import os
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_inspect import walk_chunks                      # noqa: E402
from ppg_decode_test import write_png                    # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402

OUT_ROOT = os.path.join("work", "decoded")
BANK_BYTES = 128          # 64 colours x u16, color3rd.c:540-553
GRID_W = 8                # bg.c:623-625: (y >> 7) << 3
TILE = 128

# bgtex_stage_gbix[22][3] - bg_data.c:378
STAGE_GBIX = [
    (0xF0F0F0F0, 0x7F7FFFFF, 0x0), (0xFFFFFFFF, 0x0, 0x0),
    (0x3078FCFF, 0xFF, 0x7E7E7E7E), (0xFFFFFFFF, 0xC0E0F1FF, 0x0),
    (0xFFFFFFFF, 0x0, 0x0), (0x7E7E7E7E, 0x424FEFFF, 0x0),
    (0xFFFFFFFF, 0x0, 0x0), (0x7E00007E, 0xF0FFF2FF, 0x7E7E7E00),
    (0x3C3C3C3C, 0xFF, 0x0), (0x7F7F7F3F, 0x80F4FF, 0x0),
    (0xFFFFFFFF, 0xFFFFFFFF, 0x0), (0xFFFFFFFF, 0x0, 0x0),
    (0x7F7F7F3F, 0x98FFFFF, 0x0), (0xFFFFFF38, 0x3FFFFFFF, 0x0),
    (0xFFFFFFFF, 0x6FEFEFFF, 0x18181818), (0xFFFFFFFF, 0x80E6FFFF, 0x0),
    (0x7E7E0000, 0x77FFFFF, 0x0), (0x7E7E0000, 0x77FFFFF, 0x0),
    (0x7E7E7E7E, 0x424FEFFF, 0x0), (0xFFFFFFFF, 0x0, 0x0),
    (0x3C3C3C1C, 0x20343C3C, 0x0), (0x3E3E3E3E, 0x0, 0x0),
]

# use_real_scr[22] - bg_data.c:41.  Number of BG layers actually used; masks beyond
# this are present in the table but never consumed (bg.c:301).
USE_REAL_SCR = [2, 1, 3, 2, 1, 2, 1, 2, 2, 2, 2, 1, 2, 2, 3, 2, 2, 2, 2, 1, 2, 1]

# rewrite_scr[22] - bg_data.c:45.  Extra chunks consumed after the layers, from the
# same sequential accnum stream (bg.c:316-326).  For most stages these are runtime
# substitution tiles: Bg_Kakikae_Set (bg.c:71-150) points rw_dat/rw_gbix at a
# bgrw_data_tbl script and the draw loop swaps a *single* grid index for a rewrite
# index when its timer fires, so they have no fixed slot and are dumped as chunk
# PNGs only.  See ANIM_PAGES for the one stage that does not work that way.
REWRITE_SCR = [0, 0, 0, 25, 0, 0, 0, 12, 24, 0, 96, 0, 0, 0, 1, 0, 0, 0, 10, 18, 0, 0]

# Stages whose trailing chunks are whole extra PAGES of a layer, not slotless
# substitution tiles.  Only stage 10 (Yang, Hong Kong): Bg_Kakikae_Set sets
# tokusyu_stage = 2 (bg.c:100-106) and the draw loop (bg.c:733-777) adds
# yang_ix_plus = yang_ix << 5 to BG1's index, with yang_ix cycling 0..3 every 4
# game frames.  BG1 is therefore FOUR full 32-slot pages of rain/neon animation:
# page 0 is the normal masked layer, pages 1..3 are the first 96 rewrite chunks,
# reusing BG1's own mask and grid.  Decoding them as roleless "rewrite" tiles is
# what made stage10 look like 96 chunks of garbage - each is a sparse rain overlay
# that only reads as art once placed back on its grid slot.
# stage -> (layer index, total pages including the base masked one)
ANIM_PAGES = {10: (1, 4)}

# stage 7 additionally consumes 3 chunks for the Akane sprite (bg.c:328-340).
EXTRA_CHUNKS = {7: 3}

# stem -> (ppg name, palette name, bgtex_stage_gbix row).  Pairing from ldreq_tbl[]
# (gd3rd_data.c); rule is stageNN.ppg <-> bgNN0.bin.  There is no stage17.ppg.
STAGES = ([("stage%02d" % n, "stage%02d.ppg" % n, "bg%02d0.bin" % n, n)
           for n in list(range(0, 17)) + [18, 19]]
          + [("Bonus00", "Bonus00.ppg", "bns00.bin", 20),
             ("Bonus01", "Bonus01.ppg", "bns10.bin", 21)])


def by_name(entries):
    return {e["name"]: e for e in entries if e["name"]}


def load_banks(f, entry):
    f.seek(entry["offset"])
    raw = f.read(entry["size"])
    n = len(raw) // BANK_BYTES
    return [unpack_1555(raw[b * BANK_BYTES:(b + 1) * BANK_BYTES], 64, swap_rb=True)
            for b in range(n)], len(raw) % BANK_BYTES


def bank_tables(bank):
    """4 x 256-byte translate tables (R,G,B,A) for one 64-colour bank."""
    cols = [bank[i % 64] for i in range(256)]
    return [bytes(c[ch] for c in cols) for ch in range(4)]


def decode_chunk(f, base, c, banks, tabs, notes, tag, stat):
    """Decode one 8bpp pTEX using its trans table. Returns (W, H, rgba) or None."""
    W, H, uw = c["width"], c["height"], c["width_units"]
    f.seek(base + 16)
    tran = f.read(c["transNums"] * 3)
    f.seek(base + c["data_offset"])
    payload = f.read(c["file_size"] - c["data_offset"])
    try:
        codec = c["compress_raw"] & 3
        idx = zlib.decompress(payload) if codec == 2 else payload[:W * H]
    except zlib.error as exc:
        notes.append("%s: inflate failed (%s)" % (tag, exc))
        return None
    if len(idx) != W * H:
        notes.append("%s: payload %d bytes, expected %d" % (tag, len(idx), W * H))
        return None

    planes = [bytearray(W * H) for _ in range(4)]
    covered = bytearray(W * H)
    for k in range(c["transNums"]):
        pofs, ipoint, cofsxy = tran[k * 3], tran[k * 3 + 1], tran[k * 3 + 2]
        if pofs >= len(banks):
            notes.append("%s: trans rec %d wants bank %d, palette has %d"
                         % (tag, k, pofs, len(banks)))
            return None
        t = tabs[pofs]
        sx, sy = ipoint % uw, ipoint // uw
        xs, ys = (cofsxy >> 4) + 1, (cofsxy & 0xF) + 1
        for y in range(sy * 16, min((sy + ys) * 16, H)):
            a, b = y * W + sx * 16, y * W + min((sx + xs) * 16, W)
            if b <= a:
                continue
            row = idx[a:b]
            for ch in range(4):
                planes[ch][a:b] = row.translate(t[ch])
            covered[a:b] = b"\x01" * (b - a)

    miss = W * H - sum(covered)

    stat[0] += W * H
    stat[1] += miss
    rgba = bytearray(W * H * 4)
    for ch in range(4):
        rgba[ch::4] = planes[ch]
    return W, H, bytes(rgba)


def place(tiles, mask, notes, tag):
    """Blit tiles into the 8-wide 128px grid at the mask's set slots."""
    slots = [i for i in range(32) if mask & (0x80000000 >> i)]
    W, H = GRID_W * TILE, 4 * TILE
    buf = bytearray(W * H * 4)
    used = 0
    for slot in slots:
        if used >= len(tiles):
            notes.append("%s: ran out of chunks at slot %d" % (tag, slot))
            break
        t = tiles[used]
        used += 1
        if t is None:
            continue
        tw, th, im = t
        if (tw, th) != (TILE, TILE):
            notes.append("%s: slot %d tile is %dx%d, not %dx%d"
                         % (tag, slot, tw, th, TILE, TILE))
            continue
        ox, oy = (slot % GRID_W) * TILE, (slot // GRID_W) * TILE
        for y in range(th):
            d = ((oy + y) * W + ox) * 4
            buf[d:d + tw * 4] = im[y * tw * 4:(y + 1) * tw * 4]
    return W, H, buf, len(slots)


def over(dst, src):
    """src composited over dst, both RGBA8, alpha is 0 or 255."""
    for i in range(3, len(src), 4):
        if src[i]:
            dst[i - 3:i + 1] = src[i - 3:i + 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("afs")
    ap.add_argument("--only", help="comma-separated stage stems")
    ap.add_argument("--no-chunks", action="store_true",
                    help="skip per-chunk PNGs, write assembled only")
    args = ap.parse_args()

    entries, _ = parse_index(args.afs)
    idxn = by_name(entries)
    want = set(args.only.split(",")) if args.only else None
    rows = []

    with open(args.afs, "rb") as f:
        for stem, ppg, pal, gi in STAGES:
            if want and stem not in want:
                continue
            notes = []
            if ppg not in idxn or pal not in idxn:
                rows.append((stem, "-", "MISSING", "no %s / %s in AFS" % (ppg, pal)))
                continue
            e, pe = idxn[ppg], idxn[pal]
            banks, slack = load_banks(f, pe)
            if slack:
                notes.append("%s: %d trailing bytes past last bank" % (pal, slack))
            tabs = [bank_tables(b) for b in banks]

            chunks = [c for c in walk_chunks(f, e["offset"], e["size"])
                      if c["kind"] == "pTEX"]
            depths = sorted({c["pixel_raw"] & 3 for c in chunks})
            if depths != [1]:
                notes.append("pixel modes present: %s (only 8bpp/mode 1 handled)" % depths)
            zero_trans = sum(1 for c in chunks if not c["transNums"])
            if zero_trans:
                notes.append("%d chunk(s) have transNums==0 (palette is caller state)"
                             % zero_trans)

            scrno = USE_REAL_SCR[gi]
            masks = STAGE_GBIX[gi][:scrno]
            rw = REWRITE_SCR[gi]
            extra = EXTRA_CHUNKS.get(gi, 0)
            pops = [bin(m).count("1") for m in masks]
            expect = sum(pops) + rw + extra
            if expect != len(chunks):
                notes.append("chunk budget %s + rewrite %d + extra %d = %d != %d "
                             "pTEX chunks" % (pops, rw, extra, expect, len(chunks)))

            out = os.path.join(OUT_ROOT, stem)
            os.makedirs(out, exist_ok=True)
            tiles, stat = [], [0, 0]
            man = {"ppg": ppg, "ppg_entry": e["index"], "palette": pal,
                   "palette_entry": pe["index"], "palette_banks": len(banks),
                   "use_real_scr": scrno, "rewrite_scr": rw, "extra_chunks": extra,
                   "masks": ["0x%08X" % m for m in masks],
                   "anim_layer": None, "anim_pages": 1, "chunks": []}
            for n, c in enumerate(chunks):
                tag = "%s chunk %02d" % (stem, n)
                if (c["pixel_raw"] & 3) != 1:
                    tiles.append(None)
                    continue
                t = decode_chunk(f, e["offset"] + c["chunk_offset"], c, banks, tabs,
                                 notes, tag, stat)
                tiles.append(t)
                f.seek(e["offset"] + c["chunk_offset"] + 16)
                man["chunks"].append({
                    "n": n, "width": c["width"], "height": c["height"],
                    "width_units": c["width_units"], "height_units": c["height_units"],
                    "pixel_raw": c["pixel_raw"], "compress_raw": c["compress_raw"],
                    "formARGB": "0x%04X" % c["formARGB"], "transNums": c["transNums"],
                    "trans": f.read(c["transNums"] * 3).hex(),
                    "chunk_offset": c["chunk_offset"], "file_size": c["file_size"],
                })
                if t and not args.no_chunks:
                    write_png(os.path.join(out, "chunk_%02d.png" % n), t[0], t[1], t[2])

            comp = None
            layers_written = 0
            anim_written = 0
            layer_bufs = []
            # place consumes chunks sequentially across layers (accnum, bg.c:299-312)
            cursor = 0
            for L, mask in enumerate(masks):
                if not mask:
                    continue
                nslots = bin(mask).count("1")
                W, H, buf, _ = place(tiles[cursor:cursor + nslots], mask, notes,
                                     "%s L%d" % (stem, L))
                cursor += nslots
                write_png(os.path.join(out, "layer%d.png" % L), W, H, buf)
                layers_written += 1
                if comp is None:
                    comp = bytearray(W * H * 4)
                over(comp, buf)
                layer_bufs.append((L, W, H, buf))
            if comp is not None:
                write_png(os.path.join(out, "assembled.png"), GRID_W * TILE, 4 * TILE,
                          comp)

            # Extra whole-page frames of one layer (ANIM_PAGES).  They come out of
            # the same sequential stream, straight after the masked layers, and use
            # that layer's mask, so place() handles them unchanged.
            anim_layer, anim_pages = ANIM_PAGES.get(gi, (None, 1))
            if anim_layer is not None:
                nslots = bin(masks[anim_layer]).count("1")
                need = (anim_pages - 1) * nslots
                if need > rw:
                    notes.append("stage wants %d animation-page chunks but "
                                 "rewrite_scr is only %d" % (need, rw))
                    anim_layer, anim_pages = None, 1
            for p in range(1, anim_pages):
                W, H, buf, _ = place(tiles[cursor:cursor + nslots], masks[anim_layer],
                                     notes, "%s L%d p%d" % (stem, anim_layer, p))
                cursor += nslots
                write_png(os.path.join(out, "layer%d_p%d.png" % (anim_layer, p)),
                          W, H, buf)
                frame = bytearray(W * H * 4)
                for L, lw, lh, lbuf in layer_bufs:
                    over(frame, buf if L == anim_layer else lbuf)
                write_png(os.path.join(out, "assembled_p%d.png" % p), W, H, frame)
                anim_written += 1

            if stat[0]:
                cov = 100.0 * (stat[0] - stat[1]) / stat[0]
                if cov < 99.9:
                    notes.append("%.1f%% of tile area drawn (rest is undrawn -> "
                                 "transparent, expected)" % cov)
            status = "OK" if not notes else "OK*"
            # tag each chunk with its role, grid slot and (for ANIM_PAGES) page
            cur2 = 0

            def tag(i, **kw):
                if i < len(man["chunks"]):
                    man["chunks"][i].update(**kw)

            for L, mask in enumerate(masks):
                for i in range(32):
                    if mask & (0x80000000 >> i):
                        tag(cur2, role="layer%d" % L, slot=i,
                            **({"page": 0} if L == anim_layer else {}))
                        cur2 += 1
            for p in range(1, anim_pages):
                for i in range(32):
                    if masks[anim_layer] & (0x80000000 >> i):
                        tag(cur2, role="layer%d" % anim_layer, slot=i, page=p)
                        cur2 += 1
            rw_left = rw - (anim_written * bin(masks[anim_layer]).count("1")
                            if anim_layer is not None else 0)
            for k in range(cur2, len(man["chunks"])):
                man["chunks"][k].update(
                    role="rewrite" if k - cur2 < rw_left else "extra", slot=None)
            if anim_written:
                man["anim_layer"] = anim_layer
                man["anim_pages"] = anim_pages
                notes.append("layer %d is %d animated pages (bg.c:733-777); %d of the "
                             "%d rewrite chunks are pages 1..%d, not slotless tiles"
                             % (anim_layer, anim_pages,
                                anim_written * bin(masks[anim_layer]).count("1"), rw,
                                anim_pages - 1))
            with open(os.path.join(out, "manifest.json"), "w") as mf:
                json.dump(man, mf, indent=1)

            tail = len(chunks) - cursor
            if tail:
                notes.append("%d trailing chunk(s) (rewrite %d + extra %d) dumped as "
                             "chunk PNGs, no fixed grid slot" % (tail, rw, extra))
            rows.append((stem, "%d (%d layers%s)"
                         % (len(chunks), layers_written,
                            ", +%d pages" % anim_written if anim_written else ""),
                         status, "; ".join(notes) if notes else ""))
            print("%-8s %-14s %s" % rows[-1][:3], flush=True)
            for n in notes:
                print("           ! " + n, flush=True)

    print("\n%-9s %-16s %-7s %s" % ("stage", "chunks", "status", "anomalies"))
    for r in rows:
        print("%-9s %-16s %-7s %s" % r)


if __name__ == "__main__":
    main()
