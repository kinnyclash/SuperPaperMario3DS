"""Wii OS compatibility layer: guest heap, timers and sandboxed file reads.

A C driver calls the HLE functions directly on a guest context, as generated code does. The
Python side checks the results against independent expectations: exact integer division
for timer ticks, and the file bytes as written by the test.
"""
import os
import subprocess
import tempfile
import unittest

from tests.support import HAVE_GCC, RUNTIME

DRIVER = r"""
#include <stdio.h>
#include <string.h>
#include "hle.h"

#define BASE 0x80100000u
static uint8_t mem[1u << 20];
static ppc_ctx c;
static int failures = 0;
static uint64_t fake_ns = 0;

#define CHECK(cond) do { if (!(cond)) { \
    printf("FAIL line %d: %s\n", __LINE__, #cond); failures++; } } while (0)

static uint64_t fake_clock(void) { return fake_ns; }

static uint32_t heap_alloc(uint32_t heap, uint32_t size) {
    c.r[3] = heap; c.r[4] = size; hle_OSAllocFromHeap(&c); return c.r[3];
}
static uint32_t heap_free(uint32_t heap, uint32_t p) {
    c.r[3] = heap; c.r[4] = p; hle_OSFreeToHeap(&c); return c.r[3];
}
static void put_string(uint32_t ea, const char *s) {
    memcpy(mem + (ea - BASE), s, strlen(s) + 1);
}
static uint32_t fs_open(const char *path) {
    put_string(BASE + 0x800, path);
    c.r[3] = BASE + 0x800; hle_spm3ds_FSOpen(&c); return c.r[3];
}

int main(int argc, char **argv) {
    uint32_t heap, a, b, d, e, big, h, got, i;
    if (argc < 2) return 2;
    memset(&c, 0, sizeof c);
    c.regions[0].base = BASE; c.regions[0].size = sizeof mem; c.regions[0].data = mem;
    c.nregions = 1;
    spm3ds_fs_set_root(argv[1]);

    /* heap */
    c.r[3] = BASE; c.r[4] = BASE + 0x8000u;
    hle_OSCreateHeap(&c);
    heap = c.r[3];
    CHECK(heap == BASE);
    a = heap_alloc(heap, 100); b = heap_alloc(heap, 100); d = heap_alloc(heap, 100);
    CHECK(a != 0 && b != 0 && d != 0);
    CHECK(a % 32 == 0);
    CHECK(b - a == 160);            /* 100 + 32 header, rounded up to 32 */
    CHECK(d > b);
    CHECK(heap_free(heap, a) == 0);
    CHECK(heap_free(heap, b) == 0);
    CHECK(heap_free(heap, b) == 0xFFFFFFFFu);   /* double free is refused */
    e = heap_alloc(heap, 280);      /* 280 + 32 header = 312, rounded to 320: fits only if a and b were merged */
    CHECK(e == a);
    CHECK(heap_alloc(heap, 0) == 0);
    CHECK(heap_alloc(heap, 0x7FFFFFFFu) == 0);
    big = heap_alloc(heap, 0x10000u);   /* larger than the whole heap */
    CHECK(big == 0);
    c.r[3] = BASE + 0x100u; c.r[4] = 8; hle_OSAllocFromHeap(&c);
    CHECK(c.r[3] == 0);             /* not a heap */

    /* timers: print results for the Python side to check exactly */
    spm3ds_set_clock(fake_clock);
    {
        static const uint64_t samples[] = {0ull, 1ull, 1234567ull, 4000000000ull,
                                           1000000000000ull, 1099511627793ull};
        for (i = 0; i < sizeof samples / sizeof samples[0]; i++) {
            fake_ns = samples[i];
            hle_OSGetTime(&c);
            printf("TICKS %llu %u %u\n", (unsigned long long)fake_ns,
                   (unsigned)c.r[3], (unsigned)c.r[4]);
            hle_OSGetTick(&c);
            CHECK(c.r[3] == (uint32_t)((fake_ns / 4000ull) * 243ull
                                      + ((fake_ns % 4000ull) * 243ull) / 4000ull));
        }
    }

    /* files */
    h = fs_open("data.bin");
    CHECK(h != 0xFFFFFFFFu);
    c.r[3] = h; hle_spm3ds_FSSize(&c);
    printf("SIZE %u\n", (unsigned)c.r[3]);
    got = 0;
    c.r[3] = h; c.r[4] = BASE + 0x2000u; c.r[5] = 100; c.r[6] = 5000;
    hle_spm3ds_FSRead(&c);
    got = c.r[3];
    printf("READ %u", (unsigned)got);
    for (i = 0; i < got; i++) printf(" %02x", mem[0x2000u + i]);
    printf("\n");
    c.r[3] = h; c.r[4] = BASE + 0x4000u; c.r[5] = 9900; c.r[6] = 500;
    hle_spm3ds_FSRead(&c);
    printf("TAIL %u\n", (unsigned)c.r[3]);
    c.r[3] = h; hle_spm3ds_FSClose(&c);
    c.r[3] = h; hle_spm3ds_FSSize(&c);
    CHECK(c.r[3] == 0);             /* closed handle */

    CHECK(fs_open("../data.bin") == 0xFFFFFFFFu);
    CHECK(fs_open("/etc/passwd") == 0xFFFFFFFFu);
    CHECK(fs_open("C:data.bin") == 0xFFFFFFFFu);
    CHECK(fs_open("missing.bin") == 0xFFFFFFFFu);

    if (failures) printf("FAILURES %d\n", failures);
    else printf("OK\n");
    return failures ? 1 : 0;
}
"""


@unittest.skipUnless(HAVE_GCC, "gcc is not installed")
class OsLayer(unittest.TestCase):
    def run_driver(self, root):
        with tempfile.TemporaryDirectory() as workdir:
            src = os.path.join(workdir, "driver.c")
            exe = os.path.join(workdir, "driver")
            with open(src, "w", encoding="utf-8") as f:
                f.write(DRIVER)
            subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-I", RUNTIME, "-o", exe,
                            src, os.path.join(RUNTIME, "hle.c"), os.path.join(RUNTIME, "os.c")],
                           check=True, capture_output=True, text=True)
            proc = subprocess.run([exe, root], capture_output=True, text=True)
        return proc

    def test_heap_timers_and_files(self):
        pattern = bytes((i * 7) & 0xFF for i in range(10000))
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "data.bin"), "wb") as f:
                f.write(pattern)
            proc = self.run_driver(root)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(lines[-1], "OK", proc.stdout)

        # Timer ticks against exact integer arithmetic: floor(ns * 243 / 4000), 64-bit.
        ticks = [line.split() for line in lines if line.startswith("TICKS")]
        self.assertEqual(len(ticks), 6)
        for _, ns, hi, lo in ticks:
            expected = int(ns) * 243 // 4000
            self.assertEqual((int(hi) << 32) | int(lo), expected, f"ns={ns}")

        self.assertIn("SIZE 10000", lines)
        read = [line for line in lines if line.startswith("READ")][0].split()
        self.assertEqual(int(read[1]), 5000)
        self.assertEqual(bytes(int(x, 16) for x in read[2:]), pattern[100:5100])
        self.assertIn("TAIL 100", lines)  # short read at end of file


if __name__ == "__main__":
    unittest.main()
