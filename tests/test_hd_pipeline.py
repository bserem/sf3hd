import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import upscale
from cg_hd_pack import MAGIC, quantize, read_pack


class HDPipelineTests(unittest.TestCase):
    def test_old_scale_is_regenerated_and_correct_scale_is_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, dst = root/'src', root/'dst'
            (src/'pl02').mkdir(parents=True)
            (dst/'pl02').mkdir(parents=True)
            Image.new('RGBA', (8, 8), (200, 80, 30, 255)).save(src/'pl02/frame_001.png')
            Image.new('RGBA', (16, 16)).save(dst/'pl02/frame_001.png')
            def run(indir, outdir):
                for p in Path(indir).glob('*.png'):
                    with Image.open(p) as im:
                        im.resize((32, 32)).save(Path(outdir)/p.name)
            with patch.object(upscale, 'SRC', str(src)), patch.object(upscale, 'DST', str(dst)), patch.object(upscale, 'run_upscayl', side_effect=run) as model:
                upscale.do_stage('pl02', 4, False, False)
                self.assertEqual(model.call_count, 1)
                upscale.do_stage('pl02', 4, False, False)
                self.assertEqual(model.call_count, 1)
                with Image.open(dst/'pl02/frame_001.png') as im:
                    self.assertEqual(im.size, (32, 32))

    def test_quantize_preserves_indices_and_transparency(self):
        bank = [(0, 0, 0, 0), (255, 0, 0, 255), (0, 255, 0, 255)] + [(0, 0, 255, 255)]*61
        im = Image.new('RGBA', (3, 1))
        im.putdata([(255, 0, 0, 255), (0, 255, 0, 255), (255, 0, 0, 0)])
        self.assertEqual(quantize(im, bank).tobytes(), bytes([1, 2, 0]))

    def test_directory_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'test.hdcg'
            # One logical 16x16 tile at 4x.
            payload = bytes(range(64))*64
            valid = MAGIC + struct.pack('<4I', 4, 3, 1, 36+len(payload)) + struct.pack('<3I', 1, 36, len(payload)) + payload
            path.write_bytes(valid)
            scale, group, tiles = read_pack(path)
            self.assertEqual((scale, group, tiles[0][2].size), (4, 3, (64, 64)))
            for invalid in (valid[:20], valid[:-1], valid+b'X', valid[:36]+b'\xff'+valid[37:]):
                path.write_bytes(invalid)
                with self.assertRaises(ValueError):
                    read_pack(path)


if __name__ == '__main__':
    unittest.main()
