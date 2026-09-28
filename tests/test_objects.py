"""Regression checks for the selection renderer's palette semantics."""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'tools'))
from decode_objects import colorram, piece_palette


class ObjectPaletteTests(unittest.TestCase):
    def test_cp3_ext_preserves_offsets_above_low_nibble(self):
        self.assertEqual(piece_palette(144, 0xC023), 179)
        self.assertEqual(piece_palette(144, 0x0013), 163)

    def test_chip_masks_final_bank(self):
        self.assertEqual(piece_palette(500, 20), 8)

    def test_flat_override(self):
        self.assertEqual(piece_palette(144, 0xC023, False), 144)

    def test_file_source_is_argb_not_internal_abgr(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(struct.pack('<64H', 0xFC00, 0x801F, *([0] * 62)))
            f.flush()
            ram, _ = colorram(f.name, {'default.bin': {'offset': 0, 'size': 128}})
        self.assertEqual(ram[32][0], (255, 0, 0, 255))
        self.assertEqual(ram[32][1], (0, 0, 255, 255))
        self.assertEqual(ram[32][2], (0, 0, 0, 0))


if __name__ == '__main__':
    unittest.main()
