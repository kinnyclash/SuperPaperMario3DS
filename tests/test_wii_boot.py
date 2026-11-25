"""Booting a Wii executable (runtime/wii_boot.c): DOL loading, the memory map with its
uncached mirrors, boot globals, and running the translated entry point on the result.

The DOL is built here from test code (no game data). Expected values for the globals come
from WiiBrew's "Memory map" table, as cited in runtime/wii_boot.h.
"""
import os
import struct
import subprocess
import tempfile
import unittest

from spm3ds.recomp import generate_c
from tests import helpers as h
from tests.helpers import BLR, build_dol, program_image
from tests.support import HAVE_GCC, RUNTIME

CODE = 0x80004000
DATA = 0x80010000
BSS = 0x80020000


def li32(reg, value):
    return [h.addis(reg, 0, value >> 16), h.ori(reg, reg, value & 0xFFFF)]


# Guest program: r3 = data[0] + data[1]; store it in the bss; read the MEM1 size global into
# r5; write through the uncached mirror and read back through the cached address into r7.
WORDS = (li32(4, DATA) + [h.lwz(3, 4, 0), h.lwz(6, 4, 4), h.add(3, 3, 6)]
         + li32(8, BSS) + [h.stw(3, 8, 16)]
         + li32(9, 0x80000028) + [h.lwz(5, 9, 0)]
         + li32(10, 0xC0020000) + li32(11, 0xCAFEF00D) + [h.stw(11, 10, 32)]
         + [h.lwz(7, 8, 32), BLR])
DATA_BYTES = struct.pack(">II", 1000, 234) + bytes(56)
FST = bytes([1, 0, 0, 0]) + struct.pack(">II", 0, 1) + b"\x00"   # root only

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "wii_boot.h"
void fn_@ENTRY@(ppc_ctx *c);
static uint8_t *slurp(const char *path, size_t *n) {
    FILE *f = fopen(path, "rb"); long k; uint8_t *p;
    if (!f) exit(2);
    fseek(f, 0, SEEK_END); k = ftell(f); fseek(f, 0, SEEK_SET);
    p = malloc((size_t)k); if (fread(p, 1, (size_t)k, f) != (size_t)k) exit(3); fclose(f);
    *n = (size_t)k; return p;
}
int main(int argc, char **argv) {
    static ppc_ctx c;
    size_t n, fn;
    uint8_t *dol = slurp(argv[1], &n), *fst = slurp(argv[2], &fn);
    static const uint8_t id[6] = {'T', 'E', 'S', 'T', '0', '1'};
    wii_dol d;
    wii_boot_info info;
    const char *why = "";
    int rc = wii_dol_parse(dol, n, &d, &why);
    uint32_t fst_at;
    unsigned i;
    printf("parse %d %s\n", rc, why);
    if (argc > 3) return 0;
    printf("map %d\n", wii_boot_map_memory(&c));
    printf("load %d entry %08x end %08x\n", wii_boot_load_dol(&c, dol, &d), (unsigned)d.entry,
           (unsigned)d.end);
    memset(&info, 0, sizeof info);
    info.game_id = id; info.fst = fst; info.fst_size = (uint32_t)fn; info.video_mode = 1;
    fst_at = wii_boot_globals(&c, &d, &info);
    printf("fst %08x first %08x\n", (unsigned)fst_at, (unsigned)ppc_r32(&c, fst_at));
    for (i = 0; i < 26; i++) {
        static const uint32_t g[] = {0x80000018, 0x80000020, 0x80000024, 0x80000028, 0x8000002C,
            0x80000030, 0x80000034, 0x80000038, 0x8000003C, 0x800000CC, 0x800000F0, 0x800000F8,
            0x800000FC, 0x80003100, 0x80003104, 0x8000310C, 0x80003110, 0x80003118, 0x8000311C,
            0x80003120, 0x80003124, 0x80003128, 0x80003130, 0x80003134, 0x80003138, 0x80003160};
        printf("g %08x %08x\n", (unsigned)g[i], (unsigned)ppc_r32(&c, g[i]));
    }
    printf("id %c%c%c%c%c%c wc24 %08x\n", ppc_r8(&c, 0x80000000), ppc_r8(&c, 0x80000001),
           ppc_r8(&c, 0x80000002), ppc_r8(&c, 0x80000003), ppc_r8(&c, 0x80000004),
           ppc_r8(&c, 0x80000005), (unsigned)ppc_r32(&c, 0x80003180));
    fn_@ENTRY@(&c);
    printf("run r3 %u r5 %08x r7 %08x bss %u\n", (unsigned)c.r[3], (unsigned)c.r[5],
           (unsigned)c.r[7], (unsigned)ppc_r32(&c, 0x80020010));
    printf("mirror %08x\n", (unsigned)ppc_r32(&c, 0xC0004000));
    printf("committed %lu\n", (unsigned long)ppc_committed_bytes(&c));
    ppc_unmap_paged(&c);
    printf("unmapped\n");
    return 0;
}
"""


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class WiiBoot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="spm3ds-boot-")
        d = cls.tmp.name
        cls.dol = os.path.join(d, "test.dol")
        with open(cls.dol, "wb") as f:
            f.write(build_dol([(CODE, WORDS)], CODE, data=[(DATA, DATA_BYTES)],
                              bss=(BSS, 0x100)))
        cls.fst = os.path.join(d, "fst.img")
        with open(cls.fst, "wb") as f:
            f.write(FST)
        gen = os.path.join(d, "game.c")
        with open(gen, "w") as f:
            f.write(generate_c(program_image(WORDS, CODE), CODE))
        drv = os.path.join(d, "driver.c")
        with open(drv, "w") as f:
            f.write(DRIVER.replace("@ENTRY@", f"{CODE:08X}"))
        cls.exe = os.path.join(d, "boot")
        subprocess.run(["gcc", "-std=c99", "-Wall", "-Wno-unused-label", "-I", RUNTIME, drv, gen,
                        os.path.join(RUNTIME, "wii_boot.c"), os.path.join(RUNTIME, "mem.c"),
                        "-o", cls.exe, "-lm"], check=True)
        out = subprocess.run([cls.exe, cls.dol, cls.fst], check=True, capture_output=True,
                             text=True).stdout
        cls.lines = out.splitlines()
        cls.globals = {int(l.split()[1], 16): int(l.split()[2], 16) for l in cls.lines
                       if l.startswith("g ")}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def line(self, prefix):
        return next(l for l in self.lines if l.startswith(prefix)).split()

    def test_parse_map_and_load(self):
        self.assertEqual(self.line("parse")[1], "0")
        self.assertEqual(self.line("map")[1], "0")
        load = self.line("load")
        self.assertEqual(load[1], "0")
        self.assertEqual(int(load[3], 16), CODE)
        self.assertEqual(int(load[5], 16), BSS + 0x100)

    def test_globals_follow_the_wiibrew_table(self):
        g = self.globals
        self.assertEqual(g[0x80000018], 0x5D1C9EA3)
        self.assertEqual(g[0x80000020], 0x0D15EA5E)
        self.assertEqual(g[0x80000024], 1)
        self.assertEqual(g[0x80000028], 0x01800000)
        self.assertEqual(g[0x8000002C], 0x23)
        self.assertEqual(g[0x80000030], BSS + 0x100)          # arena low after the bss
        self.assertEqual(g[0x80000034], g[0x80000038])        # arena high = FST start
        self.assertEqual(g[0x8000003C], len(FST))
        self.assertEqual(g[0x800000CC], 1)                    # PAL, as passed in
        self.assertEqual(g[0x800000F8], 0x0E7BE2C0)
        self.assertEqual(g[0x800000FC], 0x2B73A840)
        self.assertEqual((g[0x80003118], g[0x8000311C]), (0x04000000, 0x04000000))
        self.assertEqual((g[0x80003120], g[0x80003124], g[0x80003128]),
                         (0x93400000, 0x90000800, 0x933E0000))
        self.assertEqual((g[0x8000310C], g[0x80003110]), (g[0x80000030], g[0x80000034]))
        self.assertEqual(g[0x80003160], 0)
        self.assertEqual(self.line("id")[1], "TEST01")
        self.assertEqual(int(self.line("id")[3], 16), int.from_bytes(b"TEST", "big"))

    def test_fst_is_copied_below_the_top_of_mem1(self):
        fst = self.line("fst")
        addr = int(fst[1], 16)
        self.assertEqual(addr % 32, 0)
        self.assertTrue(0x81700000 < addr < 0x81800000)
        self.assertEqual(int(fst[3], 16), 0x01000000)          # first word of the root entry

    def test_translated_entry_runs_on_the_booted_image(self):
        run = self.line("run")
        self.assertEqual(int(run[2]), 1234)                     # data section was loaded
        self.assertEqual(int(run[4], 16), 0x01800000)           # read a boot global
        self.assertEqual(int(run[6], 16), 0xCAFEF00D)           # uncached write, cached read
        self.assertEqual(int(run[8]), 1234)                     # bss store
        self.assertEqual(int(self.line("mirror")[1], 16), WORDS[0])   # code via 0xC...

    def test_only_touched_pages_are_committed(self):
        committed = int(self.line("committed")[1])
        self.assertLess(committed, 16 * 65536)                 # a handful of 64 KiB pages
        self.assertIn("unmapped", self.lines)

    def test_bad_files_are_rejected(self):
        d = self.tmp.name
        cases = {
            "short": b"\x00" * 64,
            "entry": build_dol([(CODE, WORDS)], 0x80100000),
            "past_end": build_dol([(CODE, WORDS)], CODE)[:-8],
        }
        for name, blob in cases.items():
            with self.subTest(case=name):
                path = os.path.join(d, f"{name}.dol")
                with open(path, "wb") as f:
                    f.write(blob)
                out = subprocess.run([self.exe, path, self.fst, "parse-only"], check=True,
                                     capture_output=True, text=True).stdout
                self.assertLess(int(out.split()[1]), 0, out)


if __name__ == "__main__":
    unittest.main()
