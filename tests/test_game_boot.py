"""The 3DS game app's boot sequence, run on the host with the stub game
(tools/make_game_stub.py): check the DOL fingerprint, boot it (runtime/wii_boot.c), run the
translated entry point, and present frames in VIWaitForRetrace by decoding the captured FIFO
(runtime/gx_cp.c). The driver below follows platform/3ds/source/game/game_main.c.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib

from tests.support import RUNTIME

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_game_stub  # noqa: E402

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "gx_cp.h"
#include "hw.h"
#include "wii_boot.h"

extern const uint32_t spm3ds_game_dol_size, spm3ds_game_dol_crc32, spm3ds_game_entry_address;
void spm3ds_game_entry(ppc_ctx *c);

static ppc_ctx ctx;
static spm3ds_hw hw;
static gx_cp cp;
static int frames;

void hle_VIWaitForRetrace(ppc_ctx *c) {
    size_t used;
    (void)c;
    gx_cp_clear_output(&cp);
    used = gx_cp_run(&cp, hw.fifo, hw.fifo_size);
    printf("frame %d fifo %lu used %lu triangles %u error %d\n", frames, (unsigned long)hw.fifo_size,
           (unsigned long)used, (unsigned)(cp.out_count / 3u), cp.error);
    spm3ds_hw_fifo_clear(&hw);
    if (++frames == 3) exit(0);
}

int main(int argc, char **argv) {
    FILE *f = fopen(argv[1], "rb");
    static uint8_t dol[1 << 20];
    size_t n;
    wii_dol d;
    wii_boot_info info;
    const char *why = "";
    (void)argc;
    if (!f) return 2;
    n = fread(dol, 1, sizeof dol, f);
    fclose(f);
    if (n != spm3ds_game_dol_size || wii_crc32(dol, n) != spm3ds_game_dol_crc32) {
        printf("mismatch size %lu crc %08x\n", (unsigned long)n, (unsigned)wii_crc32(dol, n));
        return 1;
    }
    if (wii_dol_parse(dol, n, &d, &why) || d.entry != spm3ds_game_entry_address) {
        printf("parse %s\n", why);
        return 1;
    }
    if (wii_boot_map_memory(&ctx) || wii_boot_load_dol(&ctx, dol, &d)) return 3;
    memset(&info, 0, sizeof info);
    wii_boot_globals(&ctx, &d, &info);
    spm3ds_hw_init(&hw);
    spm3ds_hw_attach(&ctx, &hw);
    gx_cp_init(&cp, NULL, NULL);
    printf("booted entry %08x\n", (unsigned)d.entry);
    spm3ds_game_entry(&ctx);
    printf("entry returned\n");
    return 4;
}
"""


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class GameBoot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="spm3ds-game-")
        gen = os.path.join(cls.tmp, "gen")
        cls.dol = os.path.join(cls.tmp, "main.dol")
        cls.dol_bytes, files = make_game_stub.build(gen, cls.dol)
        drv = os.path.join(cls.tmp, "driver.c")
        with open(drv, "w") as f:
            f.write(DRIVER)
        sources = [os.path.join(gen, n) for n in files if n.endswith(".c")]
        cls.exe = os.path.join(cls.tmp, "game")
        subprocess.run(["gcc", "-std=c99", "-O1", "-Wall", "-Wno-unused-label", "-Werror",
                        "-I", RUNTIME, "-I", gen, drv, *sources,
                        *(os.path.join(RUNTIME, n) for n in ("wii_boot.c", "mem.c", "hw.c",
                                                             "gx_cp.c")),
                        "-o", cls.exe, "-lm"], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_info_matches_the_dol(self):
        with open(os.path.join(self.tmp, "gen", "game_info.c")) as f:
            info = f.read()
        self.assertIn(f"spm3ds_game_dol_size = {len(self.dol_bytes)}u", info)
        self.assertIn(f"0x{zlib.crc32(self.dol_bytes):08X}u", info)

    def test_boots_and_presents_three_frames(self):
        out = subprocess.run([self.exe, self.dol], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[0], f"booted entry {make_game_stub.CODE_BASE:08x}")
        frames = [l.split() for l in lines[1:]]
        self.assertEqual(len(frames), 3)
        for k, fr in enumerate(frames):
            self.assertEqual(int(fr[1]), k)
            self.assertGreater(int(fr[3]), 0)
            self.assertEqual(fr[3], fr[5])            # whole FIFO consumed
            self.assertEqual(fr[7], "1")              # one triangle per frame
            self.assertEqual(fr[9], "0")

    def test_a_different_dol_is_refused(self):
        other = os.path.join(self.tmp, "other.dol")
        with open(other, "wb") as f:
            f.write(self.dol_bytes[:-4] + b"\x00\x00\x00\x01")
        out = subprocess.run([self.exe, other], capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 1)
        self.assertIn("mismatch", out.stdout)


if __name__ == "__main__":
    unittest.main()
