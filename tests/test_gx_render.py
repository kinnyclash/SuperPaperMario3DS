"""Portable GX command processor and software rasterizer (spm3ds/gx/cp.py, raster.py, png.py).

Coverage counts and texel positions are worked out by hand in the comments. Pixel centres are
tested, and edges are inclusive, so an exact diagonal counts its pixels as covered.
"""
import struct
import unittest
import zlib

from spm3ds.gx.cp import CommandProcessor, GXError, VertexFormat
from spm3ds.gx.png import encode_png, framebuffer_to_rgba
from spm3ds.gx.raster import Framebuffer, Texture

RED = (255, 0, 0, 255)
GREEN = (0, 255, 0, 255)


def vert_f32(x, y, z, color=RED, uv=None):
    out = struct.pack(">3f", x, y, z) + bytes(color)
    if uv is not None:
        out += struct.pack(">2f", *uv)
    return out


def draw(op, verts):
    return bytes([op]) + struct.pack(">H", len(verts)) + b"".join(verts)


def processor(size=4, fmt=None):
    cp = CommandProcessor(size, size)
    cp.set_vertex_format(0, fmt or VertexFormat(position="f32", color="rgba8"))
    return cp


class CoverageTests(unittest.TestCase):
    def test_right_triangle_covers_ten_pixels_on_a_4x4_screen(self):
        # NDC (-1,-1),(1,-1),(-1,1) maps to screen (0,4),(4,4),(0,0): the diagonal y = x.
        # Centres with x <= y are covered: 4+3+2+1 = 10.
        cp = processor().run(draw(0x90, [vert_f32(-1, -1, 0), vert_f32(1, -1, 0),
                                         vert_f32(-1, 1, 0)]))
        fb = Framebuffer(4, 4)
        self.assertEqual(cp.render(fb), 10)
        self.assertEqual(fb.pixel(0, 3), (1.0, 0.0, 0.0, 1.0))
        self.assertEqual(fb.pixel(3, 0), (0.0, 0.0, 0.0, 1.0))

    def test_full_screen_quad_covers_every_pixel(self):
        quad = [vert_f32(-1, -1, 0), vert_f32(1, -1, 0), vert_f32(1, 1, 0), vert_f32(-1, 1, 0)]
        cp = processor().run(draw(0x80, quad))
        fb = Framebuffer(4, 4)
        self.assertEqual(cp.render(fb), 16)

    def test_triangle_strip_makes_two_triangles(self):
        strip = [vert_f32(-1, 1, 0), vert_f32(-1, -1, 0), vert_f32(1, 1, 0), vert_f32(1, -1, 0)]
        cp = processor().run(draw(0x98, strip))
        self.assertEqual(len(cp.triangles), 2)

    def test_fan_makes_triangles_from_the_first_vertex(self):
        fan = [vert_f32(0, 0, 0), vert_f32(1, 0, 0), vert_f32(1, 1, 0), vert_f32(0, 1, 0)]
        cp = processor().run(draw(0xA0, fan))
        self.assertEqual(len(cp.triangles), 2)
        self.assertEqual(cp.triangles[0][0]["x"], cp.triangles[1][0]["x"])

    def test_lines_are_parsed_but_recorded_as_unsupported(self):
        lines = [vert_f32(-1, 0, 0), vert_f32(1, 0, 0)]
        cp = processor().run(draw(0xA8, lines))
        self.assertEqual(cp.unsupported["lines"], 2)
        self.assertEqual(cp.triangles, [])


class CullAndDepthTests(unittest.TestCase):
    def test_back_face_is_culled_by_default(self):
        reversed_tri = [vert_f32(-1, -1, 0), vert_f32(-1, 1, 0), vert_f32(1, -1, 0)]
        cp = processor().run(draw(0x90, reversed_tri))
        self.assertEqual(cp.render(Framebuffer(4, 4)), 0)

    def test_cull_none_draws_back_faces(self):
        reversed_tri = [vert_f32(-1, -1, 0), vert_f32(-1, 1, 0), vert_f32(1, -1, 0)]
        cp = processor().run(draw(0x90, reversed_tri))
        cp.cull = "none"
        self.assertEqual(cp.render(Framebuffer(4, 4)), 10)

    def test_nearer_triangle_wins_regardless_of_draw_order(self):
        quad = [vert_f32(-1, -1, 0.5, GREEN), vert_f32(1, -1, 0.5, GREEN),
                vert_f32(1, 1, 0.5, GREEN), vert_f32(-1, 1, 0.5, GREEN)]
        near = [vert_f32(-1, -1, -0.5, RED), vert_f32(1, -1, -0.5, RED),
                vert_f32(1, 1, -0.5, RED), vert_f32(-1, 1, -0.5, RED)]
        for order in ((quad, near), (near, quad)):
            cp = processor()
            for verts in order:
                cp.run(draw(0x80, verts))
            fb = Framebuffer(4, 4)
            cp.render(fb)
            self.assertEqual(fb.pixel(1, 1), (1.0, 0.0, 0.0, 1.0), "nearer red must win")


class BlendAndTextureTests(unittest.TestCase):
    def test_alpha_blend_over_white(self):
        # Source alpha 128/255 = 0.50196. Red channel: 1.0 * a + 1.0 * (1 - a) = 1.0.
        # Green: 0 * a + 1.0 * (1 - a) = 0.49804.
        half_red = (255, 0, 0, 128)
        cp = processor().run(draw(0x80, [vert_f32(-1, -1, 0, half_red), vert_f32(1, -1, 0, half_red),
                                         vert_f32(1, 1, 0, half_red),
                                         vert_f32(-1, 1, 0, half_red)]))
        fb = Framebuffer(4, 4, clear=(1.0, 1.0, 1.0, 1.0))
        cp.render(fb, blend=True)
        r, g, b, _ = fb.pixel(2, 2)
        self.assertAlmostEqual(r, 1.0, places=3)
        self.assertAlmostEqual(g, 1.0 - 128 / 255, places=3)

    def test_texture_sampled_at_hand_computed_texels(self):
        # 2x2 texture: row 0 = red, green; row 1 = blue, white. Quad uv: top-left (0,0),
        # top-right (1,0), bottom-right (1,1), bottom-left (0,1). Vertex colour is white.
        white = (255, 255, 255, 255)
        # Counter-clockwise in NDC (front face): bottom-left, bottom-right, top-right, top-left.
        uv = [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
        corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
        verts = [vert_f32(x, y, 0, white, uv=u) for (x, y), u in zip(corners, uv)]
        fmt = VertexFormat(position="f32", color="rgba8", texcoord="f32")
        cp = processor(fmt=fmt).run(draw(0x80, verts))
        tex = Texture([[(255, 0, 0, 255), (0, 255, 0, 255)],
                       [(0, 0, 255, 255), (255, 255, 255, 255)]])
        fb = Framebuffer(4, 4)
        cp.render(fb, texture=tex)
        self.assertEqual(fb.pixel(0, 0), (1.0, 0.0, 0.0, 1.0))   # top-left: red
        self.assertEqual(fb.pixel(3, 0), (0.0, 1.0, 0.0, 1.0))   # top-right: green
        self.assertEqual(fb.pixel(0, 3), (0.0, 0.0, 1.0, 1.0))   # bottom-left: blue
        self.assertEqual(fb.pixel(3, 3), (1.0, 1.0, 1.0, 1.0))   # bottom-right: white

    def test_repeat_wrap_samples_modulo(self):
        tex = Texture([[(10, 0, 0, 255), (20, 0, 0, 255)]], wrap="repeat")
        self.assertEqual(tex.sample(1.0 - 1e-9, 0.0)[0], 20 / 255.0)
        self.assertEqual(tex.sample(1.0 + 0.5, 0.0)[0], 20 / 255.0)   # wraps to x = 1


class StreamTests(unittest.TestCase):
    def test_s16_positions_use_fractional_bits(self):
        fmt = VertexFormat(position="s16", position_frac=14, color="rgba8")
        raw = struct.pack(">3h", 16384, -16384, 0) + bytes(RED)     # 1.0, -1.0, 0.0
        cp = processor(fmt=fmt)
        v = cp._read_vertex(raw, 0, fmt)
        self.assertEqual(v["pos"], (1.0, -1.0, 0.0))

    def test_register_writes_are_accepted(self):
        stream = (bytes([0x08, 0x50]) + struct.pack(">I", 0x1234)
                  + bytes([0x61]) + struct.pack(">I", (0x42 << 24) | 0x0ABCDE)
                  + bytes([0x10]) + struct.pack(">HH", 1, 0) + struct.pack(">II", 0x3F800000, 0)
                  + bytes([0x00]))
        cp = processor().run(stream)
        self.assertEqual(cp.cp[0x50], 0x1234)
        self.assertEqual(cp.bp[0x42], 0x0ABCDE)
        self.assertEqual(cp.xf[0], 1.0)

    def test_unknown_command_is_refused(self):
        with self.assertRaises(GXError):
            processor().run(bytes([0x05]))


class PngTests(unittest.TestCase):
    def test_png_has_signature_and_decodable_pixels(self):
        pixels = bytes([255, 0, 0, 255] * 4)
        data = encode_png(2, 2, pixels)
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        idat_at = data.index(b"IDAT")
        length = struct.unpack(">I", data[idat_at - 4:idat_at])[0]
        raw = zlib.decompress(data[idat_at + 4:idat_at + 4 + length])
        self.assertEqual(len(raw), 2 * (1 + 2 * 4))
        self.assertEqual(raw[1:5], bytes([255, 0, 0, 255]))

    def test_framebuffer_export_clamps_and_rounds(self):
        fb = Framebuffer(1, 1, clear=(2.0, -1.0, 0.5, 1.0))
        self.assertEqual(framebuffer_to_rgba(fb), bytes([255, 0, 128, 255]))


if __name__ == "__main__":
    unittest.main()
