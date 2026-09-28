#!/usr/bin/env python3
"""export_for_upscale - stage the upscaler's input tree.

Stages are exported as *pages*, not tiles:

    work/decoded/<stage>/chunk_NN.png  ->  work/for_upscale/<stage>/page_L0_p0.png
                                           work/for_upscale/<stage>/page_L1_p0.png
                                           ...
                                           work/for_upscale/<stage>/chunk_NN.png

A page is one 8x4 grid of 128px tiles, cropped to the slots the stage's mask
actually uses - the unit the engine draws (bg.c:623-625). Upscaling a whole page
gives the network the real neighbouring art instead of a hard cut at every 128px
boundary, which is what produced a seam lattice across smooth areas when each
tile was upscaled alone. See tools/stage_pages.py for the reasoning and the plan.

Chunks with no grid slot - the rewrite/extra substitution tiles of stages 3, 7,
8, 14, 18 and 19, plus stage07's three 256x256 Akane chunks - have no
neighbours to speak of and are still exported one file per chunk, as chunk_NN.png.

Characters are unchanged: one file per animation frame, frame_NNN.png.

After upscaling, tools/cut_pages.py cuts the pages back into chunk_NN.png at the
same factor and writes work/tiles/, which is what ppg_repack.py consumes.
"""
import argparse
import json
import os
import shutil
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage_pages import assemble, page_plan               # noqa: E402

DEC = os.path.join("work", "decoded")
OUT = os.path.join("work", "for_upscale")

README = """work/for_upscale - input tree for an external upscaler.

Layout
    work/for_upscale/<stage>/page_L<layer>_p<page>.png   (stage backgrounds)
    work/for_upscale/<stage>/chunk_NN.png                (slotless chunks only)
    work/for_upscale/<char>/frame_NNN.png                (character animations)

Pages are whole 8x4 grids of 128px tiles, cropped to the occupied slots. They
are the unit the engine draws, so they are the unit that should be upscaled: a
tile-at-a-time pass has to invent every 128px edge from nothing and leaves a
visible seam lattice. tools/cut_pages.py cuts them back apart afterwards.

Round-trip contract
 1. Upscale every PNG and write the result to
        work/upscaled/<stage>/<same filename>
    Do not rename, reorder, add or drop files.
 2. Use the SAME integer scale factor for every file within a folder.
    Pages differ in size from each other and from the loose chunks; they just
    all need the same MULTIPLIER. The multiplier may differ BETWEEN folders:
    both stages and character folders default to 4x, and `upscale.py --scale`
    can override a run.
 3. Keep RGBA8. Alpha 0 means "not drawn" and must stay 0; the game's
    fragment shader discards alpha-0 texels, so any upscaler that bleeds
    colour into transparent areas will produce visible halos.
 4. Nearest-neighbour-safe: 3sx samples every texture with GL_NEAREST, so
    the PNG you hand back is exactly what is displayed. Do any smoothing
    in your upscaler; the engine will not smooth for you.
 5. Then run:
        python3 tools/cut_pages.py          # work/upscaled -> work/tiles
    which re-cuts the pages on the 128*scale grid, copies the loose chunks
    through, and refuses anything whose size is not an exact integer scale.

Per-stage metadata (header fields, trans records, grid slots, palette entry)
lives in work/decoded/<stage>/manifest.json and is what ppg_repack.py uses to
rebuild the .ppg. Do not edit it.

Everything here is derived from your own SF33RD.AFS and stays local.
"""


def export_stage(stem, src, dst, man, link):
    pages, loose = page_plan(man)
    need = {n for pg in pages for _, _, n in pg["cells"]}
    tiles = {}
    for n in sorted(need):
        p = os.path.join(src, "chunk_%02d.png" % n)
        if not os.path.isfile(p):
            print("  missing %s/chunk_%02d.png" % (stem, n))
            return 0, 0
        tiles[n] = Image.open(p).convert("RGBA")
    for pg in pages:
        assemble(pg, tiles).save(os.path.join(dst, pg["name"] + ".png"))
    for n in loose:
        name = "chunk_%02d.png" % n
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if not os.path.isfile(s):
            print("  missing %s/%s" % (stem, name))
            continue
        (os.link if link else shutil.copyfile)(s, d)
    return len(pages), len(loose)


def export_char(stem, src, dst, man, link):
    n = 0
    for fr in man["frames"]:
        name = "frame_%03d.png" % fr["n"]
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if not os.path.isfile(s):
            print("  missing %s/%s" % (stem, name))
            continue
        (os.link if link else shutil.copyfile)(s, d)
        n += 1
    return n


def export_screen(stem, src, dst, man, link):
    """UI screens: each pTEX is its own texture, so there is no page to build.

    Copying each chunk through is the correct upscale unit here. Adjacent
    chunks of one screen image are upscaled independently, so check the seams
    before trusting a screen that is visibly tiled.
    """
    n = 0
    for c in man["chunks"]:
        if not c.get("decoded"):
            continue
        name = "chunk_%02d.png" % c["n"]
        s_, d = os.path.join(src, name), os.path.join(dst, name)
        if not os.path.isfile(s_):
            print("  missing %s/%s" % (stem, name))
            continue
        (os.link if link else shutil.copyfile)(s_, d)
        n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stems", nargs="*", help="decoded stems (default: all)")
    ap.add_argument("--link", action="store_true",
                    help="hardlink loose chunks/frames instead of copying")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "README.txt"), "w") as f:
        f.write(README)

    stems = args.stems or sorted(os.listdir(DEC))
    total = 0
    for stem in stems:
        src = os.path.join(DEC, stem)
        manp = os.path.join(src, "manifest.json")
        if not os.path.isfile(manp):
            continue
        man = json.load(open(manp))
        dst = os.path.join(OUT, stem)
        # The layout changed when pages were introduced; drop anything stale so
        # the upscaler never sees a mix of the two contracts.
        if os.path.isdir(dst):
            shutil.rmtree(dst)
        os.makedirs(dst)
        if "frames" in man:
            n = export_char(stem, src, dst, man, args.link)
            print("%-9s %3d frames" % (stem, n))
        elif man.get("kind") == "screen":
            n = export_screen(stem, src, dst, man, args.link)
            print("%-9s %3d screen chunk(s)" % (stem, n))
        else:
            npages, nloose = export_stage(stem, src, dst, man, args.link)
            n = npages + nloose
            print("%-9s %3d pages + %3d loose chunk(s)  (%d chunks in)"
                  % (stem, npages, nloose, len(man["chunks"])))
        total += n
    print("\n%d PNGs in %s/" % (total, OUT))


if __name__ == "__main__":
    main()
