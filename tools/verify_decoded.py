#!/usr/bin/env python3
"""verify_decoded - objective health check over work/decoded/*/layer*.png.

Reports, per layer sheet, on the drawn (alpha!=0) pixels only:
  colours  - distinct RGB count; a near-1 value means a flat/garbage decode
  green    - share of the (0,247,0) unused-slot filler in bg000.bin bank 0, the
             diagnostic that exposed the single-bank mistake (docs/format-notes.md)
  seam     - mean |dRGB| across the 128px tile boundaries vs. inside the tiles;
             a ratio >> 1 means tiles are not lining up
Nothing is printed except numbers; no pixel data leaves this machine.
"""
import os
import struct
import sys
import zlib

FILLER = (0, 247, 0)


def read_png(path):
    d = open(path, "rb").read()
    i, idat, w, h = 8, [], 0, 0
    while i < len(d):
        n = struct.unpack(">I", d[i:i + 4])[0]
        t = d[i + 4:i + 8]
        if t == b"IHDR":
            w, h = struct.unpack(">II", d[i + 8:i + 16])
        elif t == b"IDAT":
            idat.append(d[i + 8:i + 8 + n])
        i += 12 + n
    raw = zlib.decompress(b"".join(idat))
    out, prev, o = bytearray(), bytearray(w * 4), 0
    for _ in range(h):
        f = raw[o]; o += 1
        ln = bytearray(raw[o:o + w * 4]); o += w * 4
        if f == 1:
            for x in range(4, len(ln)):
                ln[x] = (ln[x] + ln[x - 4]) & 255
        elif f == 2:
            for x in range(len(ln)):
                ln[x] = (ln[x] + prev[x]) & 255
        elif f == 3:
            for x in range(len(ln)):
                ln[x] = (ln[x] + ((ln[x - 4] if x >= 4 else 0) + prev[x]) // 2) & 255
        elif f == 4:
            for x in range(len(ln)):
                a = ln[x - 4] if x >= 4 else 0
                b = prev[x]
                c = prev[x - 4] if x >= 4 else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                ln[x] = (ln[x] + pr) & 255
        out += ln
        prev = ln
    return w, h, bytes(out)


def dx(px, w, x, y):
    a = (y * w + x) * 4
    b = a + 4
    if not px[a + 3] or not px[b + 3]:
        return None
    return sum(abs(px[a + k] - px[b + k]) for k in range(3))


def check(path):
    w, h, px = read_png(path)
    cols, drawn, green = set(), 0, 0
    for i in range(0, len(px), 4):
        if not px[i + 3]:
            continue
        drawn += 1
        c = (px[i], px[i + 1], px[i + 2])
        cols.add(c)
        if c == FILLER:
            green += 1
    seam, inner = [], []
    for y in range(h):
        for x in range(w - 1):
            d = dx(px, w, x, y)
            if d is None:
                continue
            (seam if (x + 1) % 128 == 0 else inner).append(d)
    ms = sum(seam) / len(seam) if seam else 0.0
    mi = sum(inner) / len(inner) if inner else 0.0
    return drawn, len(cols), 100.0 * green / drawn if drawn else 0.0, ms, mi


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join("work", "decoded")
    print("%-22s %9s %7s %7s %7s %7s %6s"
          % ("sheet", "drawn", "colours", "green%", "seam", "inner", "ratio"))
    for stem in sorted(os.listdir(root)):
        for f in sorted(os.listdir(os.path.join(root, stem))):
            if not f.startswith("layer"):
                continue
            p = os.path.join(root, stem, f)
            drawn, nc, g, ms, mi = check(p)
            r = ms / mi if mi else 0.0
            flag = "  <-- check" if (g > 0.05 or nc < 64 or r > 3.0) else ""
            print("%-22s %9d %7d %7.3f %7.1f %7.1f %6.2f%s"
                  % (stem + "/" + f, drawn, nc, g, ms, mi, r, flag))


if __name__ == "__main__":
    main()
