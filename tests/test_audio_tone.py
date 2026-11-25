"""The test-tone generator used by the 3DS audio output (runtime/audio_ring.h)."""
import math
import os
import shutil
import subprocess
import tempfile
import unittest

from tests.support import RUNTIME

DRIVER = r"""
#include <stdio.h>
#include <stdint.h>
#include "audio_ring.h"
static int16_t a[2 * 32000], b[2 * 32000];
int main(void) {
    float pa = 0.0f, pb = 0.0f;
    unsigned i;
    spm3ds_tone_fill(a, 32000, &pa, 440.0f, 32000.0f, 6000);
    for (i = 0; i < 32000; i += 1000) spm3ds_tone_fill(b + 2 * i, 1000, &pb, 440.0f, 32000.0f, 6000);
    for (i = 0; i < 2 * 32000; i++) printf("%d %d\n", a[i], b[i]);
    return 0;
}
"""


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class ToneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "t.c")
            with open(src, "w") as f:
                f.write(DRIVER)
            exe = os.path.join(tmp, "t")
            subprocess.run(["gcc", "-std=c99", "-Wall", "-Werror", "-I", RUNTIME, src, "-o", exe],
                           check=True)
            out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
        pairs = [tuple(map(int, line.split())) for line in out.splitlines()]
        cls.whole = [p[0] for p in pairs]
        cls.chunked = [p[1] for p in pairs]

    def test_channels_are_identical(self):
        self.assertEqual(self.whole[0::2], self.whole[1::2])

    def test_frequency_from_zero_crossings(self):
        left = self.whole[0::2]
        rising = sum(1 for i in range(1, len(left)) if left[i - 1] < 0 <= left[i])
        self.assertTrue(438 <= rising <= 442, rising)   # one second at 440 Hz

    def test_amplitude_and_shape(self):
        left = self.whole[0::2]
        self.assertTrue(5900 <= max(left) <= 6000, max(left))
        self.assertTrue(-6000 <= min(left) <= -5900, min(left))
        # Close to a true sine: compare one cycle-aligned sample per quarter period.
        worst = max(abs(left[i] - 6000 * math.sin(2 * math.pi * 440 * i / 32000))
                    for i in range(0, 32000, 7))
        self.assertLess(worst, 60)   # 1% of the amplitude (phase is kept in single precision)

    def test_phase_continues_across_calls(self):
        self.assertEqual(self.whole, self.chunked)


if __name__ == "__main__":
    unittest.main()
