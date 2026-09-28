#!/usr/bin/env python3
"""stage_pages - the page plan for a stage, and lossless assemble/cut at any scale.

Why pages exist
---------------
The engine never draws a stage tile on its own. Bg_Disp walks an 8-wide, 128px
grid and draws chunk `base + ((y >> 7) << 3) + (x >> 7)` at each cell
(bg.c:623-625), so every slotted chunk has exactly one fixed neighbour on each
side. Handing an upscaler one 128x128 tile at a time throws that away: the
network sees a hard cut where the art actually continues, and the four tiles
meeting at a grid corner are each invented independently. On a smooth gradient
(stage10's sky, stage05's water) that shows up as a seam lattice.

So the upscale unit is the *page* - one full grid of tiles, cropped to the
occupied slots - and the tiles are cut back out afterwards on the same grid.
The .ppg is unaffected: a chunk is identified by its position in the stream, and
cutting restores exactly the same per-chunk images, just larger.

Not every chunk has a slot. The rewrite/extra chunks of stages 3, 7, 8, 14, 18
and 19 are runtime substitutions with no fixed grid cell (see
docs/format-notes.md), and stage07's 3 Akane chunks are 256x256 rather than
128x128. Those stay loose and are upscaled on their own, as before. Stage 10 is
the opposite case: its 96 "rewrite" chunks *are* slotted - they are three extra
animated pages of BG1 - so they get page treatment too.

Scale
-----
Everything is parameterised by an integer scale, and `cut` derives the scale
from the page image it is handed. The same code therefore cuts a 1x page back
into the original tiles (a lossless self-test, see cut_pages.py --verify-against)
and a 4x page into 4x tiles.
"""
import re

from PIL import Image

TILE = 128                # bg.c:623-625, one grid cell
GRID_W = 8                # (y >> 7) << 3
GRID_H = 4                # 32 slots / 8

_LAYER = re.compile(r"^layer(\d+)$")


def page_plan(man):
    """Split a stage manifest into pages and loose chunks.

    Returns (pages, loose).

    pages: list of dicts, ordered by (layer, page)
        name        PNG basename, e.g. "page_L1_p2"
        layer, page the BG layer and, for ANIM_PAGES stages, which page of it
        col0, row0  crop origin in tile units (the page is cropped to the
                    occupied slots; a mask like 0x7E00007E leaves whole rows
                    empty and there is no point upscaling them)
        cols, rows  crop size in tile units
        w, h        page size in pixels at 1x
        cells       [(col, row, chunk_n)], col/row relative to the crop origin

    loose: [chunk_n] for every chunk with no grid slot, in stream order.
    """
    groups, loose = {}, []
    for c in man.get("chunks", []):
        m = _LAYER.match(c.get("role") or "")
        slot = c.get("slot")
        if m is None or slot is None:
            loose.append(c["n"])
            continue
        if (c["width"], c["height"]) != (TILE, TILE):
            # place() refuses these too; keep them loose rather than guess.
            loose.append(c["n"])
            continue
        groups.setdefault((int(m.group(1)), c.get("page", 0)), []).append(
            (slot % GRID_W, slot // GRID_W, c["n"]))

    pages = []
    for (layer, page), cells in sorted(groups.items()):
        col0 = min(c[0] for c in cells)
        row0 = min(c[1] for c in cells)
        cols = max(c[0] for c in cells) - col0 + 1
        rows = max(c[1] for c in cells) - row0 + 1
        pages.append({
            "name": "page_L%d_p%d" % (layer, page),
            "layer": layer, "page": page,
            "col0": col0, "row0": row0, "cols": cols, "rows": rows,
            "w": cols * TILE, "h": rows * TILE,
            "cells": [(c[0] - col0, c[1] - row0, c[2]) for c in sorted(
                cells, key=lambda c: (c[1], c[0]))],
        })
    return pages, loose


def assemble(pg, tiles, scale=1):
    """Build one page image from its tiles. `tiles` maps chunk n -> PIL image."""
    side = TILE * scale
    im = Image.new("RGBA", (pg["w"] * scale, pg["h"] * scale), (0, 0, 0, 0))
    for col, row, n in pg["cells"]:
        t = tiles[n]
        if t.size != (side, side):
            raise ValueError("chunk %d is %dx%d, expected %dx%d at scale %dx"
                             % (n, t.width, t.height, side, side, scale))
        im.paste(t, (col * side, row * side))
    return im


def page_scale(pg, im):
    """Integer scale of a page image, validated against the plan."""
    if im.width % pg["w"] or im.height % pg["h"]:
        raise ValueError("%s is %dx%d, not an integer multiple of %dx%d"
                         % (pg["name"], im.width, im.height, pg["w"], pg["h"]))
    sx, sy = im.width // pg["w"], im.height // pg["h"]
    if sx != sy:
        raise ValueError("%s scales %dx horizontally but %dx vertically"
                         % (pg["name"], sx, sy))
    if sx < 1:
        raise ValueError("%s is smaller than 1x" % pg["name"])
    return sx


def cut(pg, im):
    """Cut a page image back into its tiles.

    Returns (tiles, scale) where tiles maps chunk n -> PIL image of side
    TILE * scale. The scale comes from the image, so this is the same call
    whether the page is 1x (self-test) or upscaled.
    """
    scale = page_scale(pg, im)
    side = TILE * scale
    tiles = {}
    for col, row, n in pg["cells"]:
        x, y = col * side, row * side
        tiles[n] = im.crop((x, y, x + side, y + side))
    return tiles, scale
