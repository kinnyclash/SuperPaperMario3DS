"""Paged guest memory: pages allocated on first touch, and unaligned accesses across page
boundaries. A randomized C driver compares paged reads and writes with a flat shadow array,
which is kept separately and is not part of the code under test."""
import os
import subprocess
import tempfile
import unittest

from tests.support import HAVE_GCC, RUNTIME

DRIVER = r"""
#include <stdio.h>
#include <string.h>
#include "ppc_ctx.h"

#define BASE 0x90000000u
#define SIZE (4u << 20)         /* 4 MiB paged region for the randomized part */
static uint8_t shadow[SIZE];
static int failures = 0;

#define CHECK(cond) do { if (!(cond)) { printf("FAIL line %d: %s\n", __LINE__, #cond); failures++; } } while (0)

static uint32_t lcg_state = 12345u;
static uint32_t rnd(void) { lcg_state = lcg_state * 1103515245u + 12345u; return lcg_state >> 8; }

int main(void) {
    ppc_ctx c;
    uint32_t i, ops;
    memset(&c, 0, sizeof c);
    CHECK(ppc_map_paged(&c, BASE, SIZE) == 0);
    CHECK(ppc_map_paged(&c, BASE + 0x1000u, 0x10000u) == -1);   /* overlap refused */
    CHECK(ppc_map_paged(&c, 0x80000000u, 1000u) == -1);         /* not a page multiple */
    CHECK(ppc_committed_bytes(&c) == 0);

    /* a 32-bit write that crosses a page boundary */
    ppc_w32(&c, BASE + 0xFFFEu, 0x11223344u);
    CHECK(ppc_r8(&c, BASE + 0xFFFEu) == 0x11u);
    CHECK(ppc_r8(&c, BASE + 0xFFFFu) == 0x22u);
    CHECK(ppc_r8(&c, BASE + 0x10000u) == 0x33u);
    CHECK(ppc_r8(&c, BASE + 0x10001u) == 0x44u);
    CHECK(ppc_r32(&c, BASE + 0xFFFEu) == 0x11223344u);
    CHECK(ppc_committed_bytes(&c) == 2u * PPC_PAGE_SIZE);

    /* outside every region there is no pointer */
    CHECK(ppc_try(&c, BASE + SIZE, 1) == NULL);
    CHECK(ppc_try(&c, BASE + SIZE - 2u, 4) == NULL);

    /* randomized accesses checked against the shadow array */
    memset(shadow, 0, sizeof shadow);
    for (ops = 0; ops < 200000u; ops++) {
        uint32_t off = rnd() % (SIZE - 8u);
        uint32_t kind = rnd() % 4u;
        uint32_t value = rnd();
        uint32_t ea = BASE + off;
        if (rnd() % 2u) {  /* writes */
            if (kind == 0) { ppc_w8(&c, ea, value); shadow[off] = (uint8_t)value; }
            else if (kind == 1) {
                ppc_w16(&c, ea, value);
                shadow[off] = (uint8_t)(value >> 8); shadow[off + 1] = (uint8_t)value;
            } else if (kind == 2) {
                ppc_w32(&c, ea, value);
                for (i = 0; i < 4; i++) shadow[off + i] = (uint8_t)(value >> (24 - 8 * i));
            } else {
                uint64_t v = ((uint64_t)value << 32) | rnd();
                ppc_w64(&c, ea, v);
                for (i = 0; i < 8; i++) shadow[off + i] = (uint8_t)(v >> (56 - 8 * i));
            }
        } else {           /* reads */
            if (kind == 0) CHECK(ppc_r8(&c, ea) == shadow[off]);
            else if (kind == 1) CHECK(ppc_r16(&c, ea) == (((uint32_t)shadow[off] << 8) | shadow[off + 1]));
            else if (kind == 2) {
                uint32_t want = ((uint32_t)shadow[off] << 24) | ((uint32_t)shadow[off + 1] << 16) |
                                ((uint32_t)shadow[off + 2] << 8) | shadow[off + 3];
                CHECK(ppc_r32(&c, ea) == want);
            } else {
                uint64_t want = 0;
                for (i = 0; i < 8; i++) want = (want << 8) | shadow[off + i];
                CHECK(ppc_r64(&c, ea) == want);
            }
        }
    }

    /* the randomized part touched most pages of the 4 MiB region; committed memory is
     * bounded by the region size, and freeing returns everything */
    CHECK(ppc_committed_bytes(&c) <= SIZE);
    CHECK(ppc_committed_bytes(&c) > 0);
    printf("COMMITTED %zu\n", ppc_committed_bytes(&c));
    ppc_unmap_paged(&c);
    CHECK(ppc_committed_bytes(&c) == 0);

    printf(failures ? "FAILURES %d\n" : "OK\n", failures);
    return failures ? 1 : 0;
}
"""


@unittest.skipUnless(HAVE_GCC, "gcc is not installed")
class PagedMemory(unittest.TestCase):
    def test_paged_matches_flat_reference_and_frees_cleanly(self):
        with tempfile.TemporaryDirectory() as workdir:
            src = os.path.join(workdir, "driver.c")
            exe = os.path.join(workdir, "driver")
            with open(src, "w", encoding="utf-8") as f:
                f.write(DRIVER)
            subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-I", RUNTIME, "-o", exe,
                            src, os.path.join(RUNTIME, "mem.c")],
                           check=True, capture_output=True, text=True)
            proc = subprocess.run([exe], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(proc.stdout.splitlines()[-1], "OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
