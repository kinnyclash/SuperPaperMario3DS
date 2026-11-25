"""Writes sample PNG images produced by the host software renderer (no game data).

    python3 tools/render_samples.py docs/samples
"""
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from spm3ds.gx.cp import CommandProcessor, VertexFormat  # noqa: E402
from spm3ds.gx.png import encode_png, framebuffer_to_rgba  # noqa: E402
from spm3ds.gx.raster import Framebuffer, Texture  # noqa: E402
from spm3ds.gx.textures import decode  # noqa: E402

SIZE = 128


def vertex(x, y, z, color, uv=None):
    out = struct.pack(">3f", x, y, z) + bytes(color)
    if uv is not None:
        out += struct.pack(">2f", *uv)
    return out


def draw(op, verts):
    return bytes([op]) + struct.pack(">H", len(verts)) + b"".join(verts)


def triangles_scene(out_dir):
    cp = CommandProcessor(SIZE, SIZE)
    cp.set_vertex_format(0, VertexFormat(position="f32", color="rgba8"))
    cp.run(draw(0x90, [vertex(-0.9, -0.9, 0, (255, 60, 40, 255)),
                       vertex(0.9, -0.9, 0, (40, 200, 90, 255)),
                       vertex(0.0, 0.9, 0, (50, 90, 255, 255))]))
    fb = Framebuffer(SIZE, SIZE, clear=(0.12, 0.12, 0.16, 1.0))
    cp.render(fb)
    write(out_dir, "triangle.png", fb)


def textured_scene(out_dir):
    # 4x4 greyscale test pattern: I8 texels (0, 8, 16, ...) decoded by the texture module.
    gray = decode("I8", bytes(range(0, 256, 8)), 4, 4)
    rows = [[(p[0], p[1], p[2], 255) for p in row] for row in gray]
    texture = Texture(rows, wrap="clamp")
    white = (255, 255, 255, 255)
    uv = [(0.0, 1.0), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    corners = [(-0.8, -0.8), (0.8, -0.8), (0.8, 0.8), (-0.8, 0.8)]
    cp = CommandProcessor(SIZE, SIZE)
    cp.set_vertex_format(0, VertexFormat(position="f32", color="rgba8", texcoord="f32"))
    cp.run(draw(0x80, [vertex(x, y, 0, white, u) for (x, y), u in zip(corners, uv)]))
    fb = Framebuffer(SIZE, SIZE, clear=(0.0, 0.0, 0.0, 1.0))
    cp.render(fb, texture=texture)
    write(out_dir, "textured_quad.png", fb)


def write(out_dir, name, fb):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "wb") as f:
        f.write(encode_png(fb.w, fb.h, framebuffer_to_rgba(fb)))
    print("wrote", path)


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "samples")
    triangles_scene(target)
    textured_scene(target)
