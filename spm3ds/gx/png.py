"""Minimal PNG writer (RGBA, 8 bits per channel) using only the standard library."""
import struct
import zlib


def _chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def encode_png(width, height, rgba):
    """rgba: bytes of length width*height*4, row-major, top row first."""
    if len(rgba) != width * height * 4:
        raise ValueError("pixel buffer size does not match width and height")
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                       # filter type 0 (none) for every row
        raw += rgba[y * stride:(y + 1) * stride]
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)   # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _chunk(b"IEND", b""))


def framebuffer_to_rgba(fb):
    out = bytearray()
    for row in fb.color:
        for r, g, b, a in row:
            out += bytes((round(max(0.0, min(1.0, v)) * 255) for v in (r, g, b, a)))
    return bytes(out)
