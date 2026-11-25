/* PICA200 texture tiling index math (pure C, no libctru dependency so host tests can build it).
 *
 * Layout used: texels are grouped into 8x8 tiles. Tiles are stored row by row across the
 * texture. Inside a tile, texels follow a Morton (Z-order) curve: bit i of x goes to bit 2i and
 * bit i of y goes to bit 2i+1.
 *
 * UNVERIFIED ON HARDWARE: the tile arrangement follows libctru's y2r.h description. The
 * x/y interleave order was not confirmed against a device. If the texture looks scrambled on
 * the console, check this file first.
 * MIT License. */
#ifndef SPM3DS_GFX_TILING_H
#define SPM3DS_GFX_TILING_H

#include <stdint.h>

/* Byte offset of texel (x, y) in tiled storage. width must be a multiple of 8, bpp in bytes. */
uint32_t tiling_offset(uint32_t width, uint32_t x, uint32_t y, uint32_t bpp);

/* Morton index of an (x, y) pair inside one 8x8 tile, 0..63. */
uint32_t tiling_morton8(uint32_t x, uint32_t y);

/* Fills a tiled RGBA8 buffer (width*height*4 bytes) with a checkerboard of 8-texel squares
 * with a red/green gradient. The bytes are stored R, G, B, A per texel. */
void tiling_fill_checker_rgba8(uint8_t *dst, uint32_t width, uint32_t height);

#endif /* SPM3DS_GFX_TILING_H */
