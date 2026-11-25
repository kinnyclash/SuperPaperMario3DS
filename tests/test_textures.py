"""GX texture decoders (spm3ds/gx/textures.py). Every expected pixel is worked out by hand in
the comments, from the bit layout stated for each format."""
import unittest

from spm3ds.gx.textures import decode


def blank(n):
    return bytearray(n)


class TextureTests(unittest.TestCase):
    def test_i4_expands_nibble_by_17_and_high_nibble_comes_first(self):
        data = blank(32)
        data[0] = 0x12                      # pixel 0 = 1 -> 17, pixel 1 = 2 -> 34
        rows = decode("I4", bytes(data), 8, 8)
        self.assertEqual(rows[0][0], (17, 17, 17, 255))
        self.assertEqual(rows[0][1], (34, 34, 34, 255))

    def test_i4_second_tile_starts_after_32_bytes(self):
        data = blank(64)
        data[32] = 0xF0                     # first pixel of the second tile = 15 -> 255
        rows = decode("I4", bytes(data), 16, 8)
        self.assertEqual(rows[0][8], (255, 255, 255, 255))

    def test_i8_pixel_is_the_byte(self):
        data = blank(32)
        data[0] = 0x80
        rows = decode("I8", bytes(data), 8, 4)
        self.assertEqual(rows[0][0], (128, 128, 128, 255))

    def test_ia4_high_nibble_is_alpha_low_is_intensity(self):
        data = blank(32)
        data[0] = 0xF3                      # A = 15 -> 255, I = 3 -> 51
        rows = decode("IA4", bytes(data), 8, 4)
        self.assertEqual(rows[0][0], (51, 51, 51, 255))

    def test_ia8_alpha_byte_first(self):
        data = blank(32)
        data[0], data[1] = 0x80, 0x40       # A = 128, I = 64
        rows = decode("IA8", bytes(data), 4, 4)
        self.assertEqual(rows[0][0], (64, 64, 64, 128))

    def test_rgb565_primaries(self):
        data = bytes([0xF8, 0x00, 0x07, 0xE0, 0x00, 0x1F] + [0] * 26)
        rows = decode("RGB565", data, 4, 4)
        self.assertEqual(rows[0][0], (255, 0, 0, 255))
        self.assertEqual(rows[0][1], (0, 255, 0, 255))
        self.assertEqual(rows[0][2], (0, 0, 255, 255))

    def test_rgb5a3_opaque_and_translucent_forms(self):
        data = bytes([0x80, 0x00, 0x2F, 0x00] + [0] * 28)   # opaque black; ARGB3444 A=2, R=15
        rows = decode("RGB5A3", data, 4, 4)
        self.assertEqual(rows[0][0], (0, 0, 0, 255))
        self.assertEqual(rows[0][1], (255, 0, 0, 2 * 255 // 7))   # alpha 72

    def test_rgba8_pairs_are_split_across_two_halves_of_the_tile(self):
        data = blank(64)
        data[0], data[1] = 0x10, 0x20        # pixel 0: A = 0x10, R = 0x20
        data[32], data[33] = 0x30, 0x40      # pixel 0: G = 0x30, B = 0x40
        rows = decode("RGBA8", bytes(data), 4, 4)
        self.assertEqual(rows[0][0], (0x20, 0x30, 0x40, 0x10))

    def test_cmpr_block_colours_and_indices(self):
        data = blank(32)
        data[0:2] = bytes([0xF8, 0x00])      # c0 = red (0xF800)
        data[2:4] = bytes([0x00, 0x1F])      # c1 = blue (0x001F); c0 > c1 so four colours
        data[4] = 0x1B                       # indices 00 01 10 11 across row 0
        rows = decode("CMPR", bytes(data), 8, 8)
        self.assertEqual(rows[0][0], (255, 0, 0, 255))      # index 0 = c0
        self.assertEqual(rows[0][1], (0, 0, 255, 255))      # index 1 = c1
        self.assertEqual(rows[0][2], (170, 0, 85, 255))     # (2*c0 + c1) / 3
        self.assertEqual(rows[0][3], (85, 0, 170, 255))     # (c0 + 2*c1) / 3

    def test_cmpr_with_c0_not_above_c1_has_transparent_index_3(self):
        data = blank(32)
        data[0:2] = bytes([0x00, 0x1F])      # c0 = blue
        data[2:4] = bytes([0xF8, 0x00])      # c1 = red; c0 < c1
        data[4] = 0xC0                       # index 3 for pixel 0
        rows = decode("CMPR", bytes(data), 8, 8)
        self.assertEqual(rows[0][0], (0, 0, 0, 0))

    def test_crop_removes_padding(self):
        data = blank(32)
        rows = decode("I8", bytes(data), 5, 3)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len(rows[0]), 5)

    def test_short_data_is_refused(self):
        with self.assertRaises(ValueError):
            decode("I8", bytes(16), 8, 4)


if __name__ == "__main__":
    unittest.main()
