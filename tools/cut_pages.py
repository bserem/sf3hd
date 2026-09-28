#!/usr/bin/env python3
"""cut_pages - cut upscaled stage pages back into per-chunk PNGs.

    work/upscaled/<stage>/page_L1_p0.png  ->  work/tiles/<stage>/chunk_NN.png
    work/upscaled/<stage>/chunk_NN.png    ->  work/tiles/<stage>/chunk_NN.png

work/tiles/ is what ppg_repack.py consumes: one PNG per pTEX chunk, in the
manifest's numbering, all at one integer scale.

The cut is the exact inverse of the assembly in export_for_upscale.py - the same
plan from tools/stage_pages.py, the same crop origins - so at 1x it reproduces
work/decoded/<stage>/chunk_NN.png byte for byte. That is worth checking whenever
the plan changes:

    python3 tools/export_for_upscale.py
    python3 tools/cut_pages.py --src work/for_upscale --out work/tiles_1x \\
                               --verify-against work/decoded

The scale is never passed in; it is read off each page image and cross-checked
against the plan, so the same code path handles the 1x self-test and a 4x (or
2x, or 8x) upscale. Every page and every loose chunk in a stage must agree on
one factor, which is also what ppg_repack.py requires.
"""
import argparse
import json
import os
import shutil
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage_pages import cut, page_plan                    # noqa: E402

DEC = os.path.join("work", "decoded")
UPS = os.path.join("work", "upscaled")
OUT = os.path.join("work", "tiles")


def cut_stage(stem, src, dst, man, verify):
    pages, loose = page_plan(man)
    scale, problems, written = None, [], 0

    def note_scale(where, s):
        nonlocal scale
        if scale is None:
            scale = s
        elif s != scale:
            problems.append("%s is %dx but the stage is %dx" % (where, s, scale))
            return False
        return True

    def emit(n, im):
        nonlocal written
        p = os.path.join(dst, "chunk_%02d.png" % n)
        im.save(p)
        written += 1
        if verify:
            ref = os.path.join(verify, stem, "chunk_%02d.png" % n)
            if not os.path.isfile(ref):
                problems.append("chunk %d: no reference at %s" % (n, ref))
            elif Image.open(ref).convert("RGBA").tobytes() != im.convert("RGBA").tobytes():
                problems.append("chunk %d: pixels differ from %s" % (n, ref))

    for pg in pages:
        p = os.path.join(src, pg["name"] + ".png")
        if not os.path.isfile(p):
            problems.append("missing page %s.png" % pg["name"])
            continue
        try:
            tiles, s = cut(pg, Image.open(p).convert("RGBA"))
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if not note_scale(pg["name"], s):
            continue
        for n, im in tiles.items():
            emit(n, im)

    for n in loose:
        name = "chunk_%02d.png" % n
        p = os.path.join(src, name)
        if not os.path.isfile(p):
            problems.append("missing loose %s" % name)
            continue
        im = Image.open(p).convert("RGBA")
        mc = next(c for c in man["chunks"] if c["n"] == n)
        if im.width % mc["width"] or im.height % mc["height"]:
            problems.append("%s is %dx%d, not an integer scale of %dx%d"
                            % (name, im.width, im.height, mc["width"], mc["height"]))
            continue
        sx, sy = im.width // mc["width"], im.height // mc["height"]
        if sx != sy:
            problems.append("%s scales %dx by %dx" % (name, sx, sy))
            continue
        if not note_scale(name, sx):
            continue
        emit(n, im)

    expect = len(man["chunks"])
    if written != expect:
        problems.append("produced %d of %d chunks" % (written, expect))
    return len(pages), len(loose), scale, written, problems


def copy_char(stem, src, dst, man, link):
    n = 0
    for fr in man["frames"]:
        name = "frame_%03d.png" % fr["n"]
        s, d = os.path.join(src, name), os.path.join(dst, name)
        if not os.path.isfile(s):
            continue
        if os.path.exists(d):
            os.remove(d)
        (os.link if link else shutil.copyfile)(s, d)
        n += 1
    return n


def copy_screen(stem, src, dst, man):
    """Screens have no pages: copy each chunk, enforcing one integer scale."""
    scale, written, problems = None, 0, []
    for c in man["chunks"]:
        if not c.get("decoded"):
            continue
        name = "chunk_%02d.png" % c["n"]
        p = os.path.join(src, name)
        if not os.path.isfile(p):
            problems.append("missing %s" % name)
            continue
        im = Image.open(p).convert("RGBA")
        if im.width % c["width"] or im.height % c["height"]:
            problems.append("%s is %dx%d, not an integer scale of %dx%d"
                            % (name, im.width, im.height, c["width"], c["height"]))
            continue
        sx, sy = im.width // c["width"], im.height // c["height"]
        if sx != sy:
            problems.append("%s scales %dx by %dx" % (name, sx, sy))
            continue
        if scale is None:
            scale = sx
        elif sx != scale:
            problems.append("%s is %dx but the screen is %dx" % (name, sx, scale))
            continue
        im.save(os.path.join(dst, name))
        written += 1
    expect = sum(1 for c in man["chunks"] if c.get("decoded"))
    if written != expect:
        problems.append("produced %d of %d chunks" % (written, expect))
    return scale, written, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stems", nargs="*", help="decoded stems (default: all)")
    ap.add_argument("--src", default=UPS, help="page tree (default work/upscaled)")
    ap.add_argument("--out", default=OUT, help="chunk tree (default work/tiles)")
    ap.add_argument("--verify-against", metavar="DIR",
                    help="byte-compare each cut chunk against DIR/<stage>/chunk_NN.png "
                         "(use work/decoded for the 1x self-test)")
    ap.add_argument("--link", action="store_true",
                    help="hardlink character frames instead of copying")
    args = ap.parse_args()

    stems = args.stems or sorted(os.listdir(DEC))
    bad = 0
    print("%-9s %5s %5s %5s %6s %s"
          % ("stage", "pages", "loose", "scale", "chunks", "status"))
    for stem in stems:
        manp = os.path.join(DEC, stem, "manifest.json")
        src = os.path.join(args.src, stem)
        if not os.path.isfile(manp) or not os.path.isdir(src):
            continue
        man = json.load(open(manp))
        dst = os.path.join(args.out, stem)
        os.makedirs(dst, exist_ok=True)
        if "frames" in man:
            n = copy_char(stem, src, dst, man, args.link)
            print("%-9s %5s %5s %5s %6d %s"
                  % (stem, "-", "-", "-", n, "OK (character frames copied)"))
            continue
        if man.get("kind") == "screen":
            scale, n, problems = copy_screen(stem, src, dst, man)
            bad += bool(problems)
            print("%-9s %5s %5s %5s %6d %s"
                  % (stem, "-", "-", "%dx" % scale if scale else "?", n,
                     "OK (screen chunks copied)" if not problems else "FAIL"))
            for p in problems[:6]:
                print("           ! " + p)
            continue
        npages, nloose, scale, written, problems = cut_stage(
            stem, src, dst, man, args.verify_against)
        status = "OK" if not problems else "FAIL"
        if problems:
            bad += 1
        print("%-9s %5d %5d %5s %6d %s"
              % (stem, npages, nloose,
                 "%dx" % scale if scale else "?", written, status))
        for p in problems[:6]:
            print("           ! " + p)
        if len(problems) > 6:
            print("           ! ... and %d more" % (len(problems) - 6))

    print("\n%s" % ("all stages cut cleanly" if not bad
                    else "%d stage(s) FAILED" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
