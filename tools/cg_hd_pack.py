#!/usr/bin/env python3
"""Pack local upscaled character frames as indexed HD tiles for 3SX.

Keeps original CG geometry and AFS entries intact. The sidecar is local-only.
For shared tiles choose the occurrence with the most visible pixels (stable
first-frame tie break). Preserve hidden pixels from a nearest-scaled stock tile;
replace visible pixels and transparent background from the composited HD frame.
This is a palette-preserving test pack, not a lossless full-frame RGBA export.
"""
import argparse
import json
import struct
from pathlib import Path

from PIL import Image, ImageChops
from afs_extract import parse_index
from cg_decode_tile import TEXGROUP, WH_PIXELS, lz_ext_p6_fx, unswizzle
from cg_repack import owner_map
from decode_objects import OBJGROUP, COLORRAM_FILE, COLORRAM_BASE
from ppg_decode_indexed import unpack_1555

MAGIC = b'3SXCGHD1'


def read_object_source(afs, cg):
    """Object groups (efNN.bin): tiles as usual, but the banks come from
    ColorRAM (default.bin) and the manifest stores absolute bank numbers."""
    cfg = OBJGROUP[cg]
    entries, _ = parse_index(afs)
    entries = {e['name']: e for e in entries if e['name']}
    def read(name):
        e = entries[name]
        with open(afs, 'rb') as f:
            f.seek(e['offset'])
            return f.read(e['size'])
    data, palette = read(cg), read(COLORRAM_FILE)
    banks = {COLORRAM_BASE + i: unpack_1555(palette[i*128:(i+1)*128], 64, swap_rb=False)
             for i in range(len(palette) // 128)}
    return read_tiles(data, cfg['to_tex']), banks


def read_tiles(data, base):
    count = struct.unpack_from('<I', data, base)[0] // 4
    offsets = struct.unpack_from('<%dI' % count, data, base)
    tiles = {}
    for code, offset in enumerate(offsets):
        start = base + offset
        wh = data[start]
        side = WH_PIXELS.get((wh & 3) + 1)
        if side is None:
            raise ValueError('unsupported logical tile side at code %d' % code)
        raw, _ = lz_ext_p6_fx(data[start+1:], side * side)
        tiles[code] = (wh, side, unswizzle(raw, side))
    return tiles


def read_source(afs, cg):
    if cg in OBJGROUP:
        return read_object_source(afs, cg)
    cfg = TEXGROUP[cg]
    entries, _ = parse_index(afs)
    entries = {e['name']: e for e in entries}
    def read(name):
        e = entries[name]
        with open(afs, 'rb') as f:
            f.seek(e['offset'])
            return f.read(e['size'])
    data, palette = read(cg), read(cfg['pal'])
    banks = [unpack_1555(palette[i:i+128], 64, swap_rb=False)
             for i in range(0, len(palette), 128)]
    base = cfg['to_tex']
    count = struct.unpack_from('<I', data, base)[0] // 4
    offsets = struct.unpack_from('<%dI' % count, data, base)
    tiles = {}
    for code, offset in enumerate(offsets):
        start = base + offset
        wh = data[start]
        side = WH_PIXELS.get((wh & 3) + 1)
        if side is None:
            raise ValueError('unsupported logical tile side at code %d' % code)
        raw, _ = lz_ext_p6_fx(data[start+1:], side * side)
        tiles[code] = (wh, side, unswizzle(raw, side))
    return tiles, banks


def quantize(rgba, bank):
    # Exclude transparent entries from opaque quantization, retain original bank
    # indices so all runtime costumes still use their own ColorRAM palette.
    opaque = [i for i, c in enumerate(bank) if c[3]]
    clear = next(i for i, c in enumerate(bank) if not c[3])
    if not opaque:
        return Image.new('L', rgba.size, clear)
    mapping = (opaque * (256 // len(opaque) + 1))[:256]
    palette = Image.new('P', (1, 1))
    palette.putpalette([v for i in mapping for v in bank[i][:3]])
    indexed = rgba.convert('RGB').quantize(palette=palette, dither=Image.Dither.NONE)
    result = Image.frombytes('L', indexed.size, indexed.tobytes()).point(mapping)
    result.paste(clear, mask=rgba.getchannel('A').point(lambda a: 255 if a == 0 else 0))
    return result



def read_pack(path):
    data = Path(path).read_bytes()
    if len(data) < 24 or data[:8] != MAGIC:
        raise ValueError('bad HD pack header')
    scale, group, count, size = struct.unpack_from('<4I', data, 8)
    if scale not in (2, 4) or group >= 256 or not 0 < count <= 65536 or size != len(data):
        raise ValueError('bad HD pack dimensions/length')
    cursor = 24 + count * 12
    if cursor > size:
        raise ValueError('truncated HD directory')
    tiles = {}
    for code in range(count):
        wh, offset, length = struct.unpack_from('<3I', data, 24 + code * 12)
        side = WH_PIXELS.get((wh & 3) + 1)
        if wh > 255 or not side or offset != cursor or length != (side * scale)**2 or offset + length > size:
            raise ValueError('bad HD tile directory')
        pixels = Image.frombytes('L', (side*scale, side*scale), data[offset:offset+length])
        if pixels.getextrema()[1] >= 64:
            raise ValueError('HD palette index outside bank')
        tiles[code] = (wh, side, pixels)
        cursor += length
    if cursor != size:
        raise ValueError('trailing HD bytes')
    return scale, group, tiles


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('afs')
    ap.add_argument('--cg', required=True, choices=sorted(set(TEXGROUP) | set(OBJGROUP)),
                    help='character or object archive entry to pack')
    ap.add_argument('--src', type=Path)
    ap.add_argument('--manifest', type=Path)
    ap.add_argument('--out', type=Path)
    ap.add_argument('--limit-frames', type=int, help='small local pipeline smoke test only')
    args = ap.parse_args()
    stem = args.cg.split('.')[0]
    args.src = args.src or Path('work/tiles') / stem
    args.manifest = args.manifest or Path('work/decoded') / stem / 'manifest.json'
    args.out = args.out or Path('work/hd-cg') / (stem + '.hdcg')
    manifest = json.loads(args.manifest.read_text())
    frames = manifest['frames'][:args.limit_frames] if args.limit_frames else manifest['frames']
    tiles, banks = read_source(args.afs, args.cg)
    with Image.open(args.src / ('frame_%03d.png' % frames[0]['n'])) as im:
        scale = im.width // frames[0]['w']
    if scale not in (2, 4):
        raise ValueError('HD pack requires 2x or 4x frames')
    best, owners = {}, {}
    for fr in frames:
        own = owner_map(fr, tiles, banks)
        owners[fr['n']] = own
        for k, piece in enumerate(fr['pieces']):
            score = own.count(k)
            if piece['code'] not in best or score > best[piece['code']][0]:
                best[piece['code']] = (score, fr['n'], k)
    output = {code: Image.frombytes('L', (side, side), bytes(pix)).resize((side*scale, side*scale), Image.Resampling.NEAREST)
              for code, (wh, side, pix) in tiles.items()}
    changed, copied = 0, 0
    for fr in frames:
        with Image.open(args.src / ('frame_%03d.png' % fr['n'])) as src:
            im = src.convert('RGBA')
        if im.size != (fr['w'] * scale, fr['h'] * scale):
            raise ValueError('mixed frame scale at %d' % fr['n'])
        if set(im.getchannel('A').getdata()) - {0, 255}:
            raise ValueError('nonbinary alpha at %d' % fr['n'])
        own = owners[fr['n']]
        for k, p in enumerate(fr['pieces']):
            if best[p['code']][1:] != (fr['n'], k):
                continue
            x, y, w, h = (p[v] * scale for v in ('x', 'y', 'dw', 'dh'))
            # Some logical pieces extend beyond the exported frame bounding box.
            # Cropped-out pixels remain stock; never replace them with padding.
            crop = im.crop((x, y, x+w, y+h))
            mask = Image.frombytes('L', (fr['w'], fr['h']), bytes(255 if v == k or v == -1 else 0 for v in own))
            mask = mask.resize(im.size, Image.Resampling.NEAREST).crop((x, y, x+w, y+h))
            pixels = quantize(crop, banks[p['pal']])
            if p['attr'] & 0x8000:
                pixels = pixels.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                mask = mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if p['attr'] & 0x4000:
                pixels = pixels.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                mask = mask.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
            dst = output[p['code']]
            delta = ImageChops.difference(dst.crop((0, 0, w, h)), pixels).point(lambda v: 255 if v else 0)
            changed += ImageChops.multiply(delta, mask).histogram()[255]
            copied += mask.histogram()[255]
            dst.paste(pixels, (0, 0), mask)
    count = len(tiles)
    offset = 24 + count * 12
    directory, payload = bytearray(), bytearray()
    for code, (wh, side, original) in tiles.items():
        raw = output[code].tobytes()
        directory += struct.pack('<3I', wh, offset, len(raw))
        payload += raw
        offset += len(raw)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    temp = args.out.with_suffix('.tmp')
    grp = (OBJGROUP if args.cg in OBJGROUP else TEXGROUP)[args.cg]['grp']
    temp.write_bytes(MAGIC + struct.pack('<4I', scale, grp, count, offset) + directory + payload)
    got_scale, got_group, got_tiles = read_pack(temp)
    assert got_scale == scale and got_group == grp
    assert all(got_tiles[c][2].tobytes() == output[c].tobytes() for c in output)
    temp.replace(args.out)
    print('%s: %dx, %d frames, %d tiles, %d bytes; %d/%d sampled texels changed from stock' %
          (args.out, scale, len(frames), count, offset, changed, copied))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
