/* Translated-GX scene. See gx_scene.h.
 *
 * UNVERIFIED ON HARDWARE. citro3d and libctru names follow docs/API_VERIFICATION.md.
 * Assumptions to check on the console: GX clip-space depth (-w near, 0 far) matches the
 * PICA200 range citro3d documents in Mtx_OrthoTilt ("fixed depth range of [-1,0]"), and the
 * screen tilt below (x' = y, y' = -x) matches Mtx_OrthoTilt's rotation. MIT License. */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "gx_scene.h"
#include "gxpass_shbin.h"

#define MAX_VERTS (3u * 4096u)

void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx);

int gx_scene_create(gx_scene *s) {
    memset(s, 0, sizeof *s);
    s->dvlb = DVLB_ParseFile((u32 *)gxpass_shbin, gxpass_shbin_size);
    if (!s->dvlb) return -1;
    if (R_FAILED(shaderProgramInit(&s->program))) return -2;
    if (R_FAILED(shaderProgramSetVsh(&s->program, &s->dvlb->DVLE[0]))) return -3;

    s->vbo = (gx_scene_vertex *)linearAlloc(sizeof(gx_scene_vertex) * MAX_VERTS);
    if (!s->vbo) return -4;
    AttrInfo_Init(&s->attr);
    AttrInfo_AddLoader(&s->attr, 0, GPU_FLOAT, 4); /* v0 position */
    AttrInfo_AddLoader(&s->attr, 1, GPU_FLOAT, 4); /* v1 colour */
    BufInfo_Init(&s->buf);
    BufInfo_Add(&s->buf, s->vbo, sizeof(gx_scene_vertex), 2, 0x10);

    /* Guest state: the demo program only uses registers and the hardware pipe, so the
     * context needs no memory regions. */
    s->ctx = (ppc_ctx *)calloc(1, sizeof *s->ctx);
    s->hw = (spm3ds_hw *)malloc(sizeof *s->hw);
    s->cp = (gx_cp *)malloc(sizeof *s->cp);
    if (!s->ctx || !s->hw || !s->cp) return -5;
    spm3ds_hw_init(s->hw);
    spm3ds_hw_attach(s->ctx, s->hw);
    gx_cp_init(s->cp, NULL, NULL);
    return 0;
}

void gx_scene_frame(gx_scene *s, C3D_RenderTarget *target, float angle_y, float angle_x) {
    u32 i, n;
    C3D_TexEnv *env;

    /* 1. Run one frame of translated guest code; it writes GX commands to the pipe. */
    spm3ds_hw_fifo_clear(s->hw);
    spm3ds_gx_demo_frame(s->ctx, cosf(angle_y), sinf(angle_y), cosf(angle_x), sinf(angle_x));
    s->fifo_bytes = (u32)s->hw->fifo_size;

    /* 2. Decode the commands into clip-space triangles. */
    gx_cp_clear_output(s->cp);
    gx_cp_run(s->cp, s->hw->fifo, s->hw->fifo_size);
    s->error = s->cp->error;
    n = s->cp->out_count;
    if (n > MAX_VERTS) n = MAX_VERTS - (MAX_VERTS % 3u);

    /* 3. Copy into the GPU vertex buffer, applying the top-screen tilt. */
    for (i = 0; i < n; i++) {
        const gx_vertex_out *v = &s->cp->out[i];
        gx_scene_vertex *o = &s->vbo[i];
        o->pos[0] = v->y;
        o->pos[1] = -v->x;
        o->pos[2] = v->z;
        o->pos[3] = v->w;
        o->clr[0] = v->r;
        o->clr[1] = v->g;
        o->clr[2] = v->b;
        o->clr[3] = v->a;
    }
    GSPGPU_FlushDataCache(s->vbo, n * sizeof(gx_scene_vertex));
    s->triangles = n / 3u;

    /* 4. Draw with citro3d: vertex colour only, depth test as in the citro3d examples. */
    C3D_BindProgram(&s->program);
    C3D_SetAttrInfo(&s->attr);
    C3D_SetBufInfo(&s->buf);
    env = C3D_GetTexEnv(0);
    C3D_TexEnvInit(env);
    C3D_TexEnvSrc(env, C3D_Both, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
    C3D_TexEnvFunc(env, C3D_Both, GPU_REPLACE);
    C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
    C3D_CullFace(GPU_CULL_NONE);

    C3D_FrameBegin(C3D_FRAME_SYNCDRAW);
    C3D_FrameDrawOn(target);
    C3D_RenderTargetClear(target, C3D_CLEAR_ALL, 0x202028FFu, 0);
    if (n) C3D_DrawArrays(GPU_TRIANGLES, 0, (int)n);
    C3D_FrameEnd(0);
}

void gx_scene_destroy(gx_scene *s) {
    if (s->cp) {
        gx_cp_free(s->cp);
        free(s->cp);
    }
    if (s->hw) {
        spm3ds_hw_free(s->hw);
        free(s->hw);
    }
    free(s->ctx);
    if (s->vbo) linearFree(s->vbo);
    shaderProgramFree(&s->program);
    if (s->dvlb) DVLB_Free(s->dvlb);
    memset(s, 0, sizeof *s);
}
