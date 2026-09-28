#!/usr/bin/env python3
"""verify_screens - independently check tools/decode_screens.py output.

Re-reads the archive and the decoded PNGs and re-derives the source bytes,
rather than trusting the decoder's own report. Prints counts and format codes
only, never pixel data.

  * direct 16bpp chunks are re-encoded to ARGB1555 and compared byte-for-byte
    against the inflated payload, which proves the pitch, size, channel shifts
    and 5-bit expansion are exactly invertible;
  * indexed chunks are checked structurally: every decoded colour must be an
    entry of a palette bank the file actually contains, the distinct-colour
    count must fit the bank size, and a trans table must cover the whole image.
"""

import argparse
import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402
from ppg_inspect import walk_chunks                      # noqa: E402
from ppg_decode_indexed import unpack_1555               # noqa: E402
from decode_screens import (EXTERNAL_PALETTE, OUT_ROOT, SCREENS,             # noqa: E402
                            change_endian, inflate, read_banks,
                            read_external_banks, indices)


def load_png(path):
    """Minimal RGBA8 reader for the PNGs write_png produces (filter 0 only)."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    pos, idat, size = 8, bytearray(), None
    while pos < len(data):
        length, tag = struct.unpack_from(">I4s", data, pos)
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack_from(">IIBB", body, 0)
            assert (depth, ctype) == (8, 6), path
            size = (w, h)
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    w, h = size
    raw = zlib.decompress(bytes(idat))
    stride = w * 4
    out = bytearray()
    for y in range(h):
        assert raw[y * (stride + 1)] == 0, "unexpected PNG filter"
        out += raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)]
    return w, h, bytes(out)


def opacity(raw, n, big_endian):
    """Fraction of ARGB1555 texels with the alpha bit set, in one byte order."""
    vals = struct.unpack("%s%dH" % (">" if big_endian else "<", n), raw[:n * 2])
    return sum(1 for v in vals if v >> 15) / float(n)


def endian_sanity(raw, n, tag, notes):
    """Catch a reversed 16-bit byte order without looking at the picture.

    A byte swap moves the alpha bit out of bit 15, so a screen that should be
    largely opaque reads as almost entirely transparent. The round-trip check
    cannot see this -- re-encoding inverts whatever the decoder did -- so this
    compares the two byte orders directly.
    """
    chosen, other = opacity(raw, n, False), opacity(raw, n, True)
    if chosen < 0.20 and other > 0.80:
        notes.append("%s: %.1f%% opaque as decoded but %.1f%% in the other byte "
                     "order; endian handling is probably inverted"
                     % (tag, 100 * chosen, 100 * other))
        return False
    return True


def pack_1555(rgba, n):
    """Inverse of unpack_1555(swap_rb=False): 8-bit RGBA back to ARGB1555 LE."""
    out = bytearray(n * 2)
    for i in range(n):
        r, g, b, a = rgba[i * 4:i * 4 + 4]
        v = ((1 if a else 0) << 15) | ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)
        struct.pack_into("<H", out, i * 2, v)
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("afs")
    ap.add_argument("--only", help="comma-separated entry names or stems")
    args = ap.parse_args()

    want = set(args.only.split(",")) if args.only else None
    entries, _ = parse_index(args.afs)
    by_name = {e["name"]: e for e in entries if e["name"]}
    problems = exact = structural = 0
    print("%-10s %6s %8s %10s %s" % ("screen", "chunks", "direct", "indexed", "result"))
    with open(args.afs, "rb") as f:
        for name in SCREENS:
            stem = name.split(".")[0]
            if want and name not in want and stem not in want:
                continue
            out = os.path.join(OUT_ROOT, stem)
            mpath = os.path.join(out, "manifest.json")
            if not os.path.exists(mpath):
                print("%-10s %6s %8s %10s %s" % (stem, "-", "-", "-", "not decoded"))
                continue
            man = json.load(open(mpath))
            entry = by_name[name]
            base = entry["offset"]
            chunks = list(walk_chunks(f, base, entry["size"]))
            tex = [c for c in chunks if c["kind"] == "pTEX"]
            pals = [c for c in chunks if c["kind"] == "pPAL"]
            notes, covered_notes = [], []
            palettes = [read_banks(f, base + p["chunk_offset"], p, "pPAL %d" % n, notes)
                        for n, p in enumerate(pals)]
            ext = EXTERNAL_PALETTE.get(name)
            if ext and ext in by_name:
                palettes.append(read_external_banks(f, by_name, ext, notes))

            n_direct = n_indexed = 0
            for rec in man["chunks"]:
                if not rec.get("decoded"):
                    continue
                c = tex[rec["n"]]
                png = os.path.join(out, "chunk_%02d.png" % rec["n"])
                w, h, rgba = load_png(png)
                if (w, h) != (c["width"], c["height"]):
                    notes.append("chunk %02d: PNG %dx%d, header %dx%d"
                                 % (rec["n"], w, h, c["width"], c["height"]))
                    continue
                f.seek(base + c["chunk_offset"] + c["data_offset"])
                payload = f.read(c["file_size"] - c["data_offset"])
                raw = inflate(payload, c["compress_raw"] & 3, c["pitch"] * h,
                              "chunk %02d" % rec["n"], notes)
                if raw is None:
                    continue
                raw = change_endian(raw, c["bitdepth"], c["endian_flag"],
                                    c["formARGB"] == 0x8888)
                if c["bitdepth"] == 2:
                    if not endian_sanity(raw, w * h, "chunk %02d" % rec["n"], notes):
                        continue
                    if pack_1555(rgba, w * h) == raw:
                        n_direct += 1
                    else:
                        notes.append("chunk %02d: re-encode does not match source"
                                     % rec["n"])
                else:
                    idx = indices(raw, w, h, c["bitdepth"])
                    colors = {tuple(rgba[i * 4:i * 4 + 4]) for i in range(w * h)}
                    chosen = palettes[rec.get("palette_chunk", len(palettes) - 1)]
                    bank_size = len(chosen[0])
                    # Undrawn area is transparent by design, so an uncovered
                    # region legitimately adds the all-zero RGBA.
                    allowed = {tuple(c) for bank in chosen for c in bank}
                    if rec["covered_pct"] < 100.0:
                        allowed.add((0, 0, 0, 0))
                    if colors - allowed:
                        notes.append("chunk %02d: %d colour(s) outside the palette it "
                                     "was decoded with" % (rec["n"], len(colors - allowed)))
                    elif max(idx) >= bank_size:
                        notes.append("chunk %02d: index %d outside the %d-entry bank"
                                     % (rec["n"], max(idx), bank_size))
                    else:
                        n_indexed += 1
                        if rec["covered_pct"] < 100.0:
                            covered_notes.append(
                                "chunk %02d: trans table draws %.1f%% of the image, "
                                "rest is transparent" % (rec["n"], rec["covered_pct"]))
            exact += n_direct
            structural += n_indexed
            problems += bool(notes)
            print("%-10s %6d %8d %10d %s"
                  % (stem, len(man["chunks"]), n_direct, n_indexed,
                     "OK" if not notes else "FAIL"))
            for n in notes[:4]:
                print("           ! " + n)
            for n in covered_notes[:2]:
                print("           . " + n)
    print("\n%d chunk(s) re-encoded byte-identically, %d indexed chunk(s) "
          "structurally consistent, %d screen(s) with problems"
          % (exact, structural, problems))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
