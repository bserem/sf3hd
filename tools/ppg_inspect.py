#!/usr/bin/env python3
"""ppg_inspect - read-only PPG chunk/header inspector.

HEADERS ONLY: walks the chunk list of one AFS entry and prints each chunk header.
It never reads, decompresses, or writes pixel or palette payload bytes.

Format transcribed from 3sx source:
  - struct PPGFileHeader / PPLFileHeader / PPXFileHeader
        3sx/src/structs.h:1289-1317
  - magic compare, big-endian ..... PPGFile.c:16 (MAGIC_TO_INT), 962-963, 515-519
  - chunk walk / 4-byte align ..... PPGFile.c:957-996 (ppgSetupTexChunk_1st)
  - width/height/pixel decode ..... PPGFile.c:1169-1205 (ppgSetupContextFromPPG)
  - palette c_mode decode ......... PPGFile.c:1098-1104 (ppgSetupContextFromPPL),
                                    PPGFile.c:33 (pplColorModeWidth)
  - pixel-data start offset ....... PPGFile.c:1071-1074 (transNums * 3 + 0x10)
  - compression codes ............. PPGFile.c:408-438 (ppgDecompress)
"""

import argparse
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from afs_extract import parse_index  # noqa: E402

CHUNK_HEADER_SIZE = 16
MAGIC_END = b"pEND"

# ppgDecompress(), PPGFile.c:414-437. koCmpr = compress & 3.
COMPRESS = {0: "none", 1: "LZ77", 2: "zlib", 3: "none (default branch)"}

# ppgSetupContextFromPPG(), PPGFile.c:1174-1195. key = pixel & 3.
PIXEL_MODE = {
    0: ("4bpp indexed", 0),
    1: ("8bpp indexed", 1),
    2: ("16bpp direct", 2),
    3: ("32bpp direct", 4),
}

# pplColorModeWidth[4], PPGFile.c:33; used as +1 for entry count.
PPL_COLOR_ITEMS = {0: 16, 1: 64, 2: 256, 3: 1}


def _align4(n):
    return (n + 3) & ~3


def parse_tex_chunk(buf):
    """PPGFileHeader, structs.h:1289-1298. All multi-byte fields big-endian."""
    magic, file_size, width, height, compress, pixel, form, trans = struct.unpack(
        ">4sIBBBBHH", buf
    )
    mode, bitdepth = PIXEL_MODE[pixel & 3]
    px_w = width * 16
    px_h = height * 16
    pitch = px_w // 2 if (pixel & 3) == 0 else px_w * bitdepth
    return {
        "kind": "pTEX",
        "magic": magic,
        "file_size": file_size,
        "width": px_w,
        "height": px_h,
        "width_units": width,
        "height_units": height,
        "pixel_raw": pixel,
        "pixel_mode": mode,
        "bitdepth": bitdepth,
        "pitch": pitch,
        "unswizzled_size": pitch * px_h,
        "compress_raw": compress,
        "compress": COMPRESS[compress & 3],
        "formARGB": form,
        "transNums": trans,
        # PPGFile.c:1072-1074: payload starts after the trans table.
        "data_offset": trans * 3 + 0x10,
        "endian_flag": bool(pixel & 4),
        "desc_alt_flag": bool(pixel & 0x20),
    }


def parse_pal_chunk(buf):
    """PPLFileHeader, structs.h:1309-1317."""
    magic, file_size, free, compress, c_mode, form, palettes = struct.unpack(
        ">4sIHBBHH", buf
    )
    items = PPL_COLOR_ITEMS[c_mode & 3]
    bitdepth = 4 if form == 0x8888 else 2
    return {
        "kind": "pPAL",
        "magic": magic,
        "file_size": file_size,
        "compress_raw": compress,
        "compress": COMPRESS[compress & 3],
        "c_mode_raw": c_mode,
        "colors_per_palette": items,
        "palettes": palettes,
        "entry_bytes": bitdepth,
        "formARGB": form,
        "endian_flag": bool(c_mode & 4),
    }


def parse_generic_chunk(buf):
    magic, file_size = struct.unpack_from(">4sI", buf)
    return {"kind": magic.decode("ascii", "replace"), "magic": magic,
            "file_size": file_size}


def walk_chunks(f, base, limit, max_chunks=4096):
    """Yield chunk dicts. Seeks only to 16-byte chunk headers."""
    ofs = 0
    for _ in range(max_chunks):
        # The loader tests magic before touching any other field, so the
        # terminator is a bare 4-byte marker. PPGFile.c:960-962.
        if ofs + 4 > limit:
            yield {"kind": "!truncated", "chunk_offset": ofs}
            return
        f.seek(base + ofs)
        raw = f.read(CHUNK_HEADER_SIZE)
        magic = raw[:4]
        if magic == MAGIC_END:
            yield {"kind": "pEND", "magic": magic, "chunk_offset": ofs}
            return
        if len(raw) < CHUNK_HEADER_SIZE:
            yield {"kind": "!truncated", "chunk_offset": ofs}
            return
        if magic == b"pTEX":
            c = parse_tex_chunk(raw)
        elif magic == b"pPAL":
            c = parse_pal_chunk(raw)
        elif magic[:1] == b"p" and magic[1:].isalnum():
            c = parse_generic_chunk(raw)
        else:
            yield {"kind": "!unknown magic", "magic": magic, "chunk_offset": ofs}
            return
        c["chunk_offset"] = ofs
        yield c
        step = _align4(c["file_size"])          # PPGFile.c:968, 994
        if step <= 0:
            yield {"kind": "!bad fileSize", "chunk_offset": ofs}
            return
        ofs += step


def inspect(afs_path, index):
    entries, _ = parse_index(afs_path)
    if index >= len(entries):
        raise SystemExit(f"error: index {index} out of range (0..{len(entries)-1})")
    e = entries[index]
    print(f"entry {e['index']}: {e['name'] or '<unnamed>'}  "
          f"offset 0x{e['offset']:X}  size 0x{e['size']:X} ({e['size']})")
    if e["offset"] == 0:
        print("  empty slot, nothing to inspect")
        return

    counts = {}
    consumed = 0
    with open(afs_path, "rb") as f:
        for c in walk_chunks(f, e["offset"], e["size"]):
            counts[c["kind"]] = counts.get(c["kind"], 0) + 1
            off = c.get("chunk_offset", 0)
            if c["kind"] == "pTEX":
                consumed = off + _align4(c["file_size"])
                print(f"  [+0x{off:06X}] pTEX  {c['width']}x{c['height']}px  "
                      f"{c['pixel_mode']}  {c['compress']}")
                print(f"             fileSize=0x{c['file_size']:X}  "
                      f"units={c['width_units']}x{c['height_units']} (x16)  "
                      f"pitch={c['pitch']}  raw_bitmap={c['unswizzled_size']}B")
                print(f"             formARGB=0x{c['formARGB']:04X}  "
                      f"transNums={c['transNums']}  "
                      f"pixels@+0x{c['data_offset']:X}  "
                      f"pixel_raw=0x{c['pixel_raw']:02X}"
                      f"{'  [endian-swapped]' if c['endian_flag'] else ''}"
                      f"{'  [desc 0x24]' if c['desc_alt_flag'] else ''}")
            elif c["kind"] == "pPAL":
                consumed = off + _align4(c["file_size"])
                print(f"  [+0x{off:06X}] pPAL  {c['palettes']} palette(s) x "
                      f"{c['colors_per_palette']} colors  {c['compress']}")
                print(f"             fileSize=0x{c['file_size']:X}  "
                      f"formARGB=0x{c['formARGB']:04X}  "
                      f"{c['entry_bytes']}B/color  c_mode=0x{c['c_mode_raw']:02X}"
                      f"{'  [endian-swapped]' if c['endian_flag'] else ''}")
            elif c["kind"] == "pEND":
                consumed = off + 4
                print(f"  [+0x{off:06X}] pEND")
            elif c["kind"].startswith("!"):
                print(f"  [+0x{off:06X}] {c['kind']} {c.get('magic', b'')!r}")
            else:
                consumed = off + _align4(c["file_size"])
                print(f"  [+0x{off:06X}] {c['kind']}  "
                      f"fileSize=0x{c['file_size']:X}")

    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"  -- {summary}; chunks span 0x{consumed:X} of 0x{e['size']:X} bytes "
          f"({e['size'] - consumed} trailing/pad)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Inspect PPG chunk headers (read-only).")
    ap.add_argument("afs")
    ap.add_argument("index", type=int, nargs="+", help="AFS entry index/indices")
    args = ap.parse_args(argv)
    for i, idx in enumerate(args.index):
        if i:
            print()
        inspect(args.afs, idx)
    return 0


if __name__ == "__main__":
    sys.exit(main())
