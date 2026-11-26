/* GX renderer for translated code: draws what guest code sent to the GX FIFO.
 *
 * Translated code stores GX commands to the write-gather pipe; runtime/hw.c captures them;
 * gx_scene_present() decodes them with runtime/gx_cp.c and draws the triangles with citro3d,
 * uploading the textures they use. The graphics demo (gfx/gx_demo.c) and the game app
 * (game/game_main.c) both use it. The chain is checked on the host by
 * tests/test_gx_demo.py. MIT License. */
#ifndef SPM3DS_GX_SCENE_H
#define SPM3DS_GX_SCENE_H

#include <3ds.h>
#include <citro3d.h>

#include "gx_cp.h"
#include "hw.h"

typedef struct {
    float pos[4];   /* clip space, tilted for the 3DS top screen */
    float clr[4];
    float tc[2];    /* GX texture coordinate, scaled for power-of-two padding */
} gx_scene_vertex;

#define GX_SCENE_TEXTURES 32

typedef struct {
    int used;
    uint32_t addr, signature;
    unsigned fmt, w, h, wrap_s, wrap_t;
    float scale_u, scale_v;   /* source size / padded size */
    C3D_Tex tex;
} gx_scene_texture;

typedef struct {
    shaderProgram_s program;
    DVLB_s *dvlb;
    C3D_AttrInfo attr;
    C3D_BufInfo buf;
    gx_scene_vertex *vbo;
    ppc_ctx *ctx;      /* the guest context whose memory holds textures and arrays */
    spm3ds_hw *hw;     /* where its FIFO bytes are captured */
    gx_cp *cp;
    gx_scene_texture textures[GX_SCENE_TEXTURES];
    u32 texture_uploads; /* textures decoded and uploaded so far */
    u32 triangles;     /* drawn in the last frame */
    u32 fifo_bytes;    /* captured in the last frame */
    int error;         /* command processor error flag from the last frame */
    u32 vertices;      /* prepared for the next gx_scene_draw */
    gx_scene_texture **bound; /* texture per batch, prepared for the next gx_scene_draw */
    int external_frame; /* 1: the caller begins and ends the citro3d frame and clears */
} gx_scene;

/* ctx and hw belong to the caller and must outlive the scene. 0 on success. */
int gx_scene_create(gx_scene *s, ppc_ctx *ctx, spm3ds_hw *hw);
/* Decodes the captured FIFO and draws one frame on target. A command cut off at the end
 * of the FIFO is kept for the next frame. */
void gx_scene_present(gx_scene *s, C3D_RenderTarget *target);
/* present() in two halves: prepare decodes the FIFO and fills the vertex buffer and textures
 * (CPU only, no GPU commands); draw submits them (inside its own frame unless
 * external_frame is set). The diagnostic app checks the vertices between the two. */
void gx_scene_prepare(gx_scene *s);
void gx_scene_draw(gx_scene *s, C3D_RenderTarget *target);
void gx_scene_destroy(gx_scene *s);

#endif /* SPM3DS_GX_SCENE_H */
