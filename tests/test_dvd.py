"""Wii DVD file access HLE (runtime/dvd.c).

The FST is built here from the public format description (12-byte entries, string table
after the entries), independently of the C parser. Files are made-up test data written to a
temporary directory; no disc data is used.
"""
import os
import struct
import subprocess
import tempfile
import unittest

from tests.support import HAVE_GCC, RUNTIME

FI = 0x80100100        # guest DVDFileInfo
BUF = 0x80100800       # guest read buffer
PATH = 0x80100400      # guest path string
CALLBACK = 0x80001234  # guest callback address (recorded by the stub dispatcher)

TREE = [  # (is_dir, name, parent for dirs, content for files)
    (True, "", None, None),
    (False, "opening.txt", None, b"opening sequence data 0123456789"),
    (True, "sound", 0, None),
    (False, "bgm.dat", None, bytes(range(200))),
    (False, "se.dat", None, b"sfx" * 50),
    (True, "map", 0, None),
    (False, "aa1_01.dat", None, b"map data for area one"),
]


def build_fst(tree):
    """Encode the FST: dirs store (parent, next index); files store (offset, length)."""
    names = bytearray()
    offsets = []
    for _, name, _, _ in tree:
        offsets.append(len(names))
        names += name.encode() + b"\x00"
    # next index of each directory: one past its last descendant (computed from the layout)
    nexts = {0: len(tree), 2: 5, 5: 7}
    out = bytearray()
    for i, (is_dir, _, parent, content) in enumerate(tree):
        if is_dir:
            out += struct.pack(">III", (1 << 24) | offsets[i], parent or 0, nexts[i])
        else:
            out += struct.pack(">III", offsets[i], 0, len(content))
    return bytes(out + names)


DRIVER = r"""
#include <stdio.h>
#include <string.h>
#include "hle.h"

#define BASE 0x80100000u
static uint8_t mem[1u << 16];
static ppc_ctx c;
static uint32_t cb_target, cb_r3, cb_r4;

void ppc_dispatch(ppc_ctx *ctx, uint32_t target) {
    cb_target = target; cb_r3 = ctx->r[3]; cb_r4 = ctx->r[4];
}

static void put(uint32_t ea, const char *s) { memcpy(mem + (ea - BASE), s, strlen(s) + 1); }
static int32_t entry(const char *p) { put(PATH, p); c.r[3] = PATH; hle_DVDConvertPathToEntrynum(&c); return (int32_t)c.r[3]; }
static uint32_t dvd_open(const char *p) { put(PATH, p); c.r[3] = PATH; c.r[4] = FI; hle_DVDOpen(&c); return c.r[3]; }
static int32_t dvd_read(uint32_t len, uint32_t off) {
    c.r[3] = FI; c.r[4] = BUF; c.r[5] = len; c.r[6] = off; c.r[7] = 2; hle_DVDReadPrio(&c);
    return (int32_t)c.r[3];
}
static void dump(const char *tag, uint32_t n) {
    uint32_t i; printf("%s", tag);
    for (i = 0; i < n; i++) printf(" %02x", mem[BUF - BASE + i]);
    printf("\n");
}

int main(int argc, char **argv) {
    (void)argc;
    c.regions[0].base = BASE; c.regions[0].size = sizeof mem; c.regions[0].data = mem;
    c.nregions = 1;
    spm3ds_dvd_set_root(argv[1]);
    printf("fst %d\n", spm3ds_dvd_load_fst_file(argv[2]));
    printf("e_se %d\n", entry("/sound/se.dat"));
    printf("e_upper %d\n", entry("SOUND/SE.DAT"));
    printf("e_dir %d\n", entry("/map"));
    printf("e_root %d\n", entry("/"));
    printf("e_missing %d\n", entry("/sound/none.dat"));
    printf("e_file_as_dir %d\n", entry("/opening.txt/x"));
    printf("open_bgm %u\n", dvd_open("/sound/bgm.dat"));
    printf("len_bgm %u\n", (unsigned)ppc_r32(&c, FI + SPM3DS_DVD_FI_LENGTH));
    printf("read_bgm %d\n", dvd_read(16, 4));
    dump("bytes_bgm", 16);
    printf("read_past_end %d\n", dvd_read(64, 180));
    c.r[3] = FI; hle_DVDClose(&c);
    printf("read_closed %d\n", dvd_read(4, 0));
    c.r[3] = 6; c.r[4] = FI; hle_DVDFastOpen(&c);
    printf("fast_open %u\n", c.r[3]);
    printf("read_map %d\n", dvd_read(8, 0));
    dump("bytes_map", 8);
    c.r[3] = 2; c.r[4] = FI; hle_DVDFastOpen(&c);
    printf("fast_open_dir %u\n", c.r[3]);
    dvd_open("/sound/se.dat");
    c.r[3] = FI; c.r[4] = BUF; c.r[5] = 6; c.r[6] = 0; c.r[7] = CALLBACK; c.r[8] = 2;
    hle_DVDReadAsyncPrio(&c);
    printf("async %u cb %08x %d %08x\n", c.r[3], cb_target, (int32_t)cb_r3, cb_r4);
    dump("bytes_se", 6);
    printf("unsafe %u\n", dvd_open("../outside.txt"));
    spm3ds_dvd_reset();
    printf("direct %u\n", dvd_open("/opening.txt"));
    printf("read_direct %d\n", dvd_read(7, 0));
    dump("bytes_direct", 7);
    return 0;
}
"""


@unittest.skipUnless(HAVE_GCC, "host gcc not available")
class DvdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="spm3ds-dvd-")
        root = os.path.join(cls.tmp.name, "files")
        paths = {1: "opening.txt", 3: "sound/bgm.dat", 4: "sound/se.dat", 6: "map/aa1_01.dat"}
        for idx, rel in paths.items():
            full = os.path.join(root, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "wb") as f:
                f.write(TREE[idx][3])
        with open(os.path.join(cls.tmp.name, "outside.txt"), "wb") as f:
            f.write(b"must not be readable")
        fst = os.path.join(cls.tmp.name, "fst.img")
        with open(fst, "wb") as f:
            f.write(build_fst(TREE))
        drv = os.path.join(cls.tmp.name, "driver.c")
        defs = (f"#define FI 0x{FI:08X}u\n#define BUF 0x{BUF:08X}u\n"
                f"#define PATH 0x{PATH:08X}u\n#define CALLBACK 0x{CALLBACK:08X}u\n")
        with open(drv, "w") as f:
            f.write(defs + DRIVER)
        exe = os.path.join(cls.tmp.name, "driver")
        subprocess.run(["gcc", "-std=c99", "-Wall", "-Werror", "-I", RUNTIME, drv,
                        os.path.join(RUNTIME, "dvd.c"), "-o", exe], check=True)
        out = subprocess.run([exe, root, fst], check=True, capture_output=True,
                             text=True).stdout
        cls.out = {line.split()[0]: line.split()[1:] for line in out.splitlines()}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def val(self, key):
        return int(self.out[key][0])

    def bytes_of(self, key):
        return bytes(int(x, 16) for x in self.out[key])

    def test_fst_lookup(self):
        self.assertEqual(self.val("fst"), len(TREE))
        self.assertEqual(self.val("e_se"), 4)
        self.assertEqual(self.val("e_upper"), 4)      # case-insensitive
        self.assertEqual(self.val("e_dir"), 5)
        self.assertEqual(self.val("e_root"), 0)
        self.assertEqual(self.val("e_missing"), -1)
        self.assertEqual(self.val("e_file_as_dir"), -1)

    def test_open_and_read(self):
        self.assertEqual(self.val("open_bgm"), 1)
        self.assertEqual(self.val("len_bgm"), 200)
        self.assertEqual(self.val("read_bgm"), 16)
        self.assertEqual(self.bytes_of("bytes_bgm"), bytes(range(4, 20)))
        self.assertEqual(self.val("read_past_end"), 20)   # 200 - 180 bytes remain
        self.assertEqual(self.val("read_closed"), -1)

    def test_fast_open_by_entry_number(self):
        self.assertEqual(self.val("fast_open"), 1)
        self.assertEqual(self.bytes_of("bytes_map"), b"map data")
        self.assertEqual(self.val("fast_open_dir"), 0)    # directories cannot be opened

    def test_async_read_calls_the_callback(self):
        parts = self.out["async"]
        self.assertEqual(parts[0], "1")
        self.assertEqual(int(parts[2], 16), CALLBACK)
        self.assertEqual(int(parts[3]), 6)
        self.assertEqual(int(parts[4], 16), FI)
        self.assertEqual(self.bytes_of("bytes_se"), b"sfxsfx")

    def test_paths_cannot_leave_the_root(self):
        self.assertEqual(self.val("unsafe"), 0)

    def test_without_fst_paths_map_to_the_root(self):
        self.assertEqual(self.val("direct"), 1)
        self.assertEqual(self.bytes_of("bytes_direct"), b"opening")


if __name__ == "__main__":
    unittest.main()
