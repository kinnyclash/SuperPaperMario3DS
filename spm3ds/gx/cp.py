"""Portable GX command processor (host side). No citro3d, no 3DS code.

Command stream (opcodes from Dolphin's GX opcode table, Source/Core/VideoCommon/OpcodeDecoding.h):
  0x00        NOP
  0x08 reg32  CP register write: u8 register, u32 value (stored only, not interpreted)
  0x10        XF write: u16 (count-1), u16 base, count x u32
  0x61 w32    BP register write: top byte = register, low 24 bits = value
  0x80..0xBF  draw: (op >> 3) & 15 = primitive (0 quads, 2 triangles, 3 strip, 4 fan,
              5 lines, 6 line strip, 7 points); op & 7 = vertex attribute table index;
              then u16 vertex count and the vertices in that format.

Supported here: direct vertices (not indexed), position as f32 or s16 with fractional bits,
colour as RGBA8, texture coordinate as f32. Triangles, strips, fans and quads are assembled and
rasterized. Lines and points are parsed but not drawn (recorded as unsupported).

Transform: the 3x4 position matrix is read from XF words 0..11 (row-major), and the 4x4
projection is set with set_projection(). Each vertex goes position -> view -> clip -> NDC
-> viewport pixels.

UNVERIFIED: the CP register bit layout (vertex descriptor) is NOT decoded from hardware
registers. Vertex formats are set with set_vertex_format(). Decoding real CP/BP fields
belongs with citro3d / hardware verification.
"""
import struct
from collections import Counter
from dataclasses import dataclass

PRIMITIVES = {0: "quads", 2: "triangles", 3: "triangle_strip", 4: "triangle_fan",
              5: "lines", 6: "line_strip", 7: "points"}


class GXError(Exception):
    pass


@dataclass
class VertexFormat:
    position: str = "f32"        # "f32" or "s16"
    position_frac: int = 0       # for s16: value = raw / 2^frac
    color: str = "none"          # "none" or "rgba8"
    texcoord: str = "none"       # "none" or "f32"


def _size(fmt):
    total = {"f32": 12, "s16": 6}[fmt.position]
    total += 4 if fmt.color == "rgba8" else 0
    total += 8 if fmt.texcoord == "f32" else 0
    return total


def _mat_mul_vec(m, v):
    return [sum(m[r][c] * v[c] for c in range(len(v))) for r in range(len(m))]


class CommandProcessor:
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.bp = {}
        self.cp = {}
        self.xf = [0.0] * 0x1000
        self.set_position_matrix([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]])
        self.vat = {0: VertexFormat()}
        self.projection = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
        self.cull = "back"       # "none", "back", "front"
        self.triangles = []      # assembled triangles in screen space (see _transform)
        self.unsupported = Counter()
        self.commands = 0

    # ---- configuration (API, not command stream) ----
    def set_vertex_format(self, index, fmt):
        self.vat[index] = fmt

    def set_projection(self, matrix):
        self.projection = [list(map(float, row)) for row in matrix]

    def set_position_matrix(self, matrix3x4):
        """Writes the 3x4 position matrix into XF words 0..11 (row-major)."""
        for r in range(3):
            for c in range(4):
                self.xf[r * 4 + c] = float(matrix3x4[r][c])

    # ---- stream parsing ----
    def run(self, data):
        pos = 0
        while pos < len(data):
            op = data[pos]
            self.commands += 1
            if op == 0x00:
                pos += 1
            elif op == 0x08:
                reg = data[pos + 1]
                self.cp[reg] = struct.unpack_from(">I", data, pos + 2)[0]
                pos += 6
            elif op == 0x10:
                count = struct.unpack_from(">H", data, pos + 1)[0] + 1
                base = struct.unpack_from(">H", data, pos + 3)[0]
                words = struct.unpack_from(">%dI" % count, data, pos + 5)
                for i, w in enumerate(words):
                    self.xf[(base + i) & 0xFFF] = struct.unpack(">f", struct.pack(">I", w))[0]
                pos += 5 + 4 * count
            elif op == 0x61:
                word = struct.unpack_from(">I", data, pos + 1)[0]
                self.bp[word >> 24] = word & 0xFFFFFF
                pos += 5
            elif 0x80 <= op <= 0xBF:
                pos = self._draw(data, pos, op)
            else:
                raise GXError(f"unknown GX command 0x{op:02X} at offset {pos}")
        return self

    def _draw(self, data, pos, op):
        prim = (op >> 3) & 15
        if prim == 1:           # 0x88 is an alias of quads in the hardware table
            prim = 0
        name = PRIMITIVES.get(prim)
        if name is None:
            raise GXError(f"unknown primitive opcode 0x{op:02X}")
        fmt = self.vat.get(op & 7)
        if fmt is None:
            raise GXError(f"vertex format {op & 7} is not set")
        count = struct.unpack_from(">H", data, pos + 1)[0]
        pos += 3
        size = _size(fmt)
        verts = []
        for _ in range(count):
            verts.append(self._read_vertex(data, pos, fmt))
            pos += size
        self._assemble(name, verts)
        return pos

    def _read_vertex(self, data, pos, fmt):
        if fmt.position == "f32":
            x, y, z = struct.unpack_from(">3f", data, pos)
            pos += 12
        else:
            raw = struct.unpack_from(">3h", data, pos)
            scale = 2.0 ** -fmt.position_frac
            x, y, z = (r * scale for r in raw)
            pos += 6
        color = (1.0, 1.0, 1.0, 1.0)
        if fmt.color == "rgba8":
            color = tuple(b / 255.0 for b in data[pos:pos + 4])
            pos += 4
        uv = (0.0, 0.0)
        if fmt.texcoord == "f32":
            uv = struct.unpack_from(">2f", data, pos)
        return {"pos": (x, y, z), "color": color, "uv": uv}

    # ---- primitive assembly ----
    def _assemble(self, name, v):
        if name == "triangles":
            tris = [(v[i], v[i + 1], v[i + 2]) for i in range(0, len(v) - 2, 3)]
        elif name == "triangle_strip":
            tris = []
            for k in range(len(v) - 2):
                a, b = (v[k], v[k + 1]) if k % 2 == 0 else (v[k + 1], v[k])
                tris.append((a, b, v[k + 2]))
        elif name == "triangle_fan":
            tris = [(v[0], v[k], v[k + 1]) for k in range(1, len(v) - 1)]
        elif name == "quads":
            tris = []
            for i in range(0, len(v) - 3, 4):
                tris.append((v[i], v[i + 1], v[i + 2]))
                tris.append((v[i], v[i + 2], v[i + 3]))
        else:
            self.unsupported[name] += len(v)
            return
        for tri in tris:
            self.triangles.append(tuple(self._transform(x) for x in tri))

    # ---- transform ----
    def _transform(self, vert):
        m = [[self.xf[r * 4 + c] for c in range(4)] for r in range(3)]
        x, y, z = vert["pos"]
        view = _mat_mul_vec(m, [x, y, z, 1.0])
        clip = _mat_mul_vec(self.projection, view + [1.0])
        w = clip[3] if clip[3] != 0.0 else 1e-9
        nx, ny, nz = clip[0] / w, clip[1] / w, clip[2] / w
        inv_w = 1.0 / w
        sx = (nx * 0.5 + 0.5) * self.width
        sy = (1.0 - (ny * 0.5 + 0.5)) * self.height     # y grows downwards on the screen
        return {"x": sx, "y": sy, "z": nz * 0.5 + 0.5, "ndc_xy": (nx, ny), "invw": inv_w,
                "color": vert["color"], "uv": vert["uv"]}

    # ---- rendering ----
    def render(self, fb, texture=None, blend=False, depth_test=True):
        """Rasterize every assembled triangle into a framebuffer, applying the cull mode.

        Front faces are counter-clockwise in NDC (x right, y up). Because screen y points down,
        a front face has negative area in screen space. Returns the number of pixels written.
        """
        from .raster import draw_triangle
        written = 0
        for tri in self.triangles:
            a, b, c = tri
            area = (b["x"] - a["x"]) * (c["y"] - a["y"]) - (b["y"] - a["y"]) * (c["x"] - a["x"])
            if self.cull == "back" and area > 0:
                continue
            if self.cull == "front" and area < 0:
                continue
            written += draw_triangle(fb, tri, texture=texture, depth_test=depth_test, blend=blend)
        return written
