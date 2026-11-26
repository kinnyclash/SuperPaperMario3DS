"""Make the icon (48x48) and banner (256x128) PNGs and a short silent WAV for a CIA.

The pictures are drawn in code (a coloured gradient with a simple block letter), so the CIA
contains no art. Usage:
    python3 tools/cia/make_assets.py OUT_DIR LETTER RRGGBB
writes OUT_DIR/icon.png, OUT_DIR/banner.png and OUT_DIR/banner.wav.
"""
import os
import struct
import sys
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from spm3ds.gx.png import encode_png  # noqa: E402

# 5x7 block letters for the app markers.
GLYPHS = {
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"],
    "G": ["01111", "10000", "10000", "10011", "10001", "10001", "01111"],
    "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
}


def picture(width, height, letter, rgb):
    r0, g0, b0 = rgb
    glyph = GLYPHS[letter]
    cell = max(1, min(width // 8, height // 10))
    gx0 = (width - 5 * cell) // 2
    gy0 = (height - 7 * cell) // 2
    rows = []
    for y in range(height):
        row = []
        for x in range(width):
            t = (x + y) / float(width + height)
            px = (int(r0 * (1 - 0.5 * t)), int(g0 * (1 - 0.5 * t)), int(b0 * (1 - 0.5 * t)), 255)
            gx, gy = (x - gx0) // cell, (y - gy0) // cell
            if 0 <= gx < 5 and 0 <= gy < 7 and x >= gx0 and y >= gy0 and glyph[gy][gx] == "1":
                px = (255, 255, 255, 255)
            row.append(px)
        rows.append(row)
    return rows


def write_png(path, rows):
    flat = bytes(c for row in rows for px in row for c in px)
    with open(path, "wb") as f:
        f.write(encode_png(len(rows[0]), len(rows), flat))


def main(argv):
    out, letter, colour = argv[1], argv[2], argv[3]
    rgb = tuple(int(colour[i:i + 2], 16) for i in (0, 2, 4))
    os.makedirs(out, exist_ok=True)
    write_png(os.path.join(out, "icon.png"), picture(48, 48, letter, rgb))
    write_png(os.path.join(out, "banner.png"), picture(256, 128, letter, rgb))
    with wave.open(os.path.join(out, "banner.wav"), "wb") as w:   # 0.5 s of silence
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(struct.pack("<h", 0) * 11025)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
