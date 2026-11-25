"""GX register decoding, indexed vertices, XF transform registers and BP render state.

Streams are built with tests/gxstream.py, which packs registers as libogc does. Expected
values are worked out by hand in the comments.
"""
import struct
import unittest

from spm3ds.gx import regs
from spm3ds.gx.cp import CommandProcessor
from spm3ds.gx.raster import Framebuffer, Texture
from tests import gxstream as g
from tests.gxstream import GXStream

IDENT = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]


class Memory:
    """Physical-address memory for array reads."""

    def __init__(self):
        self.blocks = {}

    def put(self, addr, data):
        self.blocks[addr] = bytes(data)

    def read(self, addr, n):
        for base, blob in self.blocks.items():
            if base <= addr and addr + n <= base + len(blob):
                return blob[addr - base:addr - base + n]
        raise AssertionError(f"read outside test memory: {addr:#x}+{n}")


def ortho_setup(s, w=4, h=4):
    """Orthographic projection mapping x, y in [0, w] x [0, h] to the frame, z in [-1, 0]."""
    # GX ortho (libogc guOrtho): p0 = 2/(r-l), p1 = -(r+l)/(r-l), p2 = 2/(t-b),
    # p3 = -(t+b)/(t-b), p4 = -1/(f-n), p5 = -f/(f-n). With l=0, r=w, b=0, t=h, n=0, f=1.
    mt = [[2.0 / w, 0, 0, -1.0], [0, 2.0 / h, 0, -1.0], [0, 0, -1.0, -1.0], [0, 0, 0, 1]]
    s.LoadProjectionMtx(mt, g.GX_ORTHOGRAPHIC)
    s.LoadPosMtxImm(IDENT, 0)
    s.SetCurrentMtx(0)


class FieldDecoding(unittest.TestCase):
    def test_vcd(self):
        s = GXStream()
        s.SetVtxDesc(g.GX_VA_POS, g.GX_INDEX16)
        s.SetVtxDesc(g.GX_VA_CLR0, g.GX_DIRECT)
        s.SetVtxDesc(g.GX_VA_TEX0 + 2, g.GX_INDEX8)
        vcd = regs.decode_vcd(s.vcd_lo, s.vcd_hi)
        self.assertEqual((vcd["pos"], vcd["clr0"], vcd["tex2"], vcd["tex0"], vcd["nrm"]),
                         (regs.INDEX16, regs.DIRECT, regs.INDEX8, regs.NONE, regs.NONE))

    def test_vat(self):
        s = GXStream()
        s.SetVtxAttrFmt(3, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_S16, 7)
        s.SetVtxAttrFmt(3, g.GX_VA_CLR0, g.GX_CLR_RGBA, g.GX_RGBA4, 0)
        s.SetVtxAttrFmt(3, g.GX_VA_TEX0, g.GX_TEX_ST, g.GX_U8, 4)
        vat = regs.decode_vat(*s.vat[3])
        self.assertEqual((vat.attrs["pos"].comptype, vat.attrs["pos"].compsize,
                          vat.attrs["pos"].frac), (1, g.GX_S16, 7))
        self.assertEqual((vat.attrs["clr0"].comptype, vat.attrs["clr0"].compsize), (1, g.GX_RGBA4))
        self.assertEqual((vat.attrs["tex0"].comptype, vat.attrs["tex0"].compsize,
                          vat.attrs["tex0"].frac), (1, g.GX_U8, 4))
        self.assertTrue(vat.byte_dequant)

    def test_genmode_cull_mapping(self):
        # libogc cm2hw: CULL_FRONT -> hardware 2, CULL_BACK -> hardware 1.
        for mode, name in [(g.GX_CULL_NONE, "none"), (g.GX_CULL_FRONT, "front"),
                           (g.GX_CULL_BACK, "back"), (g.GX_CULL_ALL, "all")]:
            s = GXStream()
            s.SetCullMode(mode)
            s.SetNumTevStages(3)
            gm = regs.decode_genmode(s.gen_mode)
            self.assertEqual((gm.cull, gm.num_tev_stages), (name, 3))

    def test_tev_env_and_order(self):
        s = GXStream()
        s.SetTevColorIn(1, "TEXC", "RASC", "A0", "ZERO")
        s.SetTevColorOp(1, g.GX_TEV_SUB, 1, 2, 1, g.GX_TEVREG1)
        s.SetTevAlphaIn(1, "TEXA", "RASA", "A2", "ZERO")
        s.SetTevOrder(1, 2, 5, g.GX_COLOR0A0)
        c = regs.decode_tev_color_env(s.tev_color[1])
        self.assertEqual((c.a, c.b, c.c, c.d), (8, 10, 3, 15))
        self.assertEqual((c.subtract, c.bias, c.scale, c.clamp, c.dest), (True, 1, 2, True, 2))
        a = regs.decode_tev_alpha_env(s.tev_alpha[1])
        self.assertEqual((a.a, a.b, a.c, a.d), (4, 5, 3, 7))
        o = regs.decode_tev_order(s.tev_order[0], 1)
        self.assertEqual((o.texmap, o.texcoord, o.tex_enable, o.color), (5, 2, True, 0))

    def test_tev_register_colour(self):
        lo = (0xE2 << 24) | (0x40 << 12) | 0x11
        hi = (0xE3 << 24) | (0x22 << 12) | 0x33
        self.assertEqual(regs.decode_tev_reg_pair(lo & 0xFFFFFF, hi & 0xFFFFFF),
                         (0x11, 0x22, 0x33, 0x40))

    def test_colour_formats(self):
        # RGB565 0xF81F: red 31 -> 255, green 0, blue 31 -> 255.
        self.assertEqual(regs.decode_color(b"\xF8\x1F", 0), (255, 0, 255, 255))
        # RGBA4 0x1234: 1*17, 2*17, 3*17, 4*17.
        self.assertEqual(regs.decode_color(b"\x12\x34", 3), (17, 34, 51, 68))
        # RGBA6: 63, 0, 32, 1 -> 255, 0, 130, 4 (x6(v) = v << 2 | v >> 4).
        v = (63 << 18) | (0 << 12) | (32 << 6) | 1
        self.assertEqual(regs.decode_color(v.to_bytes(3, "big"), 4), (255, 0, 130, 4))


class HardwareVertices(unittest.TestCase):
    def test_direct_f32_positions_and_colour(self):
        s = GXStream()
        ortho_setup(s)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxDesc(g.GX_VA_CLR0, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.SetVtxAttrFmt(0, g.GX_VA_CLR0, g.GX_CLR_RGBA, g.GX_RGBA8, 0)
        s.Begin(g.GX_TRIANGLES, 0, 3)
        for x, y in [(0, 0), (4, 0), (0, 4)]:
            s.raw("3f", x, y, -0.5)
            s.raw("4B", 255, 0, 0, 255)
        cp = CommandProcessor(4, 4).run(s.data())
        self.assertEqual(len(cp.triangles), 1)
        xs = sorted((round(v["x"], 6), round(v["y"], 6)) for v in cp.triangles[0])
        # x = 0..4 maps to pixels 0..4; y up is screen y down: y=0 -> 4, y=4 -> 0.
        self.assertEqual(xs, [(0.0, 0.0), (0.0, 4.0), (4.0, 4.0)])
        self.assertEqual(cp.triangles[0][0]["color"], (1.0, 0.0, 0.0, 1.0))

    def test_indexed_positions_and_colours(self):
        mem = Memory()
        # Position array: s16 xyz with frac 4 (value / 16), stride 6.
        positions = [(0, 0, -8), (64, 0, -8), (0, 64, -8)]
        mem.put(0x00100000, b"".join(struct.pack(">3h", *p) for p in positions))
        # Colour array: RGB565, stride 2. Index 1 is pure green (0x07E0).
        mem.put(0x00200000, b"\xF8\x00\x07\xE0")
        s = GXStream()
        ortho_setup(s)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_INDEX8)
        s.SetVtxDesc(g.GX_VA_CLR0, g.GX_INDEX16)
        s.SetVtxAttrFmt(1, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_S16, 4)
        s.SetVtxAttrFmt(1, g.GX_VA_CLR0, g.GX_CLR_RGB, g.GX_RGB565, 0)
        s.SetArray(g.GX_VA_POS, 0x00100000, 6)
        s.SetArray(g.GX_VA_CLR0, 0x00200000, 2)
        s.Begin(g.GX_TRIANGLES, 1, 3)
        for i in range(3):
            s.raw("B", i)
            s.raw("H", 1)
        cp = CommandProcessor(4, 4, read_memory=mem.read).run(s.data())
        tri = cp.triangles[0]
        # 64 / 16 = 4 units: (4, 0) -> pixel x 4, y 4.
        self.assertAlmostEqual(tri[1]["x"], 4.0)
        self.assertAlmostEqual(tri[1]["y"], 4.0)
        self.assertEqual(tri[0]["color"], (0.0, 1.0, 0.0, 1.0))

    def test_texcoord_u8_with_frac(self):
        s = GXStream()
        ortho_setup(s)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxDesc(g.GX_VA_TEX0, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XY, g.GX_F32, 0)
        s.SetVtxAttrFmt(0, g.GX_VA_TEX0, g.GX_TEX_ST, g.GX_U8, 2)
        s.Begin(g.GX_TRIANGLES, 0, 3)
        for x, y, u, v in [(0, 0, 0, 0), (4, 0, 4, 0), (0, 4, 0, 2)]:
            s.raw("2f", x, y)
            s.raw("2B", u, v)
        tri = CommandProcessor(4, 4).run(s.data()).triangles[0]
        self.assertEqual(tri[1]["uv"], (1.0, 0.0))   # 4 / 2^2
        self.assertEqual(tri[2]["uv"], (0.0, 0.5))   # 2 / 2^2

    def test_matrix_index_selects_matrix(self):
        shift = [[1.0, 0.0, 0.0, 2.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        s = GXStream()
        ortho_setup(s)
        s.LoadPosMtxImm(shift, 3)   # GX_PNMTX1 = 3
        s.SetCurrentMtx(3)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.Begin(g.GX_TRIANGLES, 0, 3)
        for x, y in [(0, 0), (1, 0), (0, 1)]:
            s.raw("3f", x, y, -0.5)
        tri = CommandProcessor(4, 4).run(s.data()).triangles[0]
        self.assertAlmostEqual(tri[0]["x"], 2.0)

    def test_per_vertex_matrix_index(self):
        shift = [[1.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        s = GXStream()
        ortho_setup(s)
        s.LoadPosMtxImm(shift, 6)
        s.SetVtxDesc(g.GX_VA_PTNMTXIDX, g.GX_DIRECT)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.Begin(g.GX_TRIANGLES, 0, 3)
        for mtx, (x, y) in zip([6, 0, 0], [(0, 0), (1, 0), (0, 1)]):
            s.raw("B", mtx)
            s.raw("3f", x, y, -0.5)
        tri = CommandProcessor(4, 4).run(s.data()).triangles[0]
        self.assertAlmostEqual(tri[0]["x"], 1.0)   # shifted by matrix 6
        self.assertAlmostEqual(tri[1]["x"], 1.0)   # unshifted x = 1

    def test_perspective_projection_register(self):
        # guPerspective-style matrix: p0 = p2 = 1, p4 = -1, p5 = -1 (near 0.5, far 1 style).
        mt = [[1.0, 0, 0, 0], [0, 1.0, 0, 0], [0, 0, -1.0, -1.0], [0, 0, -1.0, 0]]
        s = GXStream()
        s.LoadProjectionMtx(mt, g.GX_PERSPECTIVE)
        s.LoadPosMtxImm(IDENT, 0)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.Begin(g.GX_TRIANGLES, 0, 3)
        for x, y in [(1, 0), (0, 1), (0, 0)]:
            s.raw("3f", x, y, -2.0)
        tri = CommandProcessor(8, 8).run(s.data()).triangles[0]
        # clip = (1, 0, 2 - 1, 2): ndc x = 0.5 -> pixel (0.5 * 0.5 + 0.5) * 8 = 6.
        self.assertAlmostEqual(tri[0]["x"], 6.0)
        self.assertAlmostEqual(tri[0]["invw"], 0.5)

    def test_viewport_register(self):
        s = GXStream()
        ortho_setup(s, 2, 2)
        s.SetViewport(10.0, 20.0, 100.0, 50.0, 0.0, 1.0)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.Begin(g.GX_TRIANGLES, 0, 3)
        for x, y in [(0, 0), (2, 2), (1, 1)]:
            s.raw("3f", x, y, 0.0)
        tri = CommandProcessor(200, 100).run(s.data()).triangles[0]
        # ndc (-1, -1) -> (10, 70); ndc (1, 1) -> (110, 20).
        self.assertAlmostEqual(tri[0]["x"], 10.0, places=4)
        self.assertAlmostEqual(tri[0]["y"], 70.0, places=4)
        self.assertAlmostEqual(tri[1]["x"], 110.0, places=4)
        self.assertAlmostEqual(tri[1]["y"], 20.0, places=4)

    def test_display_list_call(self):
        inner = GXStream()
        inner.Begin(g.GX_TRIANGLES, 0, 3)
        for x, y in [(0, 0), (1, 0), (0, 1)]:
            inner.raw("3f", x, y, -0.5)
        mem = Memory()
        mem.put(0x300000, inner.data())
        s = GXStream()
        ortho_setup(s)
        s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
        s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
        s.out += b"\x48" + struct.pack(">II", 0x300000, len(inner.data()))
        cp = CommandProcessor(4, 4, read_memory=mem.read).run(s.data())
        self.assertEqual(len(cp.triangles), 1)


def quad_stream(color=(255, 255, 255, 255), z=-0.5, extra=None):
    s = GXStream()
    ortho_setup(s)
    s.SetVtxDesc(g.GX_VA_POS, g.GX_DIRECT)
    s.SetVtxDesc(g.GX_VA_CLR0, g.GX_DIRECT)
    s.SetVtxDesc(g.GX_VA_TEX0, g.GX_DIRECT)
    s.SetVtxAttrFmt(0, g.GX_VA_POS, g.GX_POS_XYZ, g.GX_F32, 0)
    s.SetVtxAttrFmt(0, g.GX_VA_CLR0, g.GX_CLR_RGBA, g.GX_RGBA8, 0)
    s.SetVtxAttrFmt(0, g.GX_VA_TEX0, g.GX_TEX_ST, g.GX_F32, 0)
    if extra:
        extra(s)
    s.Begin(g.GX_QUADS, 0, 4)
    # Counter-clockwise in NDC (front facing).
    for x, y, u, v in [(0, 0, 0, 1), (4, 0, 1, 1), (4, 4, 1, 0), (0, 4, 0, 0)]:
        s.raw("3f", x, y, z)
        s.raw("4B", *color)
        s.raw("2f", u, v)
    return s


class RenderState(unittest.TestCase):
    def render(self, s, texture=None, fb=None):
        cp = CommandProcessor(4, 4).run(s.data())
        fb = fb or Framebuffer(4, 4)
        written = cp.render(fb, texture=texture)
        return cp, fb, written

    def test_cull_front_from_bp(self):
        s = quad_stream(extra=lambda s: s.SetCullMode(g.GX_CULL_FRONT))
        cp, _, written = self.render(s)
        self.assertEqual(cp.cull, "front")
        self.assertEqual(written, 0)   # the quad is front facing

    def test_bp_mask_register(self):
        s = GXStream()
        s.bp(0x00004000)              # cull back (hardware 1)
        s.bp(0xFE000003)              # mask: only bits 0..1 of the next write
        s.bp(0x0000C003)              # would set cull all, but bits 14..15 are masked
        cp = CommandProcessor(4, 4).run(s.data())
        self.assertEqual(cp.bp[0x00], 0x4003)
        self.assertEqual(cp.cull, "back")

    def test_zmode_from_bp(self):
        def no_update(s):
            s.SetCullMode(g.GX_CULL_NONE)
            s.SetZMode(1, g.GX_LEQUAL, 0)
        s = quad_stream(extra=no_update)
        cp, fb, written = self.render(s)
        # 16 pixels; the 4 on the shared diagonal are drawn by both triangles, because the
        # depth buffer is not updated (update = 0) and LEQUAL passes again.
        self.assertEqual(written, 20)
        self.assertEqual(fb.depth[0][0], Framebuffer(4, 4).depth[0][0])  # no depth writes

    def test_additive_blend_from_bp(self):
        def add(s):
            s.SetCullMode(g.GX_CULL_NONE)
            s.SetBlendMode(g.GX_BM_BLEND, g.GX_BL_ONE, g.GX_BL_ONE)
        fb = Framebuffer(4, 4, clear=(0.25, 0.25, 0.25, 1.0))
        _, fb, _ = self.render(quad_stream((128, 0, 0, 255), extra=add), fb=fb)
        r, g_, b, _ = fb.pixel(1, 1)
        self.assertAlmostEqual(r, 0.25 + 128 / 255.0)
        self.assertAlmostEqual(g_, 0.25)

    def test_tev_constant_register(self):
        # One stage: colour = C0 (a = b = c = ZERO, d = C0), alpha = A0.
        def tev(s):
            s.SetCullMode(g.GX_CULL_NONE)
            s.SetNumTevStages(1)
            s.SetTevColor(g.GX_TEVREG0, (51, 102, 153, 255))
            s.SetTevOrder(0, 0, 0xFF, g.GX_COLORNULL)
            s.SetTevColorIn(0, "ZERO", "ZERO", "ZERO", "C0")
            s.SetTevAlphaIn(0, "ZERO", "ZERO", "ZERO", "A0")
            s.SetTevColorOp(0, g.GX_TEV_ADD, 0, 0, 1, g.GX_TEVPREV)
            s.SetTevAlphaOp(0, g.GX_TEV_ADD, 0, 0, 1, g.GX_TEVPREV)
        _, fb, _ = self.render(quad_stream(extra=tev))
        self.assertEqual(tuple(round(v * 255) for v in fb.pixel(2, 2)), (51, 102, 153, 255))

    def test_tev_two_stages_modulate(self):
        # Stage 0: PREV = RASC (pass vertex colour). Stage 1: PREV = TEXC * CPREV
        # (a = ZERO, b = TEXC, c = CPREV, d = ZERO gives lerp(0, tex, prev) = tex * prev).
        def tev(s):
            s.SetCullMode(g.GX_CULL_NONE)
            s.SetNumTevStages(2)
            s.SetTevOrder(0, 0, 0xFF, g.GX_COLOR0A0)
            s.SetTevColorIn(0, "ZERO", "ZERO", "ZERO", "RASC")
            s.SetTevAlphaIn(0, "ZERO", "ZERO", "ZERO", "RASA")
            s.SetTevColorOp(0, g.GX_TEV_ADD, 0, 0, 1, g.GX_TEVPREV)
            s.SetTevAlphaOp(0, g.GX_TEV_ADD, 0, 0, 1, g.GX_TEVPREV)
            s.SetTevOrder(1, 0, 0, g.GX_COLOR0A0)
            s.SetTevColorIn(1, "ZERO", "TEXC", "CPREV", "ZERO")
            s.SetTevAlphaIn(1, "ZERO", "ZERO", "ZERO", "APREV")
            s.SetTevColorOp(1, g.GX_TEV_ADD, 0, 0, 1, g.GX_TEVPREV)
            s.SetTevAlphaOp(1, g.GX_TEV_ADD, 0, 0, 1, g.GX_TEVPREV)
        tex = Texture([[(128, 255, 0, 255)] * 2] * 2)   # Texture takes 0..255 texels
        _, fb, _ = self.render(quad_stream((255, 128, 255, 255), extra=tev), texture=tex)
        r, g_, b, a = fb.pixel(1, 1)
        self.assertAlmostEqual(r, 128 / 255.0)
        self.assertAlmostEqual(g_, 128 / 255.0)
        self.assertAlmostEqual(b, 0.0)
        self.assertAlmostEqual(a, 1.0)

    def test_tev_scale_and_bias(self):
        # colour = (RASC + 0.5) * 2, clamped: ras 0.2 -> 1.0 clamp; ras 0 -> 1.0; use a
        # subtract stage instead: (C0 - RASC) with C0 = 1.0 -> 1 - ras.
        def tev(s):
            s.SetCullMode(g.GX_CULL_NONE)
            s.SetNumTevStages(1)
            s.SetTevColor(g.GX_TEVREG0, (255, 255, 255, 255))
            s.SetTevOrder(0, 0, 0xFF, g.GX_COLOR0A0)
            s.SetTevColorIn(0, "ZERO", "ZERO", "ZERO", "C0")
            s.SetTevColorIn(0, "RASC", "ZERO", "ZERO", "C0")   # lerp = (1 - 0) * ras
            s.SetTevAlphaIn(0, "ZERO", "ZERO", "ZERO", "RASA")
            s.SetTevColorOp(0, g.GX_TEV_SUB, 0, 0, 1, g.GX_TEVPREV)
            s.SetTevAlphaOp(0, g.GX_TEV_ADD, 0, 0, 1, g.GX_TEVPREV)
        _, fb, _ = self.render(quad_stream((51, 0, 255, 255), extra=tev))
        r, g_, b, _ = fb.pixel(0, 0)
        self.assertAlmostEqual(r, 1.0 - 51 / 255.0)
        self.assertAlmostEqual(g_, 1.0)
        self.assertAlmostEqual(b, 0.0)


if __name__ == "__main__":
    unittest.main()
