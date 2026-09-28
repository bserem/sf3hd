#!/usr/bin/env python3
"""Validate an HD indexed PPG against its source transfer tables and palettes."""
import argparse
import io
import json
from pathlib import Path
import zlib
from PIL import Image
from ppg_inspect import walk_chunks
from ppg_repack import load_banks
from afs_extract import parse_index


def verify(afs, stem, packed):
    man = json.loads(Path('work/decoded', stem, 'manifest.json').read_text())
    entries, _ = parse_index(afs)
    data = Path(packed).read_bytes()
    chunks = list(walk_chunks(io.BytesIO(data), 0, len(data)))
    tex = [c for c in chunks if c['kind'] == 'pTEX']
    assert len(tex) == len(man['chunks'])
    assert chunks[-1]['kind'] == 'pEND' and chunks[-1]['chunk_offset'] + 4 == len(data)
    for c, old in zip(tex, man['chunks']):
        density = c['formARGB'] & 255
        assert c['formARGB'] & 0xff00 == 0x4800 and density in (2, 3, 4)
        assert c['pixel_raw'] & 3 == 1
        assert (c['width'], c['height']) == (old['width']*density, old['height']*density)
        start = c['chunk_offset']
        trans = data[start+16:start+c['data_offset']]
        assert trans == bytes.fromhex(old['trans'])
        raw = zlib.decompress(data[start+c['data_offset']:start+c['file_size']])
        assert len(raw) == c['width'] * c['height']
        banks = load_banks(afs, entries, man.get('palette') or old['palette_source'])
        indices = Image.frombytes('L', (c['width'], c['height']), raw)
        for p in range(0, len(trans), 3):
            bank, point, extent = trans[p:p+3]
            x, y = point % old['width_units'], point // old['width_units']
            w, h = (extent >> 4)+1, (extent & 15)+1
            rect = tuple(v*16*density for v in (x, y, x+w, y+h))
            assert rect[2] <= c['width'] and rect[3] <= c['height']
            assert indices.crop(rect).getextrema()[1] < len(banks[bank])
    print(f'{stem}: {len(tex)} HD indexed chunks verified; transfer tables unchanged, indices in palette bounds')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('afs')
    ap.add_argument('stem')
    ap.add_argument('packed')
    args = ap.parse_args()
    verify(args.afs, args.stem, args.packed)
