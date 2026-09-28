"""HD indexed textures retain palette selection and logical geometry."""
import io
import sys
import unittest
import zlib
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from ppg_repack import build_tex, quantize, scale_trans
from ppg_inspect import walk_chunks


class IndexedHDTests(unittest.TestCase):
    def test_per_rectangle_banks_at_four_times_density(self):
        red, blue = (255, 0, 0, 255), (0, 0, 255, 255)
        banks = [[red, blue], [blue, red]]
        trans = bytes([0, 0, 0, 1, 1, 0])
        pixels = bytes(red) * (128 * 64)
        indices = quantize(pixels, 128, 64, banks, trans, 2, 4)
        for y in range(64):
            self.assertEqual(indices[y*128:(y+1)*128], bytes(64) + bytes([1])*64)
        packed = build_tex(8, 4, 129, 0x4804, trans, indices)
        packed += bytes((-len(packed)) % 4) + b'pEND'
        chunk = next(walk_chunks(io.BytesIO(packed), 0, len(packed)))
        self.assertEqual(chunk['formARGB'], 0x4804)
        self.assertEqual(packed[16:22], trans)
        self.assertEqual(zlib.decompress(packed[22:chunk['file_size']]), indices)
        # The renderer divides the physical grid by density: boundaries and
        # normalized UVs must match the original rectangles exactly.
        self.assertEqual((chunk['width']//16)//4, 2)
        self.assertEqual((chunk['height']//16)//4, 1)

    def test_stock_transfer_limit_explains_opening_extension(self):
        with self.assertRaises(SystemExit):
            scale_trans(bytes([15, 0, 255]), 16, 2)


if __name__ == '__main__':
    unittest.main()
