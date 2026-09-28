#!/usr/bin/env python3
"""afs_extract - read-only AFS index parser for SF33RD.AFS.

INDEX ONLY: this tool never reads or writes entry payload bytes.

Format is transcribed from 3sx's own loader, src/port/io/afs.c:
  - magic / constants ...................... lines 11-14
  - AFSEntry {offset, size, name[32]} ...... lines 16-20
  - header + entry table parse ............. lines 87-121 (init_afs)
  - attribute-table location heuristic ..... lines 53-76, 123-145
  - name read from attribute record ........ lines 78-86, 147-156
"""

import argparse
import struct
import sys

AFS_MAGIC = b"AFS\x00"           # afs.c:11 (0x41465300, read big-endian)
ATTR_HEADER_SIZE = 8             # afs.c:12
ATTR_ENTRY_SIZE = 48             # afs.c:13
MAX_NAME_LENGTH = 32             # afs.c:14


class AfsError(Exception):
    pass


def _u32(buf, off):
    """All header/table fields are little-endian u32 (afs.c uses SDL_ReadU32LE)."""
    return struct.unpack_from("<I", buf, off)[0]


def _valid_attributes(attr_off, attr_size, file_size, entries_end, entry_count):
    """Port of is_valid_attribute_data(), afs.c:53-76."""
    if attr_off == 0 or attr_size == 0:
        return False
    if attr_size > file_size - entries_end:
        return False
    if attr_size < entry_count * ATTR_ENTRY_SIZE:
        return False
    if attr_off < entries_end:
        return False
    if attr_off > file_size - attr_size:
        return False
    return True


def parse_index(path):
    """Return (entries, meta). Reads only the header/index/attribute regions."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        file_size = f.tell()
        f.seek(0)

        head = f.read(8)
        if len(head) < 8 or head[:4] != AFS_MAGIC:
            raise AfsError(f"bad magic {head[:4]!r}, expected {AFS_MAGIC!r}")
        entry_count = _u32(head, 4)

        # Entry table: entry_count * (u32 offset, u32 size), then an 8-byte
        # attribute-table pointer record. afs.c:104-131
        table = f.read(entry_count * 8 + ATTR_HEADER_SIZE)
        if len(table) < entry_count * 8 + ATTR_HEADER_SIZE:
            raise AfsError("file truncated inside entry table")

        entries = []
        entries_start = 0
        entries_end = 0
        for i in range(entry_count):
            off, size = struct.unpack_from("<II", table, i * 8)
            entries.append({"index": i, "offset": off, "size": size, "name": ""})
            if off != 0:
                if entries_start == 0:
                    entries_start = off
                entries_end = off + size

        attr_off = _u32(table, entry_count * 8)
        attr_size = _u32(table, entry_count * 8 + 4)
        attr_source = "after entry table"

        if not _valid_attributes(attr_off, attr_size, file_size, entries_end, entry_count):
            # Fallback: pointer sits just before the first blob. afs.c:133-143
            f.seek(entries_start - ATTR_HEADER_SIZE)
            attr_off, attr_size = struct.unpack("<II", f.read(8))
            attr_source = "before first entry"

        has_attrs = _valid_attributes(
            attr_off, attr_size, file_size, entries_end, entry_count
        )

        if has_attrs:
            f.seek(attr_off)
            attrs = f.read(entry_count * ATTR_ENTRY_SIZE)
            for e in entries:
                if e["offset"] == 0:
                    continue
                rec = attrs[e["index"] * ATTR_ENTRY_SIZE:][:ATTR_ENTRY_SIZE]
                if len(rec) < MAX_NAME_LENGTH:
                    continue
                raw = rec[:MAX_NAME_LENGTH].split(b"\x00", 1)[0]
                e["name"] = raw.decode("ascii", errors="replace")

    meta = {
        "file_size": file_size,
        "entry_count": entry_count,
        "entries_start": entries_start,
        "entries_end": entries_end,
        "attributes_offset": attr_off if has_attrs else 0,
        "attributes_size": attr_size if has_attrs else 0,
        "attributes_source": attr_source if has_attrs else "none",
        "has_attributes": has_attrs,
    }
    return entries, meta


def main(argv=None):
    ap = argparse.ArgumentParser(description="List the AFS index table (read-only).")
    ap.add_argument("afs", help="path to SF33RD.AFS")
    ap.add_argument("-n", "--limit", type=int, default=20,
                    help="entries to print (0 = all, default 20)")
    ap.add_argument("--start", type=int, default=0, help="first index to print")
    args = ap.parse_args(argv)

    try:
        entries, meta = parse_index(args.afs)
    except (AfsError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"file            : {args.afs}")
    print(f"file size       : {meta['file_size']} (0x{meta['file_size']:X})")
    print(f"entry count     : {meta['entry_count']}")
    print(f"first blob at   : 0x{meta['entries_start']:X}")
    print(f"last blob ends  : 0x{meta['entries_end']:X}")
    if meta["has_attributes"]:
        print(f"attribute table : 0x{meta['attributes_offset']:X} "
              f"size 0x{meta['attributes_size']:X} ({meta['attributes_source']})")
    else:
        print("attribute table : none found (entries will be unnamed)")
    print()

    end = len(entries) if args.limit == 0 else min(len(entries), args.start + args.limit)
    print(f"{'idx':>5}  {'offset':>10}  {'size':>10}  {'size (dec)':>12}  name")
    print(f"{'-'*5}  {'-'*10}  {'-'*10}  {'-'*12}  {'-'*32}")
    for e in entries[args.start:end]:
        print(f"{e['index']:>5}  0x{e['offset']:08X}  0x{e['size']:08X}  "
              f"{e['size']:>12}  {e['name']}")

    if end < len(entries):
        print(f"\n... {len(entries) - end} more entries (use -n 0 for all)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
