#!/usr/bin/env python3
"""Verify an HD sidecar and reconstruct local sample frames for visual QA."""
import argparse
import json
from pathlib import Path
from PIL import Image, ImageChops
from cg_hd_pack import read_pack, read_source
from cg_decode_tile import TEXGROUP


def render(fr, tiles, banks, scale):
    out = Image.new('RGBA', (fr['w']*scale, fr['h']*scale))
    for p in fr['pieces']:
        wh, side, indices = tiles[p['code']]
        if not isinstance(indices, Image.Image):
            indices = Image.frombytes('L', (side, side), bytes(indices)).resize((side*scale, side*scale), Image.Resampling.NEAREST)
        bank = banks[p['pal']]
        rgba = Image.frombytes('RGBA', indices.size, b''.join(bytes(bank[i]) for i in indices.tobytes()))
        crop = rgba.crop((0, 0, p['dw']*scale, p['dh']*scale))
        if p['attr'] & 0x8000:
            crop = crop.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if p['attr'] & 0x4000:
            crop = crop.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        out.alpha_composite(crop, (p['x']*scale, p['y']*scale))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('afs')
    ap.add_argument('pack', type=Path)
    ap.add_argument('--cg', required=True, choices=sorted(TEXGROUP),
                    help='character archive entry the pack belongs to')
    ap.add_argument('--out', type=Path, default=Path('work/hd-cg/qa'))
    args = ap.parse_args()
    scale, group, packed = read_pack(args.pack)
    stock, banks = read_source(args.afs, args.cg)
    assert group == TEXGROUP[args.cg]['grp'] and len(packed) == len(stock)
    assert all(packed[k][0] == stock[k][0] for k in stock)
    stem = args.cg.split('.')[0]
    frames = json.loads((Path('work/decoded') / stem / 'manifest.json').read_text())['frames']
    args.out.mkdir(parents=True, exist_ok=True)
    # Includes ordinary poses, transformed/flipped pieces, and late animations.
    # Clamped so characters with short manifests still get distinct samples.
    picks = sorted({min(i, len(frames)-1) for i in (0, 1, 99, 299, 499, len(frames)-1)})
    selected = [frames[i] for i in picks]
    for fr in selected:
        original = render(fr, stock, banks, 1)
        decoded = Image.open(Path('work/decoded') / stem / ('frame_%03d.png' % fr['n'])).convert('RGBA')
        # Ignore RGB beneath alpha zero, but verify visible colors and alpha.
        diff = ImageChops.difference(original, decoded)
        assert not diff.getchannel('A').getbbox()
        assert not ImageChops.multiply(diff.convert('RGB'), decoded.getchannel('A').convert('RGB')).getbbox()
        hd = render(fr, packed, banks, scale)
        target = Image.open(Path('work/tiles') / stem / ('frame_%03d.png' % fr['n'])).convert('RGBA')
        assert hd.size == target.size
        alpha_diff = ImageChops.difference(hd.getchannel('A'), target.getchannel('A')).histogram()[255]
        hd.save(args.out / ('frame_%03d-packed.png' % fr['n']))
        sheet = Image.new('RGBA', (hd.width * 3, hd.height), (45, 45, 45, 255))
        for i, im in enumerate((original.resize(hd.size, Image.Resampling.NEAREST), hd, target)):
            sheet.alpha_composite(im, (i*hd.width, 0))
        sheet.save(args.out / ('frame_%03d-comparison.png' % fr['n']))
        print('frame %03d: geometry %dx%d logical; alpha differs from full-frame target at %.2f%% of pixels' %
              (fr['n'], fr['w'], fr['h'], 100*alpha_diff/(hd.width*hd.height)))
    print('Directory/indices/stock reconstruction verified. Comparison columns: stock, packed HD, full-frame HD.')


if __name__ == '__main__':
    main()
