#!/usr/bin/env python3
"""verify_rebuild - independent check of a rebuilt AFS against the original.

Does not trust afs_rebuild's own reporting: re-parses both files from scratch and
hashes every entry payload, confirming that entries not named as replacements are
byte-identical and that replacements match their source file exactly.
"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402

CHUNK = 1 << 20


def digest(f, off, size):
    h = hashlib.sha256()
    f.seek(off)
    left = size
    while left:
        b = f.read(min(CHUNK, left))
        if not b:
            raise SystemExit("short read at %d" % off)
        h.update(b)
        left -= len(b)
    return h.hexdigest()


def main():
    orig_path, new_path = sys.argv[1], sys.argv[2]
    repl = {}
    for r in sys.argv[3:]:
        name, _, path = r.partition("=")
        repl[name] = path

    a, ameta = parse_index(orig_path)
    b, bmeta = parse_index(new_path)

    print("entries        original %d, new %d" % (len(a), len(b)))
    if len(a) != len(b):
        raise SystemExit("entry count differs")

    bad = []
    names_ok = sum(1 for x, y in zip(a, b) if x["name"] == y["name"])
    print("names match     %d / %d" % (names_ok, len(a)))
    print("all 0x800-aligned  %s" % all(y["offset"] % 0x800 == 0 for y in b))
    ne = [y for y in b if y["offset"]]
    ne.sort(key=lambda y: y["offset"])
    gaps_ok = all(q["offset"] == ((p["offset"] + p["size"] + 0x7FF) & ~0x7FF)
                  for p, q in zip(ne, ne[1:]))
    print("no slack between entries  %s" % gaps_ok)
    print("attr table size == n*48   %s"
          % (bmeta["attributes_size"] == len(b) * 48))

    unchanged = changed = 0
    with open(orig_path, "rb") as fa, open(new_path, "rb") as fb:
        for x, y in zip(a, b):
            if x["name"] in repl:
                want = hashlib.sha256(open(repl[x["name"]], "rb").read()).hexdigest()
                got = digest(fb, y["offset"], y["size"])
                if got != want or y["size"] != os.path.getsize(repl[x["name"]]):
                    bad.append("%s: replacement payload mismatch" % x["name"])
                else:
                    changed += 1
                continue
            if x["size"] != y["size"]:
                bad.append("%s: size %d -> %d" % (x["name"], x["size"], y["size"]))
                continue
            if digest(fa, x["offset"], x["size"]) != digest(fb, y["offset"], y["size"]):
                bad.append("%s: payload changed" % x["name"])
            else:
                unchanged += 1

    # attribute table must be carried over verbatim
    at_a = digest(open(orig_path, "rb"), ameta["attributes_offset"], len(a) * 48)
    at_b = digest(open(new_path, "rb"), bmeta["attributes_offset"], len(b) * 48)
    print("attribute table identical  %s" % (at_a == at_b))

    print("\nunchanged entries verified identical  %d" % unchanged)
    print("replaced entries verified              %d" % changed)
    print("problems                               %d" % len(bad))
    for p in bad[:10]:
        print("  ! " + p)
    return 1 if bad or at_a != at_b else 0


if __name__ == "__main__":
    sys.exit(main())
