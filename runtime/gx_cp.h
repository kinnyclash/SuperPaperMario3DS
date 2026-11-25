/* GX command processor in C: turns a captured FIFO stream (runtime/hw.c) into clip-space
 * triangles that a 3DS renderer can draw (platform/3ds/source/gfx/gx_scene.c).
 *
 * This is a port of the hardware-mode path of spm3ds/gx/cp.py, and host tests check that the
 * two produce the same triangles (tests/test_gx_cp_c.py). Register layouts come from libogc
 * gx.c; see spm3ds/gx/regs.py for the field-by-field citations.
 *
 * Handled: CP, XF and BP writes (with the BP mask register), display-list calls, direct and
 * 8/16-bit indexed vertex attributes, position matrices by current or per-vertex index, the
 * XF projection, quads/triangles/strips/fans, and cull mode. Output coordinates are GX clip
 * space (z from -w at the near plane to 0 at the far plane, the same range citro3d uses).
 * Materials: triangles are grouped into batches. A batch is untextured (vertex colour), or
 * uses one texture map either alone or multiplied by the vertex colour, decided from TEV
 * stage 0 (texture order and colour inputs); other TEV setups are approximated by the
 * multiplied form and counted in `unsupported`.
 * Not handled: lighting, texgen, multi-stage TEV, lines, points, and the GX viewport (the
 * renderer maps the whole frame to the screen).
 * MIT License. */
#ifndef SPM3DS_GX_CP_H
#define SPM3DS_GX_CP_H

#include <stddef.h>
#include <stdint.h>

typedef struct {
    float x, y, z, w;     /* clip space */
    float r, g, b, a;     /* 0..1 */
    float u, v;           /* texture coordinate 0 */
} gx_vertex_out;

typedef struct {
    uint32_t first, count;     /* vertex range in gx_cp.out */
    int textured;              /* 0: vertex colour only */
    int modulate;              /* textured: 1 = texture x vertex colour, 0 = texture only */
    uint32_t tex_addr;         /* physical address of the texture data */
    unsigned tex_fmt, tex_w, tex_h;
    unsigned wrap_s, wrap_t;   /* GX_CLAMP 0, GX_REPEAT 1, GX_MIRROR 2 */
} gx_batch;

/* Reads n bytes of guest memory at a physical address into dst; returns 0 on failure. */
typedef int (*gx_read_fn)(void *user, uint32_t phys, uint8_t *dst, uint32_t n);

typedef struct gx_cp {
    uint32_t cp[256];
    uint32_t bp[256];
    uint8_t bp_set[256];
    uint32_t bp_mask;
    float xf_mem[0x1000];
    uint32_t xf_regs[0x100];   /* XF registers 0x1000..0x10FF */
    uint8_t xf_set[0x100];
    gx_read_fn read;
    void *read_user;
    gx_vertex_out *out;        /* 3 vertices per triangle */
    uint32_t out_count;        /* vertices */
    uint32_t out_cap;
    gx_batch *batches;
    uint32_t batch_count;
    uint32_t batch_cap;
    uint32_t commands;
    uint32_t unsupported;      /* commands or attributes that were skipped */
    int error;                 /* nonzero after an unknown command; see message */
    char message[96];
} gx_cp;

void gx_cp_init(gx_cp *g, gx_read_fn read, void *user);
void gx_cp_free(gx_cp *g);
/* Drops the triangles produced so far (start of a new frame); register state is kept. */
void gx_cp_clear_output(gx_cp *g);
/* Processes complete commands from data. Returns the number of bytes consumed; a command cut
 * off at the end of the buffer is left for the next call. */
size_t gx_cp_run(gx_cp *g, const uint8_t *data, size_t n);

#endif /* SPM3DS_GX_CP_H */
