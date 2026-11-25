/* PICA200 texture tiling index math. See tiling.h. MIT License. */
#include "tiling.h"

uint32_t tiling_morton8(uint32_t x, uint32_t y) {
    uint32_t m = 0;
    for (uint32_t i = 0; i < 3; i++) {
        m |= ((x >> i) & 1u) << (2u * i);
        m |= ((y >> i) & 1u) << (2u * i + 1u);
    }
    return m;
}

uint32_t tiling_offset(uint32_t width, uint32_t x, uint32_t y, uint32_t bpp) {
    uint32_t tiles_per_row = width >> 3;
    uint32_t tile = (y >> 3) * tiles_per_row + (x >> 3);
    uint32_t inside = tiling_morton8(x & 7u, y & 7u);
    return (tile * 64u + inside) * bpp;
}

void tiling_store_rgba8(uint8_t *px, uint8_t r, uint8_t g, uint8_t b, uint8_t a) {
#if TILING_RGBA8_ABGR
    px[0] = a; px[1] = b; px[2] = g; px[3] = r;
#else
    px[0] = r; px[1] = g; px[2] = b; px[3] = a;
#endif
}

void tiling_fill_checker_rgba8(uint8_t *dst, uint32_t width, uint32_t height) {
    for (uint32_t y = 0; y < height; y++) {
        for (uint32_t x = 0; x < width; x++) {
            uint32_t check = ((x >> 3) ^ (y >> 3)) & 1u;
            tiling_store_rgba8(dst + tiling_offset(width, x, y, 4u),
                               (uint8_t)(check ? 255u : (x * 255u) / (width - 1u)),
                               (uint8_t)(check ? 64u : (y * 255u) / (height - 1u)),
                               (uint8_t)(check ? 32u : 128u), 255u);
        }
    }
}

uint32_t tiling_pow2(uint32_t n) {
    uint32_t p = 8;
    while (p < n && p < 1024u) p <<= 1;
    return p;
}

void tiling_linear_to_tiled_rgba8(uint8_t *dst, uint32_t dst_w, uint32_t dst_h,
                                  const uint8_t *src, uint32_t src_w, uint32_t src_h) {
    for (uint32_t y = 0; y < dst_h; y++) {
        uint32_t sy = y < src_h ? y : src_h - 1u;
        uint32_t ty = TILING_FLIP_ROWS ? dst_h - 1u - y : y;
        for (uint32_t x = 0; x < dst_w; x++) {
            uint32_t sx = x < src_w ? x : src_w - 1u;
            const uint8_t *s = src + 4u * (sy * src_w + sx);
            tiling_store_rgba8(dst + tiling_offset(dst_w, x, ty, 4u), s[0], s[1], s[2], s[3]);
        }
    }
}
