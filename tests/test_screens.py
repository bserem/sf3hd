"""Regression tests for the UI screen decoder.

The endian tests exist because the first version of decode_screens.py skipped
ppgChangeDataEndian entirely. A re-encode round trip could not see the mistake,
so these lock in both the conversion and the check that detects its absence.
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from build_screens import decode_buffer_needed, direct32_safe
from cut_pages import copy_screen
from decode_screens import change_endian, indices, select_palette
from verify_screens import endian_sanity, opacity


def pal(colors, banks):
    return {"colors_per_palette": colors, "palettes": banks}


class EndianTests(unittest.TestCase):
    def test_16bit_is_swapped_only_when_the_flag_is_clear(self):
        raw = bytes([0x80, 0x01, 0x7F, 0xFE])
        self.assertEqual(change_endian(raw, 2, False, False),
                         bytes([0x01, 0x80, 0xFE, 0x7F]))
        self.assertEqual(change_endian(raw, 2, True, False), raw)

    def test_index_planes_are_never_swapped(self):
        raw = bytes(range(8))
        for depth in (0, 1):
            self.assertEqual(change_endian(raw, depth, False, False), raw)

    def test_32bit_uses_word_swap(self):
        raw = bytes([1, 2, 3, 4])
        self.assertEqual(change_endian(raw, 4, False, True), bytes([4, 3, 2, 1]))

    def test_sanity_check_flags_an_inverted_buffer(self):
        # Opaque ARGB1555 texels: alpha in bit 15, stored big-endian in the file.
        n = 64
        good = struct.pack("<%dH" % n, *([0xFFFF] * n))
        notes = []
        self.assertTrue(endian_sanity(good, n, "t", notes))
        self.assertEqual(notes, [])
        # The same data left unswapped: read as LE, the alpha bit moves out of
        # bit 15 and an opaque screen reads as fully transparent.
        bad = struct.pack(">%dH" % n, *([0x8000] * n))
        self.assertLess(opacity(bad, n, False), 0.2)
        self.assertGreater(opacity(bad, n, True), 0.8)
        self.assertFalse(endian_sanity(bad, n, "t", notes))
        self.assertIn("endian handling is probably inverted", notes[0])


class PixelTests(unittest.TestCase):
    def test_4bpp_low_nibble_is_the_even_pixel(self):
        # Matches fetch4bppIndex in shaders/opengl/frag_palette_4.glsl.
        self.assertEqual(indices(bytes([0x21, 0x43]), 4, 1, 0), bytes([1, 2, 3, 4]))

    def test_8bpp_is_passed_through(self):
        self.assertEqual(indices(bytes([9, 8]), 2, 1, 1), bytes([9, 8]))


class PaletteSelectionTests(unittest.TestCase):
    def test_single_wide_enough_palette(self):
        self.assertEqual(select_palette(1, 200, 0, 1, [pal(256, 1)], [], "t"),
                         (0, 0, "only-palette"))

    def test_one_bank_per_chunk(self):
        # Ake3rd.ppg: three chunks, one palette holding three banks.
        self.assertEqual(select_palette(1, 60, 2, 3, [pal(64, 3)], [], "t"),
                         (0, 2, "bank-per-chunk"))

    def test_one_palette_per_chunk(self):
        # Warninge.ppg: two indexed chunks, two 256-colour palettes.
        pals = [pal(256, 1), pal(256, 1)]
        self.assertEqual(select_palette(1, 200, 1, 2, pals, [], "t"),
                         (1, 0, "palette-per-chunk"))

    def test_ambiguous_is_reported_not_hidden(self):
        # scrscrn.ppg: five 4bpp chunks, two candidate 16-colour palettes.
        notes = []
        pal_n, bank, rule = select_palette(0, 15, 0, 5, [pal(16, 32), pal(16, 260)],
                                           notes, "t")
        self.assertEqual((pal_n, bank, rule), (0, 0, "ambiguous"))
        self.assertIn("made at draw time", notes[0])

    def test_no_palette_is_wide_enough(self):
        notes = []
        self.assertEqual(select_palette(1, 200, 0, 1, [pal(16, 4)], notes, "t")[0], None)
        self.assertIn("no palette in the file is that wide", notes[0])


if __name__ == '__main__':
    unittest.main()


class ScreenCutTests(unittest.TestCase):
    """A screen must come out of the upscaler at one integer scale.

    ppg_repack rejects a mixed scale too, but catching it here names the file.
    """

    def make(self, sizes):
        d = Path(tempfile.mkdtemp())
        (d / "src").mkdir()
        (d / "dst").mkdir()
        chunks = []
        for n, (base, got) in enumerate(sizes):
            Image.new("RGBA", got, (10, 20, 30, 255)).save(d / "src" / ("chunk_%02d.png" % n))
            chunks.append({"n": n, "width": base[0], "height": base[1], "decoded": True})
        return d, {"chunks": chunks}

    def test_uniform_scale_is_copied(self):
        d, man = self.make([((128, 128), (512, 512)), ((256, 128), (1024, 512))])
        scale, written, problems = copy_screen("t", d / "src", d / "dst", man)
        self.assertEqual((scale, written, problems), (4, 2, []))

    def test_mixed_scale_is_rejected(self):
        d, man = self.make([((128, 128), (512, 512)), ((128, 128), (256, 256))])
        scale, written, problems = copy_screen("t", d / "src", d / "dst", man)
        self.assertEqual(scale, 4)
        self.assertTrue(any("is 2x but the screen is 4x" in p for p in problems))

    def test_non_integer_scale_is_rejected(self):
        d, man = self.make([((128, 128), (300, 300))])
        _, written, problems = copy_screen("t", d / "src", d / "dst", man)
        self.assertEqual(written, 0)
        self.assertTrue(any("not an integer scale" in p for p in problems))

    def test_undecoded_chunks_are_skipped(self):
        d, man = self.make([((128, 128), (512, 512))])
        man["chunks"].append({"n": 9, "width": 128, "height": 128, "decoded": False})
        _, written, problems = copy_screen("t", d / "src", d / "dst", man)
        self.assertEqual((written, problems), (1, []))


class DecodeBufferTests(unittest.TestCase):
    """ppgSetupTexChunk_3rd decompresses a whole chunk into one buffer.

    Getting this wrong is not a graceful failure: the engine aborts at load
    with "Failed to allocate texture data buffer", which is how the first
    4x screen build died.
    """

    def stems(self, sizes):
        chunks = [{"n": n, "width": w, "height": h, "decoded": True}
                  for n, (w, h) in enumerate(sizes)]
        return [("Warning", {"chunks": chunks}, len(chunks))]

    def test_largest_chunk_wins_and_scales_by_the_square(self):
        need, where = decode_buffer_needed(self.stems([(128, 128), (512, 512)]), 4)
        self.assertEqual(need, 2048 * 2048 * 4)        # 16 MiB, direct32
        self.assertIn("chunk 01 (512x512)", where)
        half, _ = decode_buffer_needed(self.stems([(512, 512)]), 2)
        self.assertEqual(half, need // 4)

    def test_undecoded_chunks_do_not_count(self):
        stems = self.stems([(128, 128)])
        stems[0][1]["chunks"].append({"n": 7, "width": 512, "height": 512,
                                      "decoded": False})
        need, where = decode_buffer_needed(stems, 4)
        self.assertEqual(need, 512 * 512 * 4)
        self.assertIn("chunk 00", where)


class Direct32SafetyTests(unittest.TestCase):
    """direct32 bakes one palette into the pixels.

    The engine picks a palette bank per draw call (sc_sub.c,
    njSetPaletteBankNumG), so any screen that carries a palette must stay
    indexed. Replacing scrscrn.ppg -- the in-game HUD -- broke the health
    bars, timer, super gauge and portraits.
    """

    def direct(self, **over):
        c = {"n": 0, "decoded": True, "transNums": 0, "pixel_raw": 0x82,
             "pixel_mode": "16bpp direct"}
        c.update(over)
        return {"palette_chunks": 0, "chunks": [c]}

    def test_palette_free_direct_colour_is_allowed(self):
        ok, why = direct32_safe(self.direct())
        self.assertTrue(ok)
        self.assertIsNone(why)

    def test_embedded_palette_is_refused(self):
        man = self.direct()
        man["palette_chunks"] = 4                      # scrscrn.ppg
        ok, why = direct32_safe(man)
        self.assertFalse(ok)
        self.assertIn("selects a bank per draw", why)

    def test_trans_table_is_refused(self):
        ok, why = direct32_safe(self.direct(transNums=8))
        self.assertFalse(ok)
        self.assertIn("trans table", why)

    def test_indexed_pixels_are_refused(self):
        ok, why = direct32_safe(self.direct(pixel_raw=0x81,
                                            pixel_mode="8bpp indexed"))
        self.assertFalse(ok)
        self.assertIn("not direct colour", why)

    def test_undecoded_chunks_are_ignored(self):
        man = self.direct()
        man["chunks"].append({"n": 1, "decoded": False, "transNums": 9,
                              "pixel_raw": 0x81, "pixel_mode": "8bpp indexed"})
        self.assertTrue(direct32_safe(man)[0])
