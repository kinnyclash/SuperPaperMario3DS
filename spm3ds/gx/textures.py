"""GameCube/Wii GX texture decoders (host-side reference, no PICA200 code here).

Each format is stored in tiles. Within a tile, pixels are in row-major order, except RGBA8 and
CMPR, which have their own layouts described at the decoder. Pixels come out as (r, g, b, a)
with 8 bits per channel. Images whose size is not a multiple of the tile size are padded in the
data and cropped on output.

Tile sizes and layouts follow the public GX texture format descriptions (YAGCD / Dolphin source
notes). They are written from those notes, not from Nintendo documents. Verify them against
your own references before depending on them for shipped output.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Format:
    name: str
    tile_w: int
    tile_h: int
    tile_bytes: int


FORMATS = {
    "I4": Format("I4", 8, 8, 32),
    "I8": Format("I8", 8, 4, 32),
    "IA4": Format("IA4", 8, 4, 32),
    "IA8": Format("IA8", 4, 4, 32),
    "RGB565": Format("RGB565", 4, 4, 32),
    "RGB5A3": Format("RGB5A3", 4, 4, 32),
    "RGBA8": Format("RGBA8", 4, 4, 64),
    "CMPR": Format("CMPR", 8, 8, 32),
}


def _expand5(v):
    return (v << 3) | (v >> 2)


def _expand6(v):
    return (v << 2) | (v >> 4)


def _rgb565(v):
    return (_expand5(v >> 11 & 31), _expand6(v >> 5 & 63), _expand5(v & 31), 255)


def _rgb5a3(v):
    if v & 0x8000:
        return (_expand5(v >> 10 & 31), _expand5(v >> 5 & 31), _expand5(v & 31), 255)
    a = (v >> 12 & 7) * 255 // 7
    return (v >> 8 & 15) * 17, (v >> 4 & 15) * 17, (v & 15) * 17, a


def _cmpr_colors(c0, c1):
    """The four colours of a CMPR block. c0 > c1 gives two interpolated colours; otherwise the
    third is the average and the fourth is transparent."""
    p0, p1 = _rgb565(c0), _rgb565(c1)
    if c0 > c1:
        p2 = tuple((2 * a + b) // 3 for a, b in zip(p0[:3], p1[:3])) + (255,)
        p3 = tuple((a + 2 * b) // 3 for a, b in zip(p0[:3], p1[:3])) + (255,)
    else:
        p2 = tuple((a + b) // 2 for a, b in zip(p0[:3], p1[:3])) + (255,)
        p3 = (0, 0, 0, 0)
    return [p0, p1, p2, p3]


def decode(fmt_name, data, width, height):
    """Decode `data` into rows of (r, g, b, a) tuples, width x height after cropping."""
    fmt = FORMATS[fmt_name]
    tw, th = fmt.tile_w, fmt.tile_h
    pad_w = -(-width // tw) * tw
    pad_h = -(-height // th) * th
    tiles_x, tiles_y = pad_w // tw, pad_h // th
    need = tiles_x * tiles_y * fmt.tile_bytes
    if len(data) < need:
        raise ValueError(f"{fmt_name} {width}x{height} needs {need} bytes, got {len(data)}")
    canvas = [[None] * pad_w for _ in range(pad_h)]
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            base = (ty * tiles_x + tx) * fmt.tile_bytes
            tile = data[base:base + fmt.tile_bytes]
            for y in range(th):
                for x in range(tw):
                    canvas[ty * th + y][tx * tw + x] = _pixel(fmt_name, tile, x, y, tw, th)
    return [row[:width] for row in canvas[:height]]


def _pixel(name, tile, x, y, tw, th):
    i = y * tw + x
    if name == "I4":
        v = tile[i // 2] >> (4 if i % 2 == 0 else 0) & 15
        return (v * 17, v * 17, v * 17, 255)
    if name == "I8":
        v = tile[i]
        return (v, v, v, 255)
    if name == "IA4":
        v = tile[i]
        return ((v & 15) * 17, (v & 15) * 17, (v & 15) * 17, (v >> 4) * 17)
    if name == "IA8":
        a, v = tile[2 * i], tile[2 * i + 1]
        return (v, v, v, a)
    if name == "RGB565":
        return _rgb565(int.from_bytes(tile[2 * i:2 * i + 2], "big"))
    if name == "RGB5A3":
        return _rgb5a3(int.from_bytes(tile[2 * i:2 * i + 2], "big"))
    if name == "RGBA8":
        # First 32 bytes: (A, R) pairs for the 16 pixels. Next 32 bytes: (G, B) pairs.
        a, r = tile[2 * i], tile[2 * i + 1]
        g, b = tile[32 + 2 * i], tile[32 + 2 * i + 1]
        return (r, g, b, a)
    if name == "CMPR":
        # Four 4x4 blocks in the 8x8 tile: top-left, top-right, bottom-left, bottom-right.
        bx, by = (x // 4), (y // 4)
        block = (by * 2 + bx) * 8
        c0 = int.from_bytes(tile[block:block + 2], "big")
        c1 = int.from_bytes(tile[block + 2:block + 4], "big")
        px, py = x % 4, y % 4
        bits = tile[block + 4 + py]
        index = (bits >> (6 - 2 * px)) & 3
        return _cmpr_colors(c0, c1)[index]
    raise KeyError(name)
