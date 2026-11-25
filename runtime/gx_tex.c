/* GX texture decoding in C. See gx_tex.h. MIT License. */
#include "gx_tex.h"

typedef struct { unsigned tw, th, tile_bytes; } tex_format;

static int format_of(unsigned fmt, tex_format *f) {
    switch (fmt) {
    case 0: f->tw = 8; f->th = 8; f->tile_bytes = 32; return 1;   /* I4 */
    case 1: f->tw = 8; f->th = 4; f->tile_bytes = 32; return 1;   /* I8 */
    case 2: f->tw = 8; f->th = 4; f->tile_bytes = 32; return 1;   /* IA4 */
    case 3: f->tw = 4; f->th = 4; f->tile_bytes = 32; return 1;   /* IA8 */
    case 4: f->tw = 4; f->th = 4; f->tile_bytes = 32; return 1;   /* RGB565 */
    case 5: f->tw = 4; f->th = 4; f->tile_bytes = 32; return 1;   /* RGB5A3 */
    case 6: f->tw = 4; f->th = 4; f->tile_bytes = 64; return 1;   /* RGBA8 */
    case 14: f->tw = 8; f->th = 8; f->tile_bytes = 32; return 1;  /* CMPR */
    default: return 0;
    }
}

uint32_t gx_tex_size(unsigned fmt, unsigned width, unsigned height) {
    tex_format f;
    if (!format_of(fmt, &f) || !width || !height) return 0;
    return ((width + f.tw - 1) / f.tw) * ((height + f.th - 1) / f.th) * f.tile_bytes;
}

static unsigned x5(unsigned v) { return (v << 3) | (v >> 2); }
static unsigned x6(unsigned v) { return (v << 2) | (v >> 4); }

static void rgb565(unsigned v, uint8_t *p) {
    p[0] = (uint8_t)x5((v >> 11) & 31u);
    p[1] = (uint8_t)x6((v >> 5) & 63u);
    p[2] = (uint8_t)x5(v & 31u);
    p[3] = 255;
}

static void rgb5a3(unsigned v, uint8_t *p) {
    if (v & 0x8000u) {
        p[0] = (uint8_t)x5((v >> 10) & 31u);
        p[1] = (uint8_t)x5((v >> 5) & 31u);
        p[2] = (uint8_t)x5(v & 31u);
        p[3] = 255;
    } else {
        p[0] = (uint8_t)(((v >> 8) & 15u) * 17u);
        p[1] = (uint8_t)(((v >> 4) & 15u) * 17u);
        p[2] = (uint8_t)((v & 15u) * 17u);
        p[3] = (uint8_t)(((v >> 12) & 7u) * 255u / 7u);
    }
}

static void cmpr_pixel(const uint8_t *tile, unsigned x, unsigned y, uint8_t *p) {
    unsigned block = ((y / 4u) * 2u + (x / 4u)) * 8u;
    unsigned c0 = ((unsigned)tile[block] << 8) | tile[block + 1];
    unsigned c1 = ((unsigned)tile[block + 2] << 8) | tile[block + 3];
    unsigned index = (tile[block + 4 + (y % 4u)] >> (6u - 2u * (x % 4u))) & 3u;
    uint8_t p0[4], p1[4];
    unsigned k;
    rgb565(c0, p0);
    rgb565(c1, p1);
    switch (index) {
    case 0: for (k = 0; k < 4; k++) p[k] = p0[k]; break;
    case 1: for (k = 0; k < 4; k++) p[k] = p1[k]; break;
    case 2:
        for (k = 0; k < 3; k++)
            p[k] = (uint8_t)(c0 > c1 ? (2u * p0[k] + p1[k]) / 3u : (p0[k] + p1[k]) / 2u);
        p[3] = 255;
        break;
    default:
        if (c0 > c1) {
            for (k = 0; k < 3; k++) p[k] = (uint8_t)((p0[k] + 2u * p1[k]) / 3u);
            p[3] = 255;
        } else {
            p[0] = p[1] = p[2] = p[3] = 0;
        }
        break;
    }
}

static void texel(unsigned fmt, const uint8_t *tile, unsigned x, unsigned y, unsigned tw,
                  uint8_t *p) {
    unsigned i = y * tw + x, v;
    switch (fmt) {
    case 0:
        v = (tile[i / 2u] >> ((i % 2u) ? 0u : 4u)) & 15u;
        p[0] = p[1] = p[2] = (uint8_t)(v * 17u);
        p[3] = 255;
        break;
    case 1:
        p[0] = p[1] = p[2] = tile[i];
        p[3] = 255;
        break;
    case 2:
        v = tile[i];
        p[0] = p[1] = p[2] = (uint8_t)((v & 15u) * 17u);
        p[3] = (uint8_t)((v >> 4) * 17u);
        break;
    case 3:
        p[0] = p[1] = p[2] = tile[2u * i + 1u];
        p[3] = tile[2u * i];
        break;
    case 4:
        rgb565(((unsigned)tile[2u * i] << 8) | tile[2u * i + 1u], p);
        break;
    case 5:
        rgb5a3(((unsigned)tile[2u * i] << 8) | tile[2u * i + 1u], p);
        break;
    case 6: /* first 32 bytes: (A, R) pairs; next 32 bytes: (G, B) pairs */
        p[3] = tile[2u * i];
        p[0] = tile[2u * i + 1u];
        p[1] = tile[32u + 2u * i];
        p[2] = tile[32u + 2u * i + 1u];
        break;
    default:
        cmpr_pixel(tile, x, y, p);
        break;
    }
}

int gx_tex_decode(unsigned fmt, const uint8_t *src, unsigned width, unsigned height,
                  uint8_t *dst) {
    tex_format f;
    unsigned tiles_x, tx, ty, x, y;
    if (!format_of(fmt, &f)) return 0;
    tiles_x = (width + f.tw - 1u) / f.tw;
    for (ty = 0; ty * f.th < height; ty++) {
        for (tx = 0; tx < tiles_x; tx++) {
            const uint8_t *tile = src + (ty * tiles_x + tx) * f.tile_bytes;
            for (y = 0; y < f.th; y++) {
                unsigned py = ty * f.th + y;
                if (py >= height) break;
                for (x = 0; x < f.tw; x++) {
                    unsigned px = tx * f.tw + x;
                    if (px >= width) break;
                    texel(fmt, tile, x, y, f.tw, dst + 4u * (py * width + px));
                }
            }
        }
    }
    return 1;
}
