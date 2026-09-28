#!/usr/bin/env python3
"""check_upscaled - final gate on the per-chunk tree before any repack.

By default this checks work/tiles/, the output of tools/cut_pages.py: one PNG
per pTEX chunk, cut back off the upscaled pages. Point it elsewhere with --root
(work/upscaled still works for a legacy tile-at-a-time upscale, and work/decoded
checks the 1x source).

Checks per stage: exact filename set vs. the manifest, PNG colour type/bit depth,
integer scale, one consistent scale factor across the whole stage, and dimensions
that are a multiple of 16 (required by the 16px-unit header fields).
"""
import argparse
import json
import os
import struct
import sys

DEC = os.path.join("work", "decoded")
TILES = os.path.join("work", "tiles")
UPS = os.path.join("work", "upscaled")

COLOR_TYPE = {0: "grey", 2: "RGB", 3: "indexed", 4: "grey+A", 6: "RGBA"}


def ihdr(path):
    with open(path, "rb") as f:
        head = f.read(33)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    w, h = struct.unpack(">II", head[16:24])
    depth, ctype = head[24], head[25]
    return w, h, depth, ctype


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stems", nargs="*", help="stage stems (default: all present)")
    ap.add_argument("--root", help="chunk tree to check (default work/tiles, "
                                   "falling back to work/upscaled)")
    args = ap.parse_args()

    root = args.root or (TILES if os.path.isdir(TILES) else UPS)
    if not os.path.isdir(root):
        sys.exit("no chunk tree at %s -- run tools/cut_pages.py first" % root)
    stems = args.stems or sorted(
        d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    problems = 0
    print("checking %s" % root)
    print("%-9s %6s %6s %-13s %-9s %s"
          % ("stage", "chunks", "scale", "example", "format", "status"))
    for stem in stems:
        man = json.load(open(os.path.join(DEC, stem, "manifest.json")))
        src = os.path.join(root, stem)
        is_char = "frames" in man
        if is_char:              # character: frame_NNN.png, w/h per frame
            expect = {"frame_%03d.png" % f["n"]: {"width": f["w"], "height": f["h"]}
                      for f in man["frames"]}
        else:                    # stage: chunk_NN.png
            expect = {"chunk_%02d.png" % c["n"]: c for c in man["chunks"]}
        # work/decoded also holds the decoders' own sidecars: the manifest and
        # decode_stages' assembled/layer composites. None is a repack input, so
        # they are not "unexpected" -- but anything else still is.
        have = {n for n in os.listdir(src)
                if n != "manifest.json"
                and not n.startswith("assembled")
                and not n.startswith("layer")}
        notes = []

        missing = sorted(set(expect) - have)
        extra = sorted(have - set(expect))
        if missing:
            notes.append("missing %d (%s%s)" % (len(missing), ", ".join(missing[:3]),
                                                "..." if len(missing) > 3 else ""))
        if extra:
            notes.append("unexpected %d (%s%s)" % (len(extra), ", ".join(extra[:3]),
                                                   "..." if len(extra) > 3 else ""))

        scales, fmts, sample = {}, set(), None
        for name, c in expect.items():
            p = os.path.join(src, name)
            if not os.path.exists(p):
                continue
            try:
                w, h, depth, ctype = ihdr(p)
            except ValueError as exc:
                notes.append("%s: %s" % (name, exc))
                continue
            fmts.add("%s%d" % (COLOR_TYPE.get(ctype, "?%d" % ctype), depth))
            if sample is None:
                sample = "%dx%d" % (w, h)
            if w % c["width"] or h % c["height"]:
                notes.append("%s: %dx%d not an integer multiple of %dx%d"
                             % (name, w, h, c["width"], c["height"]))
                continue
            sw, sh = w // c["width"], h // c["height"]
            if sw != sh:
                notes.append("%s: non-uniform scale %dx horizontal, %dx vertical"
                             % (name, sw, sh))
                continue
            # multiple-of-16 is a STAGE constraint only: pTEX width/height are in
            # 16px units. Character frames are bounding boxes, not tile-aligned.
            if not is_char and (w % 16 or h % 16):
                notes.append("%s: %dx%d not a multiple of 16" % (name, w, h))
            scales.setdefault(sw, []).append(name)

        if len(scales) > 1:
            bits = ["%dx:%d files" % (k, len(v)) for k, v in sorted(scales.items())]
            odd = min(scales.items(), key=lambda kv: len(kv[1]))
            notes.append("mixed scales (%s); e.g. %s is %dx"
                         % (", ".join(bits), odd[1][0], odd[0]))
        if fmts - {"RGBA8"}:
            notes.append("non-RGBA8 files present: %s" % ", ".join(sorted(fmts)))

        scale = list(scales)[0] if len(scales) == 1 else 0
        status = "OK" if not notes else "FAIL"
        problems += bool(notes)
        print("%-9s %6d %5dx %-13s %-9s %s"
              % (stem, len(have), scale, sample or "-",
                 "/".join(sorted(fmts)) or "-", status))
        for n in notes:
            print("           ! " + n)
    print("\n%d stage(s) checked, %d with problems" % (len(stems), problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
