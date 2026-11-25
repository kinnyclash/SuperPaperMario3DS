"""Host tests for the PICA200 tiled texture layout (platform/3ds/source/gfx/tiling.c).

The C file has no libctru dependency, so it is compiled with gcc and driven by a small program.
Expected values are worked out by hand in the comments.
"""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "platform", "3ds", "source", "gfx")

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include "tiling.h"
int main(int argc, char **argv) {
    unsigned w = (unsigned)atoi(argv[1]), h = (unsigned)atoi(argv[2]);
    if (argc > 3 && argv[3][0] == 'p') {
        printf("%u %u %u %u %u\n", tiling_pow2(1), tiling_pow2(8), tiling_pow2(9),
               tiling_pow2(640), tiling_pow2(4096));
        return 0;
    }
    if (argc > 3 && argv[3][0] == 'l') {
        /* 3x2 source: texel (x, y) = (10x + y, 100, 200, 255). Converted to 8x8. */
        uint8_t src[3 * 2 * 4], dst[8 * 8 * 4];
        unsigned x, y;
        for (y = 0; y < 2; y++)
            for (x = 0; x < 3; x++) {
                uint8_t *p = src + 4 * (y * 3 + x);
                p[0] = (uint8_t)(10 * x + y); p[1] = 100; p[2] = 200; p[3] = 255;
            }
        tiling_linear_to_tiled_rgba8(dst, 8, 8, src, 3, 2);
        for (y = 0; y < 8; y++)
            for (x = 0; x < 8; x++)
                printf("%u ", dst[tiling_offset(8, x, y, 4) + 3]);   /* red byte (ABGR) */
        printf("\n");
        return 0;
    }
    if (argc > 3 && argv[3][0] == 'f') {
        uint8_t *buf = calloc((size_t)w * h * 4, 1);
        tiling_fill_checker_rgba8(buf, w, h);
        for (unsigned i = 0; i < 4; i++) printf("%u ", buf[i]);
        printf("\n");
        free(buf);
        return 0;
    }
    for (unsigned y = 0; y < h; y++)
        for (unsigned x = 0; x < w; x++)
            printf("%u\n", tiling_offset(w, x, y, 4));
    return 0;
}
"""


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class TilingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="spm3ds-tiling-")
        drv = os.path.join(cls.tmp, "driver.c")
        with open(drv, "w") as f:
            f.write(DRIVER)
        cls.exe = os.path.join(cls.tmp, "tiling_driver")
        subprocess.run(
            ["gcc", "-std=c11", "-Wall", "-Werror", "-I", SRC, drv,
             os.path.join(SRC, "tiling.c"), "-o", cls.exe],
            check=True,
        )

    def offsets(self, w, h):
        out = subprocess.run([self.exe, str(w), str(h)], check=True,
                             capture_output=True, text=True).stdout
        return [int(v) for v in out.split()]

    def test_morton_order_inside_tile(self):
        # (x=1,y=0) -> bit0 of x -> index 1; (0,1) -> bit0 of y -> index 2; (1,1) -> 3.
        # (2,0) -> bit1 of x -> bit 2 -> index 4; (7,7) -> all six bits -> 63.
        offs = self.offsets(8, 8)
        self.assertEqual(offs[0], 0)            # (0,0)
        self.assertEqual(offs[1], 4)            # (1,0): index 1 * 4 bytes
        self.assertEqual(offs[8], 8)            # (0,1): index 2 * 4 bytes
        self.assertEqual(offs[9], 12)           # (1,1): index 3 * 4 bytes
        self.assertEqual(offs[2], 16)           # (2,0): index 4 * 4 bytes
        self.assertEqual(offs[63], 63 * 4)      # (7,7): index 63 * 4 bytes

    def test_tiles_are_laid_out_row_by_row(self):
        # width 64 -> 8 tiles per row, each tile 64 texels * 4 bytes = 256 bytes.
        offs = self.offsets(64, 64)
        self.assertEqual(offs[8], 256)          # (8,0) starts tile 1
        self.assertEqual(offs[8 * 64], 8 * 256) # (0,8) starts tile 8 (the second tile row)

    def test_every_texel_has_a_unique_slot(self):
        w = h = 32
        offs = self.offsets(w, h)
        self.assertEqual(len(offs), w * h)
        self.assertEqual(sorted(offs), list(range(0, w * h * 4, 4)))

    def test_checker_fill_bytes(self):
        # Texel (0,0): check = 0 -> R=0 (gradient at x=0), G=0 (gradient at y=0), B=128, A=255,
        # stored in the PICA order A, B, G, R (see tiling.h).
        out = subprocess.run([self.exe, "64", "64", "f"], check=True,
                             capture_output=True, text=True).stdout.split()
        self.assertEqual([int(v) for v in out], [255, 128, 0, 0])


    def test_pow2_sizes(self):
        out = subprocess.run([self.exe, "8", "8", "p"], check=True, capture_output=True,
                             text=True).stdout.split()
        self.assertEqual([int(v) for v in out], [8, 8, 16, 1024, 1024])

    def test_linear_to_tiled_with_edge_repeat(self):
        out = subprocess.run([self.exe, "8", "8", "l"], check=True, capture_output=True,
                             text=True).stdout.split()
        red = [[int(out[8 * y + x]) for x in range(8)] for y in range(8)]
        self.assertEqual(red[0][:3], [0, 10, 20])
        self.assertEqual(red[1][:3], [1, 11, 21])
        self.assertEqual(red[0][7], 20)     # last column repeats
        self.assertEqual(red[7][0], 1)      # last row repeats
        self.assertEqual(red[7][7], 21)


if __name__ == "__main__":
    unittest.main()
