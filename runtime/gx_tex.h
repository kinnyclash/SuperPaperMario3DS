/* GX texture decoding in C (port of spm3ds/gx/textures.py; tests/test_gx_tex_c.py checks that
 * both give the same texels). Formats use their GX_TF_* numbers: 0 I4, 1 I8, 2 IA4, 3 IA8,
 * 4 RGB565, 5 RGB5A3, 6 RGBA8, 14 CMPR. Palette formats (CI4, CI8, CI14) are not supported.
 * The tile layouts follow the public GX texture format descriptions (YAGCD, Dolphin notes).
 * MIT License. */
#ifndef SPM3DS_GX_TEX_H
#define SPM3DS_GX_TEX_H

#include <stdint.h>

/* Bytes of texture data for a width x height image, or 0 for an unsupported format. */
uint32_t gx_tex_size(unsigned fmt, unsigned width, unsigned height);
/* Decodes into dst: width * height texels of R, G, B, A bytes, top row first.
 * Returns 1 on success, 0 for an unsupported format. */
int gx_tex_decode(unsigned fmt, const uint8_t *src, unsigned width, unsigned height,
                  uint8_t *dst);

#endif /* SPM3DS_GX_TEX_H */
