"""runtime/gx_cp.c (C command processor) against spm3ds/gx/cp.py on the same FIFO streams.

The C port feeds the 3DS renderer, so it must produce the triangles the Python reference
produces: same count after culling, and the same NDC position, 1/w, colour and texture
coordinate per vertex (within single-precision tolerance).
"""
import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from spm3ds.gx.cp import CommandProcessor
from tests import gxstream as g
from tests.gxstream import GXStream
from tests.support import RUNTIME
from tests.test_gx_regs import IDENT, Memory, ortho_setup, quad_stream

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "gx_cp.h"

typedef struct { uint32_t base; uint8_t *data; uint32_t size; } block;
static block blocks[8];
static int nblocks;

static int rd(void *user, uint32_t phys, uint8_t *dst, uint32_t n) {
    int i;
    (void)user;
    for (i = 0; i < nblocks; i++) {
        if (phys >= blocks[i].base && phys - blocks[i].base + n <= blocks[i].size) {
            memcpy(dst, blocks[i].data + (phys - blocks[i].base), n);
            return 1;
        }
    }
    return 0;
}

static uint8_t *slurp(const char *path, uint32_t *size) {
    FILE *f = fopen(path, "rb");
    long n;
    uint8_t *p;
    if (!f) exit(2);
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    p = malloc(n ? (size_t)n : 1);
    if (fread(p, 1, (size_t)n, f) != (size_t)n) exit(3);
    fclose(f);
    *size = (uint32_t)n;
    return p;
}

int main(int argc, char **argv) {
    static gx_cp cp;
    uint32_t n, i;
    uint8_t *stream = slurp(argv[1], &n);
    size_t used;
    for (i = 2; i + 1 < (uint32_t)argc; i += 2) {
        blocks[nblocks].base = (uint32_t)strtoul(argv[i], NULL, 0);
        blocks[nblocks].data = slurp(argv[i + 1], &blocks[nblocks].size);
        nblocks++;
    }
    gx_cp_init(&cp, rd, NULL);
    used = gx_cp_run(&cp, stream, n);
    printf("used %lu error %d commands %u\n", (unsigned long)used, cp.error, cp.commands);
    for (i = 0; i < cp.out_count; i++) {
        gx_vertex_out *v = &cp.out[i];
        printf("v %.9g %.9g %.9g %.9g %.9g %.9g %.9g %.9g %.9g %.9g\n",
               v->x, v->y, v->z, v->w, v->r, v->g, v->b, v->a, v->u, v->v);
    }
    gx_cp_free(&cp);
    return 0;
}
"""


def python_triangles(stream, mem=None):
    cp = CommandProcessor(4, 4, read_memory=mem.read if mem else None).run(stream)
    kept = []
    for tri in cp.triangles:
        a, b, c = tri
        area = (b["x"] - a["x"]) * (c["y"] - a["y"]) - (b["y"] - a["y"]) * (c["x"] - a["x"])
        if cp.cull == "all" or (cp.cull == "back" and area > 0) or (cp.cull == "front" and area < 0):
            continue
        kept.append(tri)
    return cp, kept


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class CPortMatchesPython(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="spm3ds-gxcp-")
        src = os.path.join(cls.tmp, "driver.c")
        with open(src, "w") as f:
            f.write(DRIVER)
        cls.exe = os.path.join(cls.tmp, "driver")
        subprocess.run(["gcc", "-std=c99", "-Wall", "-Werror", "-I", RUNTIME, src,
                        os.path.join(RUNTIME, "gx_cp.c"), "-o", cls.exe], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_c(self, stream, mem=None):
        path = os.path.join(self.tmp, "stream.bin")
        with open(path, "wb") as f:
            f.write(stream)
        args = [self.exe, path]
        for k, (base, blob) in enumerate((mem.blocks if mem else {}).items()):
            p = os.path.join(self.tmp, f"mem{k}.bin")
            with open(p, "wb") as f:
                f.write(blob)
            args += [hex(base), p]
        out = subprocess.run(args, check=True, capture_output=True, text=True).stdout.splitlines()
        head = out[0].split()
        verts = [[float(x) for x in line.split()[1:]] for line in out[1:]]
        return int(head[1]), int(head[3]), verts

    def compare(self, stream, mem=None):
        cp, tris = python_triangles(stream, mem)
        used, error, verts = self.run_c(stream, mem)
        self.assertEqual(error, 0)
        self.assertEqual(used, len(stream))
        self.assertEqual(len(verts), 3 * len(tris))
        for t, tri in enumerate(tris):
            for k, pv in enumerate(tri):
                x, y, z, w, r, g_, b, a, u, v = verts[3 * t + k]
                self.assertAlmostEqual(x / w, pv["ndc_xy"][0], places=5)
                self.assertAlmostEqual(y / w, pv["ndc_xy"][1], places=5)
                self.assertAlmostEqual(1.0 / w, pv["invw"], places=5)
                for got, want in zip((r, g_, b, a), pv["color"]):
                    self.assertAlmostEqual(got, want, places=5)
                self.assertAlmostEqual(u, pv["uv"][0], places=5)
                self.assertAlmostEqual(v, pv["uv"][1], places=5)
        return tris

    def test_direct_quad(self):
        s = quad_stream((10, 20, 30, 40), extra=lambda s: s.SetCullMode(g.GX_CULL_NONE))
        self.assertEqual(len(self.compare(s.data())), 2)

    def test_default_back_culling_keeps_front_faces(self):
        self.assertEqual(len(self.compare(quad_stream().data())), 2)

    def test_front_culling_drops_the_quad(self):
        s = quad_stream(extra=lambda s: s.SetCullMode(g.GX_CULL_FRONT))
        self.assertEqual(len(self.compare(s.data())), 0)

    def test_indexed_s16_positions_and_rgb565(self):
        mem = Memory()
        mem.put(0x00100000, b"".join(struct.pack(">3h", *p)
                                     for p in [(0, 0, -8), (64, 0, -8), (0, 64, -8), (64, 64, -8)]))
        mem.put(0x00200000, b"\xF8\x00\x07\xE0\x00\x1F")
        s = GXStream()
        ortho_setup(s)
        s.SetCullMode(g.GX_CULL_NONE)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_INDEX16)
        s.SetVtxDesc(g.GX_VA_CLR0, g.GX_INDEX8)
        s.SetVtxAttrFmt(2, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_S16, 4)
        s.SetVtxAttrFmt(2, g.GX_VA_CLR0, g.GX_CLR_RGB, g.GX_RGB565, 0)
        s.SetArray(g.GX_VA_POS, 0x00100000, 6)
        s.SetArray(g.GX_VA_CLR0, 0x00200000, 2)
        s.Begin(g.GX_TRIANGLESTRIP, 2, 4)
        for i in range(4):
            s.raw("H", i)
            s.raw("B", i % 3)
        self.assertEqual(len(self.compare(s.data(), mem)), 2)

    def test_perspective_and_fan_with_matrix_index(self):
        s = GXStream()
        mt = [[1.2, 0, 0.1, 0], [0, 1.5, -0.2, 0], [0, 0, -1.01, -0.5], [0, 0, -1.0, 0]]
        s.LoadProjectionMtx(mt, g.GX_PERSPECTIVE)
        s.LoadPosMtxImm([[0.8, -0.6, 0, 0.3], [0.6, 0.8, 0, -0.1], [0, 0, 1, -3.0]], 3)
        s.SetCurrentMtx(3)
        s.SetCullMode(g.GX_CULL_NONE)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxDesc(g.GX_VA_TEX0, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.SetVtxAttrFmt(0, g.GX_VA_TEX0, g.GX_TEX_ST, g.GX_S16, 8)
        s.Begin(g.GX_TRIANGLEFAN, 0, 5)
        for x, y, u, v in [(0, 0, 128, 128), (1, 0, 256, 128), (1, 1, 256, 256),
                           (0, 1, 128, 256), (-1, 0.5, 0, 200)]:
            s.raw("3f", x, y, 0.25)
            s.raw("2h", u, v)
        self.assertEqual(len(self.compare(s.data())), 3)

    def test_per_vertex_matrix_and_display_list(self):
        inner = GXStream()
        inner.Begin(g.GX_TRIANGLES, 0, 3)
        for mtx, (x, y) in zip([6, 0, 6], [(0, 0), (2, 0), (0, 2)]):
            inner.raw("B", mtx)
            inner.raw("3f", x, y, -0.5)
        mem = Memory()
        mem.put(0x300000, inner.data())
        s = GXStream()
        ortho_setup(s)
        s.LoadPosMtxImm([[1.0, 0, 0, 1.0], [0, 1.0, 0, 0.5], [0, 0, 1.0, 0]], 6)
        s.SetCullMode(g.GX_CULL_NONE)
        s.SetVtxDesc(g.GX_VA_PTNMTXIDX, g.GX_DIRECT)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.out += b"\x48" + struct.pack(">II", 0x300000, len(inner.data()))
        self.assertEqual(len(self.compare(s.data(), mem)), 1)

    def test_truncated_command_is_left_for_the_next_call(self):
        stream = quad_stream().data()
        used, error, _ = self.run_c(stream[:-5])
        self.assertEqual(error, 0)
        self.assertLess(used, len(stream) - 5)   # the cut-off draw was not consumed

    def test_unknown_command_reports_an_error(self):
        _, error, _ = self.run_c(b"\x00\x00\x55")
        self.assertEqual(error, 1)


if __name__ == "__main__":
    unittest.main()
