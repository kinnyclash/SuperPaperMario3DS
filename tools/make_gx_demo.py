"""Build the translated GX demo: a PowerPC program that draws a rotating cube through GX.

The program is written here as PowerPC words (test encoders in tests/helpers.py). Each call
does what a game's GX code does every frame: it stores GX commands to the write-gather pipe
at 0xCC008000 with stb/sth/stw/stfs. It loads a perspective projection, computes a rotation
matrix with fmuls/fneg from cos/sin values the host puts in f1, f2 (Y axis) and f4, f5
(X axis), sets the vertex format (direct f32 position, RGBA8 colour) and draws 6 quads.

No game code is involved. The cube and its colours are made up for this demo.

Usage:
  python3 tools/make_gx_demo.py [OUTPUT_DIR]

Writes OUTPUT_DIR/gx_demo_guest.c (default: platform/3ds/source/gfx). It defines
    void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx);
which runs one frame of the guest program on c (attach runtime/hw.c first).
"""
import math
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from spm3ds.recomp import generate_c  # noqa: E402
from tests.helpers import (BLR, addi, addis, fmuls, fneg, ori, program_image, stb,  # noqa: E402
                           stfs, sth, stw)

CODE_BASE = 0x80100000
PIPE = 0xCC008000
ASPECT = 400.0 / 240.0      # 3DS top screen
FOVY = 60.0
NEAR, FAR = 0.1, 100.0
DISTANCE = -2.5             # cube centre on the view axis

# Cube faces: four corners each (counter-clockwise seen from outside) and a colour.
FACES = [
    ([(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)], (230, 60, 60, 255)),       # +z
    ([(1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)], (60, 200, 80, 255)),   # -z
    ([(1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)], (60, 110, 230, 255)),      # +x
    ([(-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)], (240, 200, 50, 255)),  # -x
    ([(-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)], (200, 80, 220, 255)),      # +y
    ([(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)], (60, 210, 210, 255)),  # -y
]
HALF = 0.5


def f32_bits(x):
    return struct.unpack(">I", struct.pack(">f", x))[0]


class Emitter:
    """Emits guest code that stores values to the pipe; r3 holds the pipe address."""

    def __init__(self):
        self.words = [addis(3, 0, PIPE >> 16), ori(3, 3, PIPE & 0xFFFF)]

    def li32(self, reg, value):
        value &= 0xFFFFFFFF
        self.words += [addis(reg, 0, value >> 16), ori(reg, reg, value & 0xFFFF)]

    def u8(self, v):
        self.words += [addi(4, 0, v & 0xFF), stb(4, 3, 0)]

    def u16(self, v):
        self.li32(4, v & 0xFFFF)
        self.words.append(sth(4, 3, 0))

    def u32(self, v):
        self.li32(4, v)
        self.words.append(stw(4, 3, 0))

    def f32(self, x):
        self.u32(f32_bits(x))

    def freg(self, n):
        self.words.append(stfs(n, 3, 0))

    # GX commands (libogc GX_LOAD_* macros)
    def cp(self, reg, value):
        self.u8(0x08)
        self.u8(reg)
        self.u32(value)

    def bp(self, word):
        self.u8(0x61)
        self.u32(word)

    def xf_header(self, base, count):
        self.u8(0x10)
        self.u32(((count - 1) << 16) | base)


def projection():
    """GX perspective parameters (libogc GX_LoadProjectionMtx layout, guPerspective values)."""
    cot = 1.0 / math.tan(math.radians(FOVY) / 2.0)
    return [cot / ASPECT, 0.0, cot, 0.0, -NEAR / (FAR - NEAR), -(FAR * NEAR) / (FAR - NEAR)]


def program():
    e = Emitter()
    # Rotation M = Rx * Ry. With cy, sy in f1, f2 and cx, sx in f4, f5:
    #   row0 = ( cy,      0,   sy,     0)
    #   row1 = ( sx*sy,   cx, -sx*cy,  0)
    #   row2 = (-cx*sy,   sx,  cx*cy,  DISTANCE)
    e.words += [fmuls(6, 5, 2),              # f6 = sx*sy
                fmuls(7, 5, 1), fneg(7, 7),  # f7 = -sx*cy
                fmuls(8, 4, 2), fneg(8, 8),  # f8 = -cx*sy
                fmuls(9, 4, 1)]              # f9 = cx*cy
    # Projection: XF 0x1020, six floats and the type word (0 = perspective).
    e.xf_header(0x1020, 7)
    for x in projection():
        e.f32(x)
    e.u32(0)
    # Position matrix 0: XF 0x0000, 12 floats, row-major.
    e.xf_header(0x0000, 12)
    e.freg(1); e.u32(0); e.freg(2); e.u32(0)                     # noqa: E702
    e.freg(6); e.freg(4); e.freg(7); e.u32(0)                    # noqa: E702
    e.freg(8); e.freg(5); e.freg(9); e.f32(DISTANCE)             # noqa: E702
    # Current matrix 0 (CP 0x30 and XF 0x1018), no culling (BP 0x00, gen mode 0).
    e.cp(0x30, 0)
    e.xf_header(0x1018, 1)
    e.u32(0)
    e.bp(0x00000000)
    # Vertex descriptor: POS direct (bits 9..10), CLR0 direct (bits 13..14).
    e.cp(0x50, (1 << 9) | (1 << 13))
    e.cp(0x60, 0)
    # VAT 0: POS XYZ F32 (bit 0, size bits 1..3 = 4), CLR0 RGBA RGBA8 (bit 13, bits 14..16 = 5).
    e.cp(0x70, 1 | (4 << 1) | (1 << 13) | (5 << 14))
    e.cp(0x80, 0)
    e.cp(0x90, 0)
    # Draw: GX_QUADS (0x80) with VAT 0, 24 vertices.
    e.u8(0x80)
    e.u16(4 * len(FACES))
    for corners, rgba in FACES:
        for x, y, z in corners:
            e.f32(x * HALF)
            e.f32(y * HALF)
            e.f32(z * HALF)
            e.u32((rgba[0] << 24) | (rgba[1] << 16) | (rgba[2] << 8) | rgba[3])
    e.words.append(BLR)
    return e.words


def build_c():
    words = program()
    source = generate_c(program_image(words, CODE_BASE), CODE_BASE)
    wrapper = [
        "",
        "/* Entry point for one frame. Generated by tools/make_gx_demo.py. */",
        "static uint64_t demo_bits(float x) { return ppc_b((double)x); }",
        "",
        "void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx);",
        "void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx) {",
        "    c->f[1] = demo_bits(cy);",
        "    c->f[2] = demo_bits(sy);",
        "    c->f[4] = demo_bits(cx);",
        "    c->f[5] = demo_bits(sx);",
        f"    fn_{CODE_BASE:08X}(c);",
        "}",
        "",
    ]
    return source + "\n".join(wrapper)


def main(argv):
    out_dir = argv[1] if len(argv) > 1 else os.path.join(ROOT, "platform", "3ds", "source", "gfx")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "gx_demo_guest.c")
    with open(path, "w", encoding="utf-8") as f:
        f.write(build_c())
    print(f"wrote {path} ({len(program())} guest instructions)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
