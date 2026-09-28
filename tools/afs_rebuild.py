#!/usr/bin/env python3
"""afs_rebuild - write a new AFS with some entries replaced.

Every unchanged entry is copied verbatim; replacements are substituted; the
offset/size index table is regenerated for all entries so that growth in any
entry correctly shifts every entry after it.

Alignment rule -- measured from the real SF33RD.AFS index, not assumed:
  * all 1535 entries start on a 0x800 boundary
  * every entry is followed by the next at exactly the NEXT 0x800 boundary
    (checked for all 1534 pairs: zero exceptions, no slack beyond padding)
  * the data region starts at align(8 + count*8 + 8, 0x800)  = 0x3800 here
  * the attribute table sits at the 0x800 boundary after the last blob,
    size == count * 48, and the file is padded to a 0x800 multiple

Attribute records are copied verbatim. 3sx reads only the NUL-terminated name
(afs.c:153-162). The 16 bytes after the name hold a fixed timestamp plus a u32
whose values in the stock file are a strided copy of the index table rather than
per-entry sizes -- a quirk of Capcom's packer, so there is no "correct" value to
recompute. Leaving them untouched is the only lossless option.
"""
import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index                      # noqa: E402

ALIGN = 0x800
ATTR_ENTRY = 48
COPY_CHUNK = 1 << 20


def align(n, a=ALIGN):
    return (n + a - 1) & ~(a - 1)


def copy_range(src, dst, offset, size):
    src.seek(offset)
    left = size
    while left:
        b = src.read(min(COPY_CHUNK, left))
        if not b:
            raise SystemExit("short read at %d" % offset)
        dst.write(b)
        left -= len(b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("afs")
    ap.add_argument("out")
    ap.add_argument("--replace", action="append", default=[],
                    help="NAME=path, repeatable")
    ap.add_argument("--replace-dir",
                    help="directory whose filenames match AFS entry names")
    args = ap.parse_args()

    entries, meta = parse_index(args.afs)
    n = len(entries)

    repl = {}
    for r in args.replace:
        name, _, path = r.partition("=")
        repl[name] = path
    if args.replace_dir:
        for fn in sorted(os.listdir(args.replace_dir)):
            repl.setdefault(fn, os.path.join(args.replace_dir, fn))

    known = {e["name"] for e in entries}
    for name in list(repl):
        if name not in known:
            raise SystemExit("no AFS entry named %r" % name)

    # plan
    data_start = align(8 + n * 8 + 8)
    plan, cur = [], data_start
    for e in entries:
        path = repl.get(e["name"])
        size = os.path.getsize(path) if path else e["size"]
        plan.append((e, path, cur, size))
        cur = align(cur + size)
    attr_off = cur
    total = align(attr_off + n * ATTR_ENTRY)

    with open(args.afs, "rb") as src, open(args.out, "wb") as dst:
        dst.write(b"AFS\x00" + struct.pack("<I", n))
        for _, _, off, size in plan:
            dst.write(struct.pack("<II", off, size))
        dst.write(struct.pack("<II", attr_off, n * ATTR_ENTRY))
        dst.write(b"\x00" * (data_start - dst.tell()))

        for e, path, off, size in plan:
            assert dst.tell() == off, (dst.tell(), off)
            if path:
                with open(path, "rb") as g:
                    dst.write(g.read())
            else:
                copy_range(src, dst, e["offset"], size)
            dst.write(b"\x00" * (align(off + size) - (off + size)))

        assert dst.tell() == attr_off, (dst.tell(), attr_off)
        copy_range(src, dst, meta["attributes_offset"], n * ATTR_ENTRY)
        dst.write(b"\x00" * (total - dst.tell()))

    # verify by re-parsing the product with the same parser the game logic implies
    new, nmeta = parse_index(args.out)
    assert len(new) == n
    problems = []
    if nmeta["attributes_size"] != n * ATTR_ENTRY:
        problems.append("attribute size")
    for (e, path, off, size), ne in zip(plan, new):
        if ne["name"] != e["name"]:
            problems.append("name %s != %s" % (ne["name"], e["name"]))
        if (ne["offset"], ne["size"]) != (off, size):
            problems.append("index mismatch for %s" % e["name"])
        if ne["offset"] % ALIGN:
            problems.append("%s not 0x800-aligned" % e["name"])
    with open(args.out, "rb") as g:
        for (e, path, off, size), ne in zip(plan, new):
            if not path:
                continue
            g.seek(off)
            if g.read(size) != open(path, "rb").read():
                problems.append("payload mismatch for %s" % e["name"])

    print("entries        %d" % n)
    print("replaced       %d (%s)" % (len(repl), ", ".join(sorted(repl)) or "-"))
    print("data start     0x%X" % data_start)
    print("attr table     0x%X (%d bytes)" % (attr_off, n * ATTR_ENTRY))
    print("output size    %d (%+d vs original)"
          % (total, total - meta["file_size"]))
    print("verify         %s" % ("OK" if not problems else "; ".join(problems[:5])))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
