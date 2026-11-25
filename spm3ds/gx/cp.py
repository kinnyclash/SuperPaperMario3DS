"""Portable GX command processor (host side). No citro3d, no 3DS code.

Command stream (opcodes from Dolphin's GX opcode table and libogc's GX_LOAD_* macros):
  0x00        NOP
  0x08 reg32  CP register write: u8 register, u32 value
  0x10        XF write: u32 ((count-1) << 16 | base), then count x u32
  0x20..0x38  indexed XF loads (position, normal, texture, light): u32 parsed and recorded
  0x48        call display list: u32 address, u32 size (followed when read_memory is set)
  0x61 w32    BP register write: top byte = register, low 24 bits = value
  0x80..0xBF  draw: (op >> 3) & 15 = primitive (0 quads, 2 triangles, 3 strip, 4 fan,
              5 lines, 6 line strip, 7 points); op & 7 = vertex format (VAT) index;
              then u16 vertex count and the vertices.

Vertex decoding has two modes:
  * Hardware mode, used once CP register 0x50 (VCD low) has been written: the vertex layout
    comes from the VCD and VAT registers (decoded in regs.py), with direct and indexed
    (8- and 16-bit) attributes. Indexed attributes are read from the array base and stride
    registers through `read_memory(physical_address, length)`.
  * API mode, for tests and tools that set a VertexFormat directly (set_vertex_format).

Transform: the position matrix comes from XF memory (row-major 3x4) at 4 * index, where the
index is the per-vertex PNMTXIDX attribute or else the current matrix index (CP 0x30 or
XF 0x1018, low 6 bits). The projection comes from XF 0x1020 when written, else from
set_projection(). The viewport comes from XF 0x101A when written, else the full frame.

Render state from BP registers when written: cull (gen mode), number of TEV stages, Z mode,
blend mode, TEV colour/alpha environments, TEV order and TEV colour registers.

Not modelled (recorded in `unsupported`): lighting (colour channels pass the vertex colour),
texture coordinate generation (texcoord 0 is used as is), konst colours, TEV compare modes,
indirect textures, alpha test, fog, lines and points.
"""
import struct
from collections import Counter
from dataclasses import dataclass

from . import regs

PRIMITIVES = {0: "quads", 2: "triangles", 3: "triangle_strip", 4: "triangle_fan",
              5: "lines", 6: "line_strip", 7: "points"}
GX_DEPTH_MAX = 16777215.0     # libogc Zfactor
VIEWPORT_OFFSET = 342.0       # libogc Yfactor, added to the viewport origin


class GXError(Exception):
    pass


@dataclass
class VertexFormat:
    """API-mode vertex format (see the module docstring)."""
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


def _f32(word):
    return struct.unpack(">f", struct.pack(">I", word & 0xFFFFFFFF))[0]


class CommandProcessor:
    def __init__(self, width, height, read_memory=None):
        self.width, self.height = width, height
        self.read_memory = read_memory
        self.bp = {}
        self.cp = {}
        self.xf = [0.0] * 0x1000          # XF matrix memory as floats
        self.xf_regs = {}                 # XF registers 0x1000.. as raw words
        self.set_position_matrix([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0],
                                  [0.0, 0.0, 1.0, 0.0]])
        self.vat = {0: VertexFormat()}
        self.projection = [[1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
        self.cull = "back"                # "none", "back", "front", "all"
        self.triangles = []               # assembled triangles in screen space
        self.unsupported = Counter()
        self.commands = 0
        self._bp_mask = 0xFFFFFF
        self._textures = {}        # (address, format, width, height, wrap) -> Texture

    # ---- configuration (API, not command stream) ----
    def set_vertex_format(self, index, fmt):
        self.vat[index] = fmt

    def set_projection(self, matrix):
        self.projection = [list(map(float, row)) for row in matrix]

    def set_position_matrix(self, matrix3x4, index=0):
        """Writes a 3x4 position matrix into XF memory at 4 * index (row-major)."""
        for r in range(3):
            for c in range(4):
                self.xf[4 * index + r * 4 + c] = float(matrix3x4[r][c])

    # ---- stream parsing ----
    def run(self, data, depth=0):
        pos = 0
        n = len(data)
        while pos < n:
            op = data[pos]
            self.commands += 1
            if op == 0x00:
                pos += 1
            elif op == 0x08:
                reg = data[pos + 1]
                self.cp[reg] = struct.unpack_from(">I", data, pos + 2)[0]
                pos += 6
            elif op == 0x10:
                head = struct.unpack_from(">I", data, pos + 1)[0]
                count = (head >> 16) + 1
                base = head & 0xFFFF
                words = struct.unpack_from(">%dI" % count, data, pos + 5)
                for i, w in enumerate(words):
                    self._xf_write(base + i, w)
                pos += 5 + 4 * count
            elif op in (0x20, 0x28, 0x30, 0x38):
                self.unsupported["indexed_xf_load"] += 1
                pos += 5
            elif op == 0x48:
                addr, size = struct.unpack_from(">II", data, pos + 1)
                pos += 9
                if self.read_memory is None or depth > 0:
                    self.unsupported["display_list"] += 1
                else:
                    self.run(self.read_memory(addr, size), depth + 1)
            elif op == 0x61:
                self._bp_write(struct.unpack_from(">I", data, pos + 1)[0])
                pos += 5
            elif 0x80 <= op <= 0xBF:
                pos = self._draw(data, pos, op)
            else:
                raise GXError(f"unknown GX command 0x{op:02X} at offset {pos}")
        return self

    def _xf_write(self, addr, word):
        if addr < 0x1000:
            self.xf[addr] = _f32(word)
        else:
            self.xf_regs[addr] = word & 0xFFFFFFFF

    def _bp_write(self, word):
        reg, value = word >> 24, word & 0xFFFFFF
        if reg == 0xFE:          # mask for the next BP write
            self._bp_mask = value
            return
        mask, self._bp_mask = self._bp_mask, 0xFFFFFF
        old = self.bp.get(reg, 0)
        value = (old & ~mask & 0xFFFFFF) | (value & mask)
        self.bp[reg] = value
        if reg == 0x00:
            self.cull = regs.decode_genmode(value).cull

    # ---- vertex decoding ----
    def _hardware_mode(self):
        return 0x50 in self.cp

    def _draw(self, data, pos, op):
        prim = (op >> 3) & 15
        if prim == 1:           # 0x88 is an alias of quads in the hardware table
            prim = 0
        name = PRIMITIVES.get(prim)
        if name is None:
            raise GXError(f"unknown primitive opcode 0x{op:02X}")
        count = struct.unpack_from(">H", data, pos + 1)[0]
        pos += 3
        verts = []
        if self._hardware_mode():
            vcd = regs.decode_vcd(self.cp.get(0x50, 0), self.cp.get(0x60, 0))
            idx = op & 7
            vat = regs.decode_vat(self.cp.get(0x70 + idx, 0), self.cp.get(0x80 + idx, 0),
                                  self.cp.get(0x90 + idx, 0))
            for _ in range(count):
                vert, pos = self._read_hw_vertex(data, pos, vcd, vat)
                verts.append(vert)
        else:
            fmt = self.vat.get(op & 7)
            if fmt is None:
                raise GXError(f"vertex format {op & 7} is not set")
            size = _size(fmt)
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
        return {"pos": (x, y, z), "color": color, "uv": uv, "mtx": None}

    def _attr_bytes(self, data, pos, kind, attr, nbytes):
        """Return (bytes of the attribute, new stream position) for direct or indexed data."""
        if kind == regs.DIRECT:
            return data[pos:pos + nbytes], pos + nbytes
        if kind == regs.INDEX8:
            index, pos = data[pos], pos + 1
        else:
            index, pos = struct.unpack_from(">H", data, pos)[0], pos + 2
        if self.read_memory is None:
            raise GXError(f"indexed {attr} needs read_memory")
        array = regs.ARRAY_INDEX[attr]
        base = self.cp.get(0xA0 + array, 0)
        stride = self.cp.get(0xB0 + array, 0)
        return self.read_memory(base + index * stride, nbytes), pos

    @staticmethod
    def _components(raw, compsize, count, frac):
        if compsize == 4:
            return list(struct.unpack(">%df" % count, raw[:4 * count]))
        size = regs.COMP_SIZE[compsize]
        signed = regs.COMP_SIGNED[compsize]
        code = {(1, False): "B", (1, True): "b", (2, False): "H", (2, True): "h"}[(size, signed)]
        values = struct.unpack(">%d%s" % (count, code), raw[:size * count])
        scale = 2.0 ** -frac
        return [v * scale for v in values]

    def _read_hw_vertex(self, data, pos, vcd, vat):
        vert = {"pos": (0.0, 0.0, 0.0), "color": (1.0, 1.0, 1.0, 1.0), "uv": (0.0, 0.0),
                "mtx": None}
        for attr in regs.VCD_ATTRS:
            kind = vcd[attr]
            if kind == regs.NONE:
                continue
            if attr.endswith("mtxidx"):
                if kind != regs.DIRECT:
                    raise GXError(f"{attr} must be direct")
                if attr == "pnmtxidx":
                    vert["mtx"] = data[pos] & 0x3F
                pos += 1
                continue
            fmt = vat.attrs[attr]
            if attr == "pos":
                n = 3 if fmt.comptype else 2
                nbytes = regs.COMP_SIZE[fmt.compsize] * n
                raw, pos = self._attr_bytes(data, pos, kind, attr, nbytes)
                comps = self._components(raw, fmt.compsize, n, fmt.frac)
                vert["pos"] = tuple(comps + [0.0] * (3 - n))
            elif attr == "nrm":
                n = 9 if fmt.comptype else 3
                if fmt.comptype and vat.normal_index3 and kind != regs.DIRECT:
                    self.unsupported["nbt3_indexed"] += 1
                nbytes = regs.COMP_SIZE[fmt.compsize] * n
                _, pos = self._attr_bytes(data, pos, kind, attr, nbytes)
                self.unsupported["lighting"] += 1
            elif attr in ("clr0", "clr1"):
                raw, pos = self._attr_bytes(data, pos, kind, attr, regs.COLOR_SIZE[fmt.compsize])
                if attr == "clr0":
                    vert["color"] = tuple(c / 255.0 for c in regs.decode_color(raw, fmt.compsize))
            else:  # texN
                n = 2 if fmt.comptype else 1
                nbytes = regs.COMP_SIZE[fmt.compsize] * n
                raw, pos = self._attr_bytes(data, pos, kind, attr, nbytes)
                if attr == "tex0":
                    comps = self._components(raw, fmt.compsize, n, fmt.frac)
                    vert["uv"] = tuple(comps + [0.0] * (2 - n))
        return vert, pos

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
    def _current_matrix_index(self):
        if 0x1018 in self.xf_regs:
            return self.xf_regs[0x1018] & 0x3F
        return self.cp.get(0x30, 0) & 0x3F

    def _projection(self):
        if all(0x1020 + i in self.xf_regs for i in range(7)):
            words = [_f32(self.xf_regs[0x1020 + i]) for i in range(6)] + [self.xf_regs[0x1026]]
            return regs.projection_matrix(words), True
        return self.projection, False

    def _transform(self, vert):
        idx = vert["mtx"] if vert["mtx"] is not None else self._current_matrix_index()
        base = 4 * idx
        m = [[self.xf[base + r * 4 + c] for c in range(4)] for r in range(3)]
        x, y, z = vert["pos"]
        view = _mat_mul_vec(m, [x, y, z, 1.0])
        proj, hw_proj = self._projection()
        clip = _mat_mul_vec(proj, view + [1.0])
        w = clip[3] if clip[3] != 0.0 else 1e-9
        nx, ny, nz = clip[0] / w, clip[1] / w, clip[2] / w
        if all(0x101A + i in self.xf_regs for i in range(6)):
            vp = [_f32(self.xf_regs[0x101A + i]) for i in range(6)]
            sx = nx * vp[0] + vp[3] - VIEWPORT_OFFSET
            sy = ny * vp[1] + vp[4] - VIEWPORT_OFFSET
            sz = (nz * vp[2] + vp[5]) / GX_DEPTH_MAX
        else:
            sx = (nx * 0.5 + 0.5) * self.width
            sy = (1.0 - (ny * 0.5 + 0.5)) * self.height     # y grows downwards
            # GX clip-space z runs from -w (near) to 0 (far); the API path keeps [-1, 1].
            sz = nz + 1.0 if hw_proj else nz * 0.5 + 0.5
        return {"x": sx, "y": sy, "z": sz, "ndc_xy": (nx, ny), "invw": 1.0 / w,
                "color": vert["color"], "uv": vert["uv"]}

    # ---- render state from BP ----
    def tev_setup(self):
        """Return a list of TEV stages and the register file, or None if no TEV was set."""
        if not any(0xC0 <= r <= 0xDF for r in self.bp):
            return None
        gen = regs.decode_genmode(self.bp.get(0x00, 0))
        stages = []
        for s in range(gen.num_tev_stages):
            order = regs.decode_tev_order(self.bp.get(0x28 + s // 2, 0), s)
            stages.append((regs.decode_tev_color_env(self.bp.get(0xC0 + 2 * s, 0)),
                           regs.decode_tev_alpha_env(self.bp.get(0xC1 + 2 * s, 0)), order))
        reg_file = []
        for r in range(4):
            lo, hi = self.bp.get(0xE0 + 2 * r, 0), self.bp.get(0xE1 + 2 * r, 0)
            reg_file.append(tuple(v / 255.0 for v in regs.decode_tev_reg_pair(lo, hi)))
        return stages, reg_file

    # ---- textures ----
    def texture_for_map(self, texmap):
        """Decode texture map 0..7 from guest memory (cached), or None if it is not usable."""
        from .raster import Texture
        from .textures import FORMATS, decode
        if texmap > 7 or self.read_memory is None:
            return None
        if regs.TEX_IMAGE0[texmap] not in self.bp or regs.TEX_IMAGE3[texmap] not in self.bp:
            return None
        img = regs.decode_tex_image(self.bp[regs.TEX_IMAGE0[texmap]],
                                    self.bp[regs.TEX_IMAGE3[texmap]])
        wrap_s, _, _, _ = regs.decode_tex_mode0(self.bp.get(regs.TEX_MODE0[texmap], 0))
        name = regs.TEX_FORMATS.get(img.fmt)
        if name is None:
            self.unsupported[f"texture_format_{img.fmt}"] += 1
            return None
        key = (img.address, img.fmt, img.width, img.height, wrap_s)
        if key not in self._textures:
            f = FORMATS[name]
            tiles = (-(-img.width // f.tile_w)) * (-(-img.height // f.tile_h))
            data = self.read_memory(img.address, tiles * f.tile_bytes)
            self._textures[key] = Texture(decode(name, data, img.width, img.height), wrap=wrap_s)
        return self._textures[key]

    def _tev_texture(self, stages):
        """The single texture the software renderer can use: the first enabled TEV texmap."""
        maps = {order.texmap for _, _, order in stages if order.tex_enable}
        if len(maps) > 1:
            self.unsupported["multiple_texture_maps"] += 1
        for _, _, order in stages:
            if order.tex_enable:
                return self.texture_for_map(order.texmap)
        return None

    # ---- rendering ----
    def render(self, fb, texture=None, blend=False, depth_test=True):
        """Rasterize every assembled triangle into a framebuffer, applying the cull mode.

        Front faces are counter-clockwise in NDC (x right, y up). Because screen y points down,
        a front face has negative area in screen space. BP Z mode, blend mode and TEV state
        override the keyword arguments when they have been written. Returns pixels written.
        """
        from .raster import draw_triangle
        from .tev import make_shader
        depth_func, depth_write = "less", True
        if 0x40 in self.bp:
            z = regs.decode_zmode(self.bp[0x40])
            depth_test, depth_func, depth_write = z.enable, z.func, z.update
        blend_mode = None
        if 0x41 in self.bp:
            bm = regs.decode_blend(self.bp[0x41])
            blend = False
            if bm.enable and not bm.logic:
                blend_mode = bm
            elif bm.logic:
                self.unsupported["logic_op"] += 1
        setup = self.tev_setup()
        shader = None
        if setup is not None:
            shader = make_shader(setup[0], setup[1], self.unsupported)
            if texture is None:
                texture = self._tev_texture(setup[0])
        written = 0
        for tri in self.triangles:
            a, b, c = tri
            area = (b["x"] - a["x"]) * (c["y"] - a["y"]) - (b["y"] - a["y"]) * (c["x"] - a["x"])
            if self.cull == "all":
                continue
            if self.cull == "back" and area > 0:
                continue
            if self.cull == "front" and area < 0:
                continue
            written += draw_triangle(fb, tri, texture=texture, depth_test=depth_test, blend=blend,
                                     shader=shader, depth_func=depth_func,
                                     depth_write=depth_write, blend_mode=blend_mode)
        return written
