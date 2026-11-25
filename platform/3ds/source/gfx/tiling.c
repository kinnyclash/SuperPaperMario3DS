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

void tiling_fill_checker_rgba8(uint8_t *dst, uint32_t width, uint32_t height) {
    for (uint32_t y = 0; y < height; y++) {
        for (uint32_t x = 0; x < width; x++) {
            uint32_t check = ((x >> 3) ^ (y >> 3)) & 1u;
            uint8_t *px = dst + tiling_offset(width, x, y, 4u);
            px[0] = (uint8_t)(check ? 255u : (x * 255u) / (width - 1u)); /* R */
            px[1] = (uint8_t)(check ? 64u : (y * 255u) / (height - 1u)); /* G */
            px[2] = (uint8_t)(check ? 32u : 128u);                       /* B */
            px[3] = 255u;                                                /* A */
        }
    }
}
