#!/usr/bin/env python3
"""cg_repack - rebuild a character plNN.bin from frame PNGs.

What changes and what does not
------------------------------
Only the **texture table** is rebuilt. The trans table (piece lists) is copied
byte for byte: piece count, per-piece x/y deltas, attr and tile `code` all stay
exactly as they were, so tile geometry, frame anchors and flip bits cannot drift
and `to_tex` (a compiled-in constant, texgroup_data.c) does not move. Only
`to_chd` can shift, and only if the re-encoded texture table changes size - the
tool pads it back to the original length whenever it can, which keeps the file a
drop-in for an unmodified engine.

Pipeline per tile, the inverse of cg_decode_tile:
  frame PNG -> cut the piece rect -> un-flip -> quantise to the piece's
  64-colour bank -> re-swizzle (inverse dctex_linear) -> lz_ext_p6_fx encode.

Two details that bite:

* **Overlapping pieces.** 2382 piece pairs across 679 of Ryu's 725 frames
  overlap, and the frame PNG only keeps whichever piece drew last. Cutting a
  rect straight out of the frame would therefore pull a neighbour's pixels into
  the tile. So the decode's compositing order is replayed to build an ownership
  map, and a tile only takes frame pixels it actually owns; everywhere else it
  keeps the original tile's pixel.
* **Undrawn tile area.** A tile is side x side but only dw x dh of it is ever
  sampled. Each rebuilt tile starts as a copy of the original and only the
  dw x dh window is overwritten, so the remainder round-trips untouched.

Scale
-----
1x only for this original-format AFS route. For the modified engine HD sidecar,
use cg_hd_pack.py instead (see docs/ryu4x-test.md). The original path below
explains why increasing this file format alone does not add resolution. seqsStoreChip
(mtrans.c:1600-1652) uses the same `w`/`h` for the sprite quad *and* for the UV
span into the 256x256 atlas:

    chip->v[1].x = x + w;                     /* game-space size */
    chip->t[1].s = (u + w - uv_dx) / 256.0f;  /* atlas UV span   */

so a character texel is welded 1:1 to a game pixel. Adding resolution needs the
quad size decoupled from the UV span, bigger atlas slots (16->32 and 32->64) and
new slot indexing - a sprite-renderer change, not a data change. See
docs/format-notes.md.
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
from cg_decode_tile import (DCTEX_LINEAR, TEXGROUP, WH_PIXELS,  # noqa: E402
                            lz_ext_p6_fx, unswizzle)

DEC = os.path.join("work", "decoded")
TILES = os.path.join("work", "tiles")
OUT = os.path.join("work", "repacked_cg")


def swizzle(lin, side):
    """linear -> Morton. Exact inverse of cg_decode_tile.unswizzle."""
    out = bytearray(side * side)
    if side == 32:
        for k in range(1024):
            out[DCTEX_LINEAR[k]] = lin[k]
    else:
        for i in range(side):
            for j in range(side):
                out[DCTEX_LINEAR[j + (i << 5)]] = lin[i * side + j]
    return bytes(out)


def lz_encode(data):
    """Encode for lz_ext_p6_fx. Greedy, and never overshoots len(data).

    Opcodes, from the decoder (mtrans.c:1935-1983):
      0x00-0x3F  literal, emits itself           1 byte  -> 1 px
      0x40|v     dist (v>>2)+1 (<=16),
                 len (v&3)+2 (<=5)               1 byte  -> 2..5 px
      0x80|hi,lo dist (n>>6)+1 (<=256),
                 len (n&0x3F)+2 (<=65)           2 bytes -> 2..65 px
                 n is only 14 bits ((b&0x3F)<<8 | next), so the distance
                 field is 8 bits -- 256 back, not 1024.
      0xC0|f|k   k=(b&0xF)+2 pairs of nibbles,
                 each OR'd with f=(b&0x30)       1+k B   -> 4..34 px

    One bounded search (dist<=256, len<=65) serves both back-reference forms;
    the cheaper 0x40 encoding is used whenever the match fits it. Candidates
    come from a 3-byte hash chain capped at CHAIN_CAP, which keeps this linear
    in the input - the flat transparent expanses that dominate sprite tiles are
    found as dist-1 self-overlapping matches and collapse at 65 px per 2 bytes.
    """
    CHAIN_CAP = 6
    out = bytearray()
    n = len(data)
    chains = {}
    i = 0
    while i < n:
        left = n - i
        best_px, best_cost, best_tok = 1, 1, None

        # 0xC0 nibble run: every pixel inside one 0x30 band, even length
        band = data[i] & 0x30
        run = 0
        while run < 34 and run < left and (data[i + run] & 0x30) == band:
            run += 1
        pairs = min(run // 2, 17)
        if pairs >= 2 and (pairs * 2) * best_cost > best_px * (1 + pairs):
            best_px, best_cost = pairs * 2, 1 + pairs
            best_tok = bytes((0xC0 | band | (pairs - 2),)) + bytes(
                ((data[i + 2 * k] & 0xF) << 4) | (data[i + 2 * k + 1] & 0xF)
                for k in range(pairs))

        # back-reference: one bounded search, cheapest encoding that fits
        if left >= 2:
            key = bytes(data[i:i + 3])
            blen, bdist = 0, 0
            for p in reversed(chains.get(key, ())):
                if i - p > 256:
                    break
                ln = 0
                while ln < 65 and ln < left and data[p + ln] == data[i + ln]:
                    ln += 1
                if ln > blen:
                    blen, bdist = ln, i - p
                    if blen == 65:
                        break
            if blen >= 2:
                if bdist <= 16 and blen <= 5:
                    cost, tok = 1, bytes(
                        (0x40 | (((bdist - 1) << 2) | (blen - 2)),))
                else:
                    v = ((bdist - 1) << 6) | (blen - 2)
                    cost, tok = 2, bytes((0x80 | (v >> 8), v & 0xFF))
                if blen * best_cost > best_px * cost:
                    best_px, best_cost, best_tok = blen, cost, tok

        if best_tok is None:
            out.append(data[i] & 0x3F)
            step = 1
        else:
            out += best_tok
            step = best_px

        for k in range(i, i + step):
            if k + 3 <= n:
                c = chains.setdefault(bytes(data[k:k + 3]), [])
                c.append(k)
                if len(c) > CHAIN_CAP:
                    del c[0]
        i += step
    return bytes(out)


def bank_lookup(bank):
    """RGBA tuple -> palette index, exact first then nearest."""
    exact = {}
    for i, c in enumerate(bank):
        exact.setdefault(tuple(c), i)
    clear = next((i for i, c in enumerate(bank) if not c[3]), 0)
    opaque = [(i, c) for i, c in enumerate(bank) if c[3]]
    cache = {}

    def look(px):
        v = cache.get(px)
        if v is not None:
            return v
        if not px[3]:
            v = clear
        else:
            v = exact.get(px)
            if v is None:
                v = min(opaque, key=lambda ic: (ic[1][0] - px[0]) ** 2
                        + (ic[1][1] - px[1]) ** 2 + (ic[1][2] - px[2]) ** 2)[0]
        cache[px] = v
        return v
    return look


def owner_map(fr, tiles, banks):
    """Replay the decode's compositing exactly; -1 where nothing was drawn.

    cg_decode_all draws pieces in list order but skips texels whose palette
    entry is transparent, so a later piece does NOT take a pixel it merely
    covers - it only takes the ones it actually paints. Marking ownership by
    rectangle instead of by opacity puts a neighbour's colour into this tile
    wherever the two overlap and this piece is clear, which is worth 0.5% of
    the pixels on 100 of Ryu's 725 frames.
    """
    W, H = fr["w"], fr["h"]
    own = [-1] * (W * H)
    for k, p in enumerate(fr["pieces"]):
        whb, side, pix = tiles[p["code"]]
        if pix is None:
            continue
        bank = banks[p["pal"]]
        fx = bool(p["attr"] & 0x8000)
        fy = bool(p["attr"] & 0x4000)
        dw, dh = p["dw"], p["dh"]
        for yy in range(dh):
            oy = p["y"] + yy
            if oy >= H:
                continue
            sy = dh - 1 - yy if fy else yy
            row = oy * W
            for xx in range(dw):
                ox = p["x"] + xx
                if ox >= W:
                    continue
                sx = dw - 1 - xx if fx else xx
                if bank[pix[sy * side + sx]][3]:
                    own[row + ox] = k
    return own


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("afs")
    ap.add_argument("--cg", default="pl02.bin")
    ap.add_argument("--src", help="frame PNG dir (default work/tiles/<stem>, "
                                  "falling back to work/decoded/<stem>)")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--reencode-all", action="store_true",
                    help="re-encode every tile even where the art is unchanged "
                         "(exercises the LZ encoder; produces a bigger file)")
    args = ap.parse_args()

    stem = args.cg.split(".")[0]
    cfg = TEXGROUP[args.cg]
    man = json.load(open(os.path.join(DEC, stem, "manifest.json")))
    src = args.src or (os.path.join(TILES, stem)
                       if os.path.isdir(os.path.join(TILES, stem))
                       else os.path.join(DEC, stem))

    entries, _ = parse_index(args.afs)
    byname = {e["name"]: e for e in entries}
    with open(args.afs, "rb") as f:
        e = byname[args.cg]
        f.seek(e["offset"])
        d = f.read(e["size"])
        pe = byname[cfg["pal"]]
        f.seek(pe["offset"])
        rawpal = f.read(pe["size"])

    banks = [unpack_1555(rawpal[b * 128:(b + 1) * 128], 64, swap_rb=False)
             for b in range(len(rawpal) // 128)]
    looks = [bank_lookup(b) for b in banks]

    to_tex = cfg["to_tex"]
    to_chd = cfg["to_chd"]
    ntex = struct.unpack_from("<I", d, to_tex)[0] // 4
    tofs = struct.unpack_from("<%dI" % ntex, d, to_tex)

    # decode every tile once; tiles we never touch are re-encoded from exactly
    # these bytes, so an untouched tile can only round-trip identically
    tiles, orig_blob = {}, {}
    for code in range(ntex):
        tp = to_tex + tofs[code]
        whb = d[tp]
        wh = (whb & 3) + 1
        if wh not in WH_PIXELS:
            tiles[code] = (whb, None, None)
            continue
        side = WH_PIXELS[wh]
        raw, used = lz_ext_p6_fx(d[tp + 1:], (wh * wh) << 6)
        tiles[code] = (whb, side, bytearray(unswizzle(raw, side)))
        orig_blob[code] = d[tp:tp + 1 + used]

    # check the scale up front: 1x is an engine limit, see the module docstring
    fr0 = man["frames"][0]
    im0 = Image.open(os.path.join(src, "frame_%03d.png" % fr0["n"]))
    if im0.width != fr0["w"] or im0.height != fr0["h"]:
        s = im0.width / fr0["w"]
        sys.exit("%s: frames in %s are %gx (%dx%d vs %dx%d in the manifest).\n"
                 "Use tools/cg_hd_pack.py for the HD sidecar. This AFS route is 1x only.\n"
                 "The original seqsStoreChip (mtrans.c:1600-1652) uses the\n"
                 "same w/h for the sprite quad and for the UV span into the 256x256\n"
                 "atlas, so a character texel is welded 1:1 to a game pixel. A 2x\n"
                 "character needs a sprite-renderer change, not a repack.\n"
                 "See \"characters are 1x\" in docs/format-notes.md."
                 % (stem, src, s, im0.width, im0.height, fr0["w"], fr0["h"]))

    # cut every piece back out of its frame
    updated, conflicts, missing = {}, 0, 0
    for fr in man["frames"]:
        p = os.path.join(src, "frame_%03d.png" % fr["n"])
        if not os.path.isfile(p):
            missing += 1
            continue
        im = Image.open(p).convert("RGBA")
        px = im.load()
        own = owner_map(fr, tiles, banks)
        W = fr["w"]
        for k, pc in enumerate(fr["pieces"]):
            whb, side, orig = tiles[pc["code"]]
            if orig is None:
                continue
            look = looks[pc["pal"]]
            bank = banks[pc["pal"]]
            fx = bool(pc["attr"] & 0x8000)
            fy = bool(pc["attr"] & 0x4000)
            dw, dh = pc["dw"], pc["dh"]
            new = bytearray(orig)
            for yy in range(dh):
                fy_row = pc["y"] + yy
                if fy_row >= fr["h"]:
                    continue
                for xx in range(dw):
                    fx_col = pc["x"] + xx
                    if fx_col >= W or own[fy_row * W + fx_col] != k:
                        continue          # a later piece owns it; keep the original
                    sx = dw - 1 - xx if fx else xx
                    sy = dh - 1 - yy if fy else yy
                    tgt = px[fx_col, fy_row]
                    cur = bank[new[sy * side + sx]]
                    # Keep the index that is already there when it renders the
                    # colour we want. CPS3 banks repeat colours, so a plain
                    # nearest-colour lookup happily returns a *different* index
                    # with identical output - visually a no-op, but it rewrites
                    # the tile bytes and costs the verbatim-reuse below.
                    if (tuple(cur) == tgt) or (not cur[3] and not tgt[3]):
                        continue
                    new[sy * side + sx] = look(tgt)
            prev = updated.get(pc["code"])
            if prev is not None and prev != new:
                conflicts += 1
            updated[pc["code"]] = new

    # rebuild the texture table: same codes, same wh bytes, new payloads
    blobs, reused = [], 0
    for code in range(ntex):
        whb, side, orig = tiles[code]
        if side is None:                  # unsupported wh: copy the original bytes
            tp = to_tex + tofs[code]
            end = to_tex + tofs[code + 1] if code + 1 < ntex else to_chd
            blobs.append(d[tp:end])
            reused += 1
            continue
        pix = updated.get(code, orig)
        if not args.reencode_all and pix == orig:
            # art unchanged: keep the original bytes. Our encoder is a plain
            # greedy parse and lands ~21% above whatever produced the shipped
            # stream, so re-encoding an untouched tile would grow the file (and
            # push to_chd) for nothing.
            blobs.append(orig_blob[code])
            reused += 1
            continue
        blobs.append(bytes((whb,)) + lz_encode(swizzle(pix, side)))

    # The shipped table packs blobs tightly - offsets are not even 2-aligned -
    # and never shares a blob between two codes (6648 codes, 6648 distinct
    # offsets), so laying them out back to back in code order reproduces the
    # original byte for byte when nothing has changed.
    table = bytearray(struct.pack("<%dI" % ntex, *([0] * ntex)))
    offs, cur = [], ntex * 4
    for b in blobs:
        offs.append(cur)
        cur += len(b)
    struct.pack_into("<%dI" % ntex, table, 0, *offs)
    newtex = bytes(table) + b"".join(blobs)

    old_len = to_chd - to_tex
    pad = 0
    if len(newtex) < old_len:             # pad so to_chd does not move
        pad = old_len - len(newtex)
        newtex += b"\x00" * pad
    out_data = d[:to_tex] + newtex + d[to_chd:]
    new_to_chd = to_tex + len(newtex)

    os.makedirs(args.out, exist_ok=True)
    outp = os.path.join(args.out, args.cg)
    open(outp, "wb").write(out_data)

    print("%s -> %s" % (args.cg, outp))
    print("  frames re-cut     %d (%d missing)" % (len(man["frames"]) - missing, missing))
    print("  tiles             %d total, %d re-encoded, %d kept verbatim"
          % (ntex, ntex - reused, reused))
    if conflicts:
        print("  ! %d tile(s) reached from pieces that disagree; last write wins"
              % conflicts)
    print("  texture table     %d -> %d bytes (%+d)"
          % (old_len, len(newtex), len(newtex) - old_len))
    print("  to_tex            0x%X (unchanged, trans table copied verbatim)" % to_tex)
    if new_to_chd == to_chd:
        print("  to_chd            0x%X (unchanged, %d pad bytes) - no engine change"
              % (to_chd, pad))
    else:
        print("  to_chd            0x%X -> 0x%X  ** update texgrpdat[] in "
              "texgroup_data.c **" % (to_chd, new_to_chd))
    print("  file              %d -> %d bytes" % (len(d), len(out_data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
