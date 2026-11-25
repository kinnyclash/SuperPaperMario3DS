"""Host-side software rasterizer for GX-style triangles (no citro3d).

Pixels are tested at their centres (x + 0.5, y + 0.5). A pixel is covered when all three
edge functions are >= 0 (edges included). Colour and texture coordinates are interpolated
with perspective correction: attribute/w and 1/w are interpolated linearly in screen space,
then divided. Depth is interpolated linearly in screen space and tested with 'less'.
"""


class Texture:
    """Decoded texels as rows of (r, g, b, a) integers 0..255."""

    def __init__(self, rows, wrap="clamp"):
        self.rows = rows
        self.h = len(rows)
        self.w = len(rows[0])
        self.wrap = wrap

    def _index(self, i, n):
        if self.wrap == "repeat":
            return i % n
        return min(max(i, 0), n - 1)

    def sample(self, u, v):
        """Nearest-texel sample. (u, v) in 0..1 with v = 0 at the first row."""
        x = self._index(int(u * self.w // 1), self.w)
        y = self._index(int(v * self.h // 1), self.h)
        r, g, b, a = self.rows[y][x]
        return (r / 255.0, g / 255.0, b / 255.0, a / 255.0)


class Framebuffer:
    def __init__(self, w, h, clear=(0.0, 0.0, 0.0, 1.0)):
        self.w, self.h = w, h
        self.color = [[clear] * w for _ in range(h)]
        self.depth = [[1.0] * w for _ in range(h)]

    def clear(self, rgba=(0.0, 0.0, 0.0, 1.0)):
        self.color = [[rgba] * self.w for _ in range(self.h)]
        self.depth = [[1.0] * self.w for _ in range(self.h)]

    def pixel(self, x, y):
        return self.color[y][x]


def _edge(ax, ay, bx, by, px, py):
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


DEPTH_FUNCS = {
    "never": lambda z, d: False, "less": lambda z, d: z < d, "equal": lambda z, d: z == d,
    "lequal": lambda z, d: z <= d, "greater": lambda z, d: z > d, "nequal": lambda z, d: z != d,
    "gequal": lambda z, d: z >= d, "always": lambda z, d: True,
}


def _blend_factor(code, src, dst, for_src):
    """GX blend factor (libogc GX_BL_*): for the source, codes 2/3 are the destination colour;
    for the destination, codes 2/3 are the source colour."""
    if code == 0:
        return (0.0,) * 4
    if code == 1:
        return (1.0,) * 4
    if code in (2, 3):
        other = dst if for_src else src
        return tuple(other) if code == 2 else tuple(1.0 - x for x in other)
    if code == 4:
        return (src[3],) * 4
    if code == 5:
        return (1.0 - src[3],) * 4
    if code == 6:
        return (dst[3],) * 4
    return (1.0 - dst[3],) * 4


def gx_blend(mode, src, dst):
    """Apply a regs.BlendMode to a source and destination colour (each RGBA in 0..1)."""
    if mode.subtract:
        return tuple(max(0.0, dst[k] - src[k]) for k in range(4))
    fs = _blend_factor(mode.src, src, dst, True)
    fd = _blend_factor(mode.dst, src, dst, False)
    return tuple(min(1.0, src[k] * fs[k] + dst[k] * fd[k]) for k in range(4))


def draw_triangle(fb, tri, texture=None, depth_test=True, blend=False, shader=None,
                  depth_func="less", depth_write=True, blend_mode=None):
    """Rasterize one screen-space triangle. tri = three dicts from CommandProcessor.

    shader(ras_rgba, texel_rgba_or_None) replaces the default MODULATE when given (see tev.py).
    blend_mode is a regs.BlendMode; `blend` is the older fixed source-alpha blend.
    Returns the number of pixels written."""
    passes = DEPTH_FUNCS[depth_func]
    (a, b, c) = tri
    area = _edge(a["x"], a["y"], b["x"], b["y"], c["x"], c["y"])
    if area == 0.0:
        return 0
    min_x = max(int(min(a["x"], b["x"], c["x"])), 0)
    max_x = min(int(max(a["x"], b["x"], c["x"])) + 1, fb.w - 1)
    min_y = max(int(min(a["y"], b["y"], c["y"])), 0)
    max_y = min(int(max(a["y"], b["y"], c["y"])) + 1, fb.h - 1)
    written = 0
    for y in range(min_y, max_y + 1):
        py = y + 0.5
        for x in range(min_x, max_x + 1):
            px = x + 0.5
            w0 = _edge(b["x"], b["y"], c["x"], c["y"], px, py)
            w1 = _edge(c["x"], c["y"], a["x"], a["y"], px, py)
            w2 = _edge(a["x"], a["y"], b["x"], b["y"], px, py)
            if area < 0:
                w0, w1, w2 = -w0, -w1, -w2
            if w0 < 0 or w1 < 0 or w2 < 0:
                continue
            l0, l1, l2 = w0 / abs(area), w1 / abs(area), w2 / abs(area)
            z = l0 * a["z"] + l1 * b["z"] + l2 * c["z"]
            if depth_test and not passes(z, fb.depth[y][x]):
                continue
            # Perspective-correct interpolation: attributes stored as attr * invw.
            denom = l0 * a["invw"] + l1 * b["invw"] + l2 * c["invw"]
            if denom == 0.0:
                continue
            col = [(l0 * a["color"][k] * a["invw"] + l1 * b["color"][k] * b["invw"]
                    + l2 * c["color"][k] * c["invw"]) / denom for k in range(4)]
            t = None
            if texture is not None:
                u = (l0 * a["uv"][0] * a["invw"] + l1 * b["uv"][0] * b["invw"]
                     + l2 * c["uv"][0] * c["invw"]) / denom
                v = (l0 * a["uv"][1] * a["invw"] + l1 * b["uv"][1] * b["invw"]
                     + l2 * c["uv"][1] * c["invw"]) / denom
                t = texture.sample(u, v)
            if shader is not None:
                col = list(shader(tuple(col), t))
            elif t is not None:
                col = [col[k] * t[k] for k in range(4)]          # MODULATE: vertex * texel
            if blend_mode is not None:
                col = gx_blend(blend_mode, tuple(col), fb.color[y][x])
            elif blend:
                dst = fb.color[y][x]
                al = col[3]
                col = [col[k] * al + dst[k] * (1.0 - al) for k in range(3)] + [1.0]
                col = tuple(col)
            else:
                col = tuple(col)
            fb.color[y][x] = col
            if depth_test and depth_write:
                fb.depth[y][x] = z
            written += 1
    return written
