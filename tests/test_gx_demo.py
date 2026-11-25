"""The translated GX demo end to end: PowerPC guest -> FIFO -> command processor.

Two paths must agree for several rotation angles:
  * interpreter (spm3ds/ppc.py) -> spm3ds/hw.py FIFO -> spm3ds/gx/cp.py
  * generated C (platform/3ds/source/gfx/gx_demo_guest.c) -> runtime/hw.c -> runtime/gx_cp.c
The second path is exactly what the 3DS graphics app runs each frame before citro3d draws.
"""
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from spm3ds.gx.cp import CommandProcessor
from spm3ds.hw import Hardware
from spm3ds.memory import MemoryMap
from spm3ds.ppc import Machine
from tests.helpers import program_image
from tests.support import RUNTIME

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import make_gx_demo  # noqa: E402

GUEST_C = os.path.join(ROOT, "platform", "3ds", "source", "gfx", "gx_demo_guest.c")

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hw.h"
#include "gx_cp.h"
void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx);
static float f_of(const char *hex) {
    uint32_t w = (uint32_t)strtoul(hex, NULL, 16);
    float f;
    memcpy(&f, &w, 4);
    return f;
}
int main(int argc, char **argv) {
    static ppc_ctx c;
    static spm3ds_hw hw;
    static gx_cp cp;
    size_t used, i;
    (void)argc;
    memset(&c, 0, sizeof c);
    spm3ds_hw_init(&hw);
    spm3ds_hw_attach(&c, &hw);
    spm3ds_gx_demo_frame(&c, f_of(argv[1]), f_of(argv[2]), f_of(argv[3]), f_of(argv[4]));
    printf("fifo");
    for (i = 0; i < hw.fifo_size; i++) printf(" %02x", hw.fifo[i]);
    printf("\n");
    gx_cp_init(&cp, NULL, NULL);
    used = gx_cp_run(&cp, hw.fifo, hw.fifo_size);
    printf("used %lu error %d unsupported %u\n", (unsigned long)used, cp.error, cp.unsupported);
    for (i = 0; i < cp.out_count; i++) {
        gx_vertex_out *v = &cp.out[i];
        printf("v %.9g %.9g %.9g %.9g %.9g %.9g %.9g %.9g\n",
               v->x, v->y, v->z, v->w, v->r, v->g, v->b, v->a);
    }
    return 0;
}
"""

ANGLES = [(0.0, 0.0), (0.6, 0.3), (2.1, -1.2), (3.9, 2.4)]


def f32(x):
    return struct.unpack(">f", struct.pack(">f", x))[0]


def f32_hex(x):
    return "%08x" % struct.unpack(">I", struct.pack(">f", x))[0]


def interpret_frame(cy, sy, cx, sx):
    memory = MemoryMap()
    hw = Hardware()
    memory.mmio = hw
    m = Machine(program_image(make_gx_demo.program(), make_gx_demo.CODE_BASE), memory)
    for reg, value in ((1, cy), (2, sy), (4, cx), (5, sx)):
        m.f[reg] = struct.unpack(">Q", struct.pack(">d", value))[0]
    m.run(make_gx_demo.CODE_BASE)
    return bytes(hw.fifo)


class GeneratedSource(unittest.TestCase):
    def test_checked_in_source_matches_generator(self):
        with open(GUEST_C, encoding="utf-8") as f:
            self.assertEqual(f.read(), make_gx_demo.build_c())


class InterpreterPath(unittest.TestCase):
    def test_draws_twelve_visible_triangles(self):
        fifo = interpret_frame(1.0, 0.0, 1.0, 0.0)
        cp = CommandProcessor(400, 240).run(fifo)
        self.assertEqual(len(cp.triangles), 12)
        self.assertEqual(dict(cp.unsupported), {})
        self.assertEqual(cp.cull, "none")
        for tri in cp.triangles:
            for v in tri:
                self.assertGreater(v["invw"], 0.0)          # in front of the camera
                self.assertTrue(-1.0 <= v["ndc_xy"][0] <= 1.0)
                self.assertTrue(-1.0 <= v["ndc_xy"][1] <= 1.0)


@unittest.skipUnless(shutil.which("gcc"), "gcc not installed")
class NativePathMatches(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="spm3ds-gxdemo-")
        drv = os.path.join(cls.tmp, "driver.c")
        with open(drv, "w") as f:
            f.write(DRIVER)
        cls.exe = os.path.join(cls.tmp, "demo")
        subprocess.run(["gcc", "-std=c99", "-O1", "-Wall", "-Wno-unused-label", "-Werror",
                        "-I", RUNTIME, drv, GUEST_C, os.path.join(RUNTIME, "hw.c"),
                        os.path.join(RUNTIME, "gx_cp.c"), "-o", cls.exe, "-lm"], check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fifo_and_triangles_match_the_interpreter(self):
        import math
        for ay, ax in ANGLES:
            with self.subTest(ay=ay, ax=ax):
                vals = [f32(math.cos(ay)), f32(math.sin(ay)), f32(math.cos(ax)), f32(math.sin(ax))]
                out = subprocess.run([self.exe] + [f32_hex(v) for v in vals], check=True,
                                     capture_output=True, text=True).stdout.splitlines()
                c_fifo = bytes(int(x, 16) for x in out[0].split()[1:])
                py_fifo = interpret_frame(*vals)
                self.assertEqual(c_fifo, py_fifo)
                head = out[1].split()
                self.assertEqual((int(head[1]), int(head[3]), int(head[5])),
                                 (len(c_fifo), 0, 0))
                verts = [[float(x) for x in line.split()[1:]] for line in out[2:]]
                tris = CommandProcessor(400, 240).run(py_fifo).triangles
                self.assertEqual(len(verts), 3 * len(tris))
                for t, tri in enumerate(tris):
                    for k, pv in enumerate(tri):
                        x, y, z, w = verts[3 * t + k][:4]
                        self.assertAlmostEqual(x / w, pv["ndc_xy"][0], places=5)
                        self.assertAlmostEqual(y / w, pv["ndc_xy"][1], places=5)
                        self.assertAlmostEqual(1.0 / w, pv["invw"], places=5)
                        self.assertEqual(tuple(round(c * 255) for c in verts[3 * t + k][4:]),
                                         tuple(round(c * 255) for c in pv["color"]))


if __name__ == "__main__":
    unittest.main()
