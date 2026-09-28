#!/usr/bin/env python3
"""Decode, upscale and repack the UI/menu screens into work/repacked/.

Run from the repository root. This is the screen equivalent of the stage route:
it ends with one .ppg per screen in work/repacked/, which is exactly what
tools/build_hd_test.py already picks up, so the screens land in the same
rebuilt archive as the stages. No engine change is needed -- these entries are
loaded by AFS index, so replacing them in the archive is the whole job.

    python3 tools/build_screens.py SF33RD.AFS          # full run
    python3 tools/build_screens.py SF33RD.AFS --only Select,TitleTM
    python3 tools/build_screens.py SF33RD.AFS --skip-upscale   # reuse work/upscaled
    python3 tools/build_screens.py SF33RD.AFS --dry-run        # plan and sizes only

Then rebuild the bundle as usual:

    python3 tools/build_hd_test.py

Everything it writes stays under work/ and is local-only.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decode_screens import OUT_ROOT, SCREENS

REPACKED = Path("work/repacked")
DECBUF_DEFINE = Path("3sx/src/main.c")


def engine_decbuf():
    """HD_PPG_DECBUF from the engine checkout, or None if it cannot be read."""
    try:
        for line in DECBUF_DEFINE.read_text().splitlines():
            if line.startswith("#define HD_PPG_DECBUF"):
                return int(line.split()[2], 0)
    except OSError:
        pass
    return None


def decode_buffer_needed(stems, scale):
    """Largest single chunk ppgSetupTexChunk_3rd will decompress, in bytes.

    It pulls height * pitch for one whole chunk, and direct32 makes pitch
    width * 4. This is the allocation that aborts with "Failed to allocate
    texture data buffer" when the engine's HD_PPG_DECBUF is too small.
    """
    worst, where = 0, None
    for stem, man, _ in stems:
        for c in man["chunks"]:
            if not c.get("decoded"):
                continue
            need = (c["height"] * scale) * (c["width"] * scale) * 4
            if need > worst:
                worst, where = need, "%s chunk %02d (%dx%d)" % (
                    stem, c["n"], c["width"], c["height"])
    return worst, where


def run(*args, **kw):
    print("+ " + " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True, **kw)


def direct32_safe(man):
    """Can this screen be rewritten as 32-bit direct colour?

    Only if nothing about it depends on a palette at draw time. direct32 bakes
    one palette into the pixels, but the engine picks a palette bank per draw
    call -- sc_sub.c has 18 njSetPaletteBankNumG(page, atr & 0x3F) sites, and
    scrscrn.ppg's palettes declare 32 and 260 banks. Baking one bank into the
    HUD is what turned the health bars, timer and portraits into rainbow mush.

    So the rule is conservative: an embedded palette or a trans table means the
    art is palette-driven and must stay indexed. That leaves the screens that
    are already 16bpp direct colour, which carry no palette at all.
    """
    if man.get("palette_chunks"):
        return False, "has %d embedded palette(s); the engine selects a bank per draw" % man["palette_chunks"]
    for c in man["chunks"]:
        if not c.get("decoded"):
            continue
        if c["transNums"]:
            return False, "chunk %02d has a trans table selecting palette banks" % c["n"]
        if c["pixel_raw"] & 3 != 2:
            return False, "chunk %02d is %s, not direct colour" % (c["n"], c["pixel_mode"])
    return True, None


def decoded_screens(only):
    """Screen stems that decoded, read back from their manifests."""
    stems = []
    for name in SCREENS:
        stem = name.split(".")[0]
        if only and stem not in only and name not in only:
            continue
        man = Path(OUT_ROOT) / stem / "manifest.json"
        if not man.exists():
            continue
        data = json.loads(man.read_text())
        if data.get("kind") != "screen":
            continue
        n = sum(1 for c in data["chunks"] if c.get("decoded"))
        if n:
            stems.append((stem, data, n))
    return stems


def estimate(stems, scale):
    """Uncompressed PSMCT32 texel bytes at this scale.

    An upper bound on the repack, not the result: build_tex zlib-compresses the
    payload (ppg_repack.py, codec 2), and UI art with flat colour compresses
    hard -- CapLogo's 4 MiB of texels came out as a 0.4 MiB chunk. Use it to
    sanity-check the order of magnitude before rebuilding a 1 GB archive; the
    real total is printed at the end of a full run.
    """
    total = 0
    for _, man, _ in stems:
        for c in man["chunks"]:
            if c.get("decoded"):
                total += (c["width"] * scale) * (c["height"] * scale) * 4
    return total


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("afs")
    ap.add_argument("--only", help="comma-separated screen stems")
    ap.add_argument("--scale", type=int, choices=(1, 2, 4), default=4)
    ap.add_argument("--skip-decode", action="store_true",
                    help="reuse the existing work/decoded/<stem> output")
    ap.add_argument("--skip-upscale", action="store_true",
                    help="reuse the existing work/upscaled/<stem> output")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and the projected sizes, change nothing")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    only = set(args.only.split(",")) if args.only else None

    if not args.skip_decode and not args.dry_run:
        cmd = [sys.executable, "tools/decode_screens.py", args.afs]
        if args.only:
            cmd += ["--only", args.only]
        run(*cmd)
        run(sys.executable, "tools/verify_screens.py", args.afs,
            *(["--only", args.only] if args.only else []))

    stems = decoded_screens(only)
    if not stems:
        raise SystemExit("No decoded screens. Run tools/decode_screens.py first; "
                         "SelectNew.ppg and Opening.ppg are still unresolved.")

    safe, skipped = [], []
    for entry in stems:
        ok, why = direct32_safe(entry[1])
        (safe if ok else skipped).append((entry, why))

    print("\n%-10s %7s %s" % ("screen", "chunks", "chunk sizes"))
    for (stem, man, n), _ in safe:
        sizes = sorted({"%dx%d" % (c["width"], c["height"])
                        for c in man["chunks"] if c.get("decoded")})
        print("%-10s %7d %s" % (stem, n, ", ".join(sizes)))
    if skipped:
        print("\nNot replaced -- palette-driven, and direct32 would bake one "
              "palette in:")
        for (stem, _, _), why in skipped:
            print("  %-10s %s" % (stem, why))
    if not safe:
        raise SystemExit("No screen can be safely replaced.")
    stems = [entry for entry, _ in safe]
    payload = estimate(stems, args.scale)
    print("\n%d screen(s), %d chunk(s); at %dx that is %.1f MiB of 32-bit texels "
          "before zlib -- the packed result is typically far smaller."
          % (len(stems), sum(n for _, _, n in stems), args.scale, payload / 2**20))
    need, where = decode_buffer_needed(stems, args.scale)
    have = engine_decbuf()
    print("Largest chunk decodes to %.1f MiB in one piece: %s."
          % (need / 2**20, where))
    if have is None:
        print("Could not read HD_PPG_DECBUF from %s; check it is at least that "
              "big or the engine aborts in ppgSetupTexChunk_3rd." % DECBUF_DEFINE)
    elif have < need:
        print("\nHD_PPG_DECBUF in %s is %.1f MiB, smaller than that. The engine "
              "will abort with 'Failed to allocate texture data buffer'.\n"
              "Raise it and rebuild, or run with a lower --scale."
              % (DECBUF_DEFINE, have / 2**20))
        if not args.dry_run:
            raise SystemExit("refusing to build screens the engine cannot load")
    else:
        print("HD_PPG_DECBUF is %.1f MiB: enough." % (have / 2**20))

    if args.dry_run:
        print("Dry run: nothing written.")
        return 0

    names = [stem for stem, _, _ in stems]
    run(sys.executable, "tools/export_for_upscale.py", *names)
    if not args.skip_upscale:
        run(sys.executable, "tools/upscale.py", "--scale", args.scale, *names)
    run(sys.executable, "tools/cut_pages.py", *names)
    run(sys.executable, "tools/check_upscaled.py", *names)
    # direct32 only: indexed mode needs a bgNN0.bin palette these screens have not
    # got, and caps at 2x anyway. The engine's GS alpha fix is required for it.
    run(sys.executable, "tools/ppg_repack.py", args.afs, "--mode", "direct32",
        "--stages", ",".join(names))

    made = [REPACKED / (man["ppg"]) for _, man, _ in stems]
    missing = [p for p in made if not p.exists()]
    if missing:
        raise SystemExit("repack did not produce: %s"
                         % ", ".join(p.name for p in missing))
    total = sum(p.stat().st_size for p in made)
    print("\n%d screen .ppg in %s, %.1f MiB total." % (len(made), REPACKED,
                                                       total / 2**20))
    print("They are picked up automatically by: python3 tools/build_hd_test.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
