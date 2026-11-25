/* PICA200 texture tiling index math (pure C, no libctru dependency so host tests can build it).
 *
 * Layout used: texels are grouped into 8x8 tiles. Tiles are stored row by row across the
 * texture. Inside a tile, texels follow a Morton (Z-order) curve: bit i of x goes to bit 2i and
 * bit i of y goes to bit 2i+1.
 *
 * UNVERIFIED ON HARDWARE: the tile arrangement follows libctru's y2r.h description. The
 * x/y interleave order was not confirmed against a device. If the texture looks scrambled on
 * the console, check this file first.
 *
 * Byte order of GPU_RGBA8 texels (TILING_RGBA8_ABGR): citro3d renders straight into RGBA8
 * textures (C3D_RenderTargetCreateFromTex), so a texel has the colour buffer's layout, whose
 * clear colour is the 32-bit value 0xRRGGBBAA stored little-endian: bytes A, B, G, R.
 * Set TILING_RGBA8_ABGR to 0 if red and blue (or alpha) look swapped on the console.
 *
 * Row order (TILING_FLIP_ROWS): source row 0 is stored in tile row 0. Set it to 1 if GX
 * textures appear upside down on the console.
 * MIT License. */
#ifndef SPM3DS_GFX_TILING_H
#define SPM3DS_GFX_TILING_H

#include <stdint.h>

/* Byte offset of texel (x, y) in tiled storage. width must be a multiple of 8, bpp in bytes. */
uint32_t tiling_offset(uint32_t width, uint32_t x, uint32_t y, uint32_t bpp);

/* Morton index of an (x, y) pair inside one 8x8 tile, 0..63. */
uint32_t tiling_morton8(uint32_t x, uint32_t y);

#ifndef TILING_RGBA8_ABGR
#  define TILING_RGBA8_ABGR 1
#endif
#ifndef TILING_FLIP_ROWS
#  define TILING_FLIP_ROWS 0
#endif

/* Stores one RGBA8 texel in the PICA byte order (see above). */
void tiling_store_rgba8(uint8_t *px, uint8_t r, uint8_t g, uint8_t b, uint8_t a);

/* Fills a tiled RGBA8 buffer (width*height*4 bytes) with a checkerboard of 8-texel squares
 * with a red/green gradient. */
void tiling_fill_checker_rgba8(uint8_t *dst, uint32_t width, uint32_t height);

/* Smallest power of two >= n, clamped to the PICA texture size range 8..1024. */
uint32_t tiling_pow2(uint32_t n);

/* Converts a linear image (src_w x src_h texels of R, G, B, A, top row first) to a tiled
 * dst_w x dst_h RGBA8 texture. dst sizes must be multiples of 8 and at least the source
 * size; texels beyond the source repeat its last column and row. */
void tiling_linear_to_tiled_rgba8(uint8_t *dst, uint32_t dst_w, uint32_t dst_h,
                                  const uint8_t *src, uint32_t src_w, uint32_t src_h);

#endif /* SPM3DS_GFX_TILING_H */
