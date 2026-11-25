"""Input mapping and audio foundations.

Python (spm3ds/input.py) is checked directly. The C headers (runtime/input.h,
runtime/audio_ring.h) are compiled with gcc in a small driver, and their output is compared
with values worked out by hand in the comments.
"""
import os
import subprocess
import tempfile
import unittest

from spm3ds import input as wii_input
from tests.support import HAVE_GCC, RUNTIME

KEY_A, KEY_B, KEY_SELECT, KEY_START = 1, 2, 4, 8
KEY_DRIGHT, KEY_DLEFT, KEY_DUP, KEY_DDOWN = 16, 32, 64, 128
KEY_X, KEY_Y = 1 << 10, 1 << 11


class PythonMappingTests(unittest.TestCase):
    def test_mapping_is_valid(self):
        wii_input.validate()

    def test_a_and_one_map_to_a_and_x(self):
        keys, unmapped = wii_input.convert(0x0800 | 0x0200)
        self.assertEqual(keys, KEY_A | KEY_X)
        self.assertEqual(unmapped, [])

    def test_home_is_reported_not_guessed(self):
        keys, unmapped = wii_input.convert(0x8000)
        self.assertEqual(keys, 0)
        self.assertEqual(unmapped, ["Home"])

    def test_dpad_bits(self):
        keys, _ = wii_input.convert(0x0001 | 0x0002 | 0x0008 | 0x0004)
        self.assertEqual(keys, KEY_DLEFT | KEY_DRIGHT | KEY_DUP | KEY_DDOWN)


@unittest.skipUnless(HAVE_GCC, "gcc not available")
class CHeaderTests(unittest.TestCase):
    DRIVER = r'''
#include <stdio.h>
#include <stdint.h>
#include "input.h"
#include "audio_ring.h"
int main(void) {
    int16_t out[8]; int16_t store[8]; spm3ds_ring r; uint32_t n, i;
    uint8_t pcm[8] = {0x01,0x00, 0xFF,0xFE, 0x7F,0xFF, 0x80,0x00};
    /* input: A|One = 0x0800|0x0200 -> KEY_A|KEY_X = 0x401 */
    printf("KEYS %u\n", (unsigned)spm3ds_wiimote_to_keys(0x0800u | 0x0200u));
    /* pcm: 256 at unity = 256; -2 at volume 128 = -1 (truncated toward zero);
       32767 at volume 512 clamps to 32767; -32768 at unity stays -32768. */
    spm3ds_pcm16_be_to_le(out, pcm, 4, 256);
    printf("PCM %d %d %d %d\n", out[0], out[1], out[2], out[3]);
    spm3ds_pcm16_be_to_le(out, pcm + 2, 1, 128);
    printf("PCM2 %d\n", out[0]);
    spm3ds_pcm16_be_to_le(out, pcm + 4, 1, 512);
    printf("PCM3 %d\n", out[0]);
    /* ring of 8: write 10 -> 8 stored; read 12 -> 8 values then 4 zeros. */
    spm3ds_ring_init(&r, store, 8);
    for (i = 0; i < 10; i++) out[0] = 0; {
        int16_t src[10]; for (i = 0; i < 10; i++) src[i] = (int16_t)(i + 1);
        n = spm3ds_ring_write(&r, src, 10);
        printf("WRITE %u FILL %u\n", (unsigned)n, (unsigned)spm3ds_ring_fill(&r));
    }
    {
        int16_t dst[12]; uint32_t k = spm3ds_ring_read(&r, dst, 12);
        printf("READ %u FILL %u\n", (unsigned)k, (unsigned)spm3ds_ring_fill(&r));
        for (i = 0; i < 12; i++) printf("%d ", dst[i]);
        printf("\n");
    }
    return 0;
}
'''

    def test_c_matches_hand_values(self):
        with tempfile.TemporaryDirectory() as workdir:
            src = os.path.join(workdir, "driver.c")
            exe = os.path.join(workdir, "driver")
            with open(src, "w", encoding="utf-8") as f:
                f.write(self.DRIVER)
            subprocess.run(["gcc", "-std=c99", "-Wall", "-I", RUNTIME, "-o", exe, src],
                           check=True, capture_output=True, text=True)
            out = subprocess.run([exe], check=True, capture_output=True, text=True).stdout
        lines = out.splitlines()
        self.assertEqual(lines[0], f"KEYS {KEY_A | KEY_X}")
        self.assertEqual(lines[1], "PCM 256 -2 32767 -32768")
        self.assertEqual(lines[2], "PCM2 -1")
        self.assertEqual(lines[3], "PCM3 32767")
        self.assertEqual(lines[4], "WRITE 8 FILL 8")
        self.assertEqual(lines[5], "READ 8 FILL 0")
        self.assertEqual(lines[6], "1 2 3 4 5 6 7 8 0 0 0 0 ")


if __name__ == "__main__":
    unittest.main()
