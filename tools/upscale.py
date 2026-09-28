#!/usr/bin/env python3
"""upscale - upscale work/for_upscale/ into work/upscaled/, offline.

Input is whatever export_for_upscale.py staged: whole stage *pages* (an 8x4 grid
of 128px tiles, cropped to the occupied slots), the handful of slotless chunks
that have no page to belong to, and character frames. Pages are the point - a
tile-at-a-time pass has to invent every 128px edge from nothing, so smooth areas
come back with a seam lattice. cut_pages.py cuts the pages back apart afterwards.

Model: realesr-animevideov3-x4 (Real-ESRGAN anime-video v3), run through
upscayl-bin's ncnn/Vulkan backend. See docs/upscaling-notes.md for why.

The round-trip contract in work/for_upscale/README.txt requires alpha 0 to stay
alpha 0, so the RGB and the alpha are handled separately:

  1. Colour bleed - the source chunks store black (0,0,0) under alpha-0 texels.
     Feeding that to the network drags dark halos across every silhouette edge,
     and the game's shader would show them on the surviving opaque texels. So
     the opaque colour is flood-expanded a few pixels into the transparent
     region first, and the network only ever sees an opaque image.
  2. The alpha mask is upscaled on its own (bicubic, then re-thresholded at 128)
     so the result stays strictly binary 0/255: smoother silhouettes than
     nearest-neighbour, but never a partially transparent texel.

Both nothing-but-transparent chunks and fully opaque ones are handled; the
former never reach the upscaler at all.

Stages and character frames default to 4x. The HD character sidecar loader in
3SX keeps geometry in logical pixels while sampling a higher resolution atlas.
The network runs at native 4x; --scale can request a downsampled output.

upscayl-bin is found via $UPSCAYL_BIN, then $PATH, then the usual install
locations; the model directory via $UPSCAYL_MODELS.

Usage:
    tools/upscale.py                  # everything, skipping finished folders
    tools/upscale.py stage03 pl02     # named folders only
    tools/upscale.py --force          # redo folders already in work/upscaled/
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from PIL import Image, ImageFilter, ImageMath

DEC = os.path.join("work", "decoded")
SRC = os.path.join("work", "for_upscale")
DST = os.path.join("work", "upscaled")

MODEL = "realesr-animevideov3-x4"
MODEL_DIR = os.environ.get(
    "UPSCAYL_MODELS", os.path.expanduser("~/Downloads/custom-models-main/models"))
NET_SCALE = 4          # the model's native scale; the network always runs here
STAGE_SCALE = 4
CHAR_SCALE = 4
BLEED_ITERS = 6

BIN_CANDIDATES = [
    os.path.expanduser("~/Programs/bin/upscayl-bin"),
    "/opt/Upscayl/resources/bin/upscayl-bin",
    "/usr/lib/upscayl/resources/bin/upscayl-bin",
]


def find_bin():
    """$UPSCAYL_BIN, then PATH, then the usual install locations."""
    env = os.environ.get("UPSCAYL_BIN")
    if env:
        if not os.path.isfile(env):
            sys.exit("UPSCAYL_BIN=%s does not exist" % env)
        return env
    onpath = shutil.which("upscayl-bin")
    if onpath:
        return onpath
    for c in BIN_CANDIDATES:
        if os.path.isfile(c):
            return c
    sys.exit("upscayl-bin not found: set UPSCAYL_BIN or put it on PATH")


def bleed(rgb, mask):
    """Expand the opaque colour outwards so nothing dark sits next to an edge."""
    r, g, b = rgb.split()
    m = mask
    for _ in range(BLEED_ITERS):
        mb = m.filter(ImageFilter.BoxBlur(1))
        bands = []
        for ch in (r, g, b):
            # premultiply, blur, divide back out -> average of the known neighbours
            pre = ImageMath.eval("a*c/255", a=ch, c=m).convert("L")
            pb = pre.filter(ImageFilter.BoxBlur(1))
            filled = ImageMath.eval("convert(min(p*255/max(q,1), 255), 'L')",
                                    p=pb, q=mb)
            bands.append(Image.composite(ch, filled, m))
        r, g, b = bands
        m = mb.point(lambda v: 255 if v else 0)
    return Image.merge("RGB", (r, g, b))


def upscale_alpha(mask, scale):
    big = mask.resize((mask.width * scale, mask.height * scale), Image.BICUBIC)
    return big.point(lambda v: 255 if v >= 128 else 0)


def is_character(stem):
    """Characters are frame_NNN.png folders; the manifest is the authority."""
    man = os.path.join(DEC, stem, "manifest.json")
    if not os.path.exists(man):
        sys.exit("no manifest for %s -- run tools/decode_stages.py first" % stem)
    with open(man) as f:
        return "frames" in json.load(f)


def run_upscayl(indir, outdir):
    cmd = [find_bin(), "-i", indir, "-o", outdir,
           "-m", MODEL_DIR, "-n", MODEL, "-z", str(NET_SCALE), "-s", str(NET_SCALE),
           "-f", "png"]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        sys.exit("upscayl-bin failed (%d):\n%s"
                 % (proc.returncode, proc.stderr.decode("utf-8", "replace")))


def do_stage(stem, scale, force, prune):
    src = os.path.join(SRC, stem)
    dst = os.path.join(DST, stem)
    names = sorted(n for n in os.listdir(src) if n.endswith(".png"))
    os.makedirs(dst, exist_ok=True)

    # Anything in work/upscaled that is not in the current input set is left over
    # from an older contract -- most likely per-tile chunk_NN.png from before
    # pages existed. cut_pages.py drives everything off the manifest plan so it
    # will not pick these up, but they are dead weight and confusing provenance.
    # Deleting is hours of GPU time, so say so and let the user decide.
    stale = sorted(n for n in os.listdir(dst) if n not in set(names))
    if stale and prune:
        for n in stale:
            os.remove(os.path.join(dst, n))
        print("%-9s pruned %d stale file(s)" % (stem, len(stale)))
        stale = []

    def complete_at_scale():
        if not names:
            return False
        for name in names:
            try:
                with Image.open(os.path.join(src, name)) as original, Image.open(os.path.join(dst, name)) as result:
                    if result.size != (original.width * scale, original.height * scale) or result.mode != "RGBA":
                        return False
                    result.load()
            except (OSError, ValueError):
                return False
        return True

    if not force and complete_at_scale():
        print("%-9s skip (already complete%s)"
              % (stem, ", %d stale" % len(stale) if stale else ""))
        return

    tmp = tempfile.mkdtemp(prefix="upscale_%s_" % stem)
    prep, out = os.path.join(tmp, "in"), os.path.join(tmp, "out")
    os.makedirs(prep)
    os.makedirs(out)
    masks, blank = {}, []
    try:
        for name in names:
            im = Image.open(os.path.join(src, name)).convert("RGBA")
            mask = im.getchannel("A").point(lambda v: 255 if v >= 128 else 0)
            masks[name] = mask
            if not mask.getbbox():          # nothing drawn at all
                blank.append(name)
                continue
            rgb = im.convert("RGB")
            if mask.histogram()[0]:         # has transparent texels
                rgb = bleed(rgb, mask)
            rgb.save(os.path.join(prep, name))

        if len(blank) < len(names):
            run_upscayl(prep, out)

        for name in names:
            mask = masks[name]
            big_a = upscale_alpha(mask, scale)
            if name in blank:
                res = Image.new("RGBA", big_a.size, (0, 0, 0, 0))
            else:
                res = Image.open(os.path.join(out, name)).convert("RGB")
                net = (mask.width * NET_SCALE, mask.height * NET_SCALE)
                if res.size != net:
                    sys.exit("%s/%s: got %s, expected %s" % (stem, name, res.size, net))
                if res.size != big_a.size:      # NET_SCALE -> scale
                    res = res.resize(big_a.size, Image.LANCZOS)
                res = res.convert("RGBA")
                res.putalpha(big_a)
            res.save(os.path.join(dst, name))
        print("%-9s %3d file(s) %dx -> %s (%d blank%s)"
              % (stem, len(names), scale, dst, len(blank),
                 ", %d stale left in place; --prune to remove" % len(stale)
                 if stale else ""))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stages", nargs="*", help="stage stems (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="re-upscale stages already present in work/upscaled/")
    ap.add_argument("--scale", type=int, choices=(1, 2, 3, 4),
                    help="override the scale for the folders named (default: "
                         "%d for stages, %d for characters)" % (STAGE_SCALE, CHAR_SCALE))
    ap.add_argument("--prune", action="store_true",
                    help="delete files in work/upscaled/<stage>/ that are not part "
                         "of the current input set (e.g. per-tile chunk_NN.png from "
                         "before pages existed)")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        sys.exit("run this from the repo root: %s not found -- run "
                 "tools/export_for_upscale.py first" % SRC)
    if not os.path.exists(os.path.join(MODEL_DIR, MODEL + ".param")):
        sys.exit("model %s not found in %s (set UPSCAYL_MODELS)" % (MODEL, MODEL_DIR))
    find_bin()

    stems = args.stages or sorted(
        d for d in os.listdir(SRC) if os.path.isdir(os.path.join(SRC, d)))
    for stem in stems:
        scale = args.scale or (CHAR_SCALE if is_character(stem) else STAGE_SCALE)
        do_stage(stem, scale, args.force, args.prune)


if __name__ == "__main__":
    main()
