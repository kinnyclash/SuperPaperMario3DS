"""runtime/gx_tex.c against spm3ds/gx/textures.py on random texture data, every format."""
import os
import random
import shutil
import subprocess
import tempfile
import unittest

from spm3ds.gx.regs import TEX_FORMATS
from spm3ds.gx.textures import FORMATS, decode
from tests.support import RUNTIME

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include "gx_tex.h"
int main(int argc, char **argv) {
    unsigned fmt = (unsigned)atoi(argv[1]), w = (unsigned)atoi(argv[2]), h = (unsigned)atoi(argv[3]);
    uint32_t n = gx_tex_size(fmt, w, h), i;
    uint8_t *src = malloc(n ? n : 1), *dst = malloc(4u * w * h);
    FILE *f = fopen(argv[4], "rb");
    (void)argc;
    if (!f || fread(src, 1, n, f) != n) return 2;
    fclose(f);
    printf("%u %d\n", (unsigned)n, gx_tex_decode(fmt, src, w, h, dst));
    for (i = 0; i < 4u * w * h; i++) printf("%02x", dst[i]);
    printf("\n");
    return 0;
}
"""

SIZES = [(8, 8), (16, 8), (12, 10), (3, 5), (32, 4)]


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class CDecoderMatchesPython(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="spm3ds-tex-")
        src = os.path.join(cls.tmp, "d.c")
        with open(src, "w") as f:
            f.write(DRIVER)
        cls.exe = os.path.join(cls.tmp, "d")
        subprocess.run(["gcc", "-std=c99", "-Wall", "-Werror", "-I", RUNTIME, src,
                        os.path.join(RUNTIME, "gx_tex.c"), "-o", cls.exe], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_format_and_size(self):
        rng = random.Random(77)
        for fmt, name in TEX_FORMATS.items():
            f = FORMATS[name]
            for w, h in SIZES:
                with self.subTest(fmt=name, w=w, h=h):
                    size = (-(-w // f.tile_w)) * (-(-h // f.tile_h)) * f.tile_bytes
                    data = bytes(rng.getrandbits(8) for _ in range(size))
                    path = os.path.join(self.tmp, "tex.bin")
                    with open(path, "wb") as out:
                        out.write(data)
                    lines = subprocess.run([self.exe, str(fmt), str(w), str(h), path],
                                           check=True, capture_output=True,
                                           text=True).stdout.split()
                    self.assertEqual(int(lines[0]), size)
                    self.assertEqual(lines[1], "1")
                    got = bytes.fromhex(lines[2])
                    want = b"".join(bytes(px) for row in decode(name, data, w, h) for px in row)
                    self.assertEqual(got, want)

    def test_palette_formats_are_refused(self):
        path = os.path.join(self.tmp, "empty.bin")
        open(path, "wb").close()
        out = subprocess.run([self.exe, "9", "8", "8", path], capture_output=True, text=True)
        # gx_tex_size returns 0, so the driver reads nothing and decode reports 0.
        self.assertEqual(out.stdout.split()[:2], ["0", "0"])


if __name__ == "__main__":
    unittest.main()
