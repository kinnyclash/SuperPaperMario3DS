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
#include "gx_tex.h"
#include "gxpass_shbin.h"
#include "tiling.h"

#define MAX_VERTS (3u * 4096u)

/* Physical addresses (as GX registers hold them) to guest reads: MEM1 0x0..., MEM2 0x1...
 * are mapped at 0x80000000 + physical, as on the Wii. */
static int read_guest(void *user, uint32_t phys, uint8_t *dst, uint32_t n) {
    ppc_ctx *c = (ppc_ctx *)user;
    uint32_t done = 0;
    while (done < n) {
        uint32_t ea = (phys + done) | 0x80000000u;
        uint32_t chunk = n - done, room = PPC_PAGE_SIZE - (ea & (PPC_PAGE_SIZE - 1u));
        uint8_t *p;
        if (chunk > room) chunk = room;
        p = ppc_try(c, ea, chunk);
        if (!p) return 0;
        memcpy(dst + done, p, chunk);
        done += chunk;
    }
    return 1;
}

static uint32_t signature(const uint8_t *data, uint32_t n) {
    /* FNV-1a over the first and last 256 bytes: cheap change detection for reused memory. */
    uint32_t h = 2166136261u, i, k = n < 256u ? n : 256u;
    for (i = 0; i < k; i++) h = (h ^ data[i]) * 16777619u;
    for (i = n - k; i < n; i++) h = (h ^ data[i]) * 16777619u;
    return h;
}

static GPU_TEXTURE_WRAP_PARAM pica_wrap(unsigned gx) {
    return gx == 1 ? GPU_REPEAT : (gx == 2 ? GPU_MIRRORED_REPEAT : GPU_CLAMP_TO_EDGE);
}

/* Finds or uploads the texture of a batch. Returns NULL if it cannot be read or decoded. */
static gx_scene_texture *scene_texture(gx_scene *s, const gx_batch *b) {
    uint32_t size = gx_tex_size(b->tex_fmt, b->tex_w, b->tex_h), sig, pw, ph;
    uint8_t *raw, *linear, *tiled;
    gx_scene_texture *t = NULL;
    int i, free_slot = -1;
    if (!size || b->tex_w > 1024u || b->tex_h > 1024u) return NULL;
    raw = (uint8_t *)malloc(size);
    if (!raw) return NULL;
    if (!read_guest(s->ctx, b->tex_addr, raw, size)) {
        free(raw);
        return NULL;
    }
    sig = signature(raw, size);
    for (i = 0; i < GX_SCENE_TEXTURES; i++) {
        gx_scene_texture *e = &s->textures[i];
        if (!e->used) {
            if (free_slot < 0) free_slot = i;
            continue;
        }
        if (e->addr == b->tex_addr && e->fmt == b->tex_fmt && e->w == b->tex_w &&
            e->h == b->tex_h && e->wrap_s == b->wrap_s && e->wrap_t == b->wrap_t) {
            if (e->signature == sig) {
                free(raw);
                return e;
            }
            C3D_TexDelete(&e->tex);      /* same place, new contents: upload again */
            e->used = 0;
            free_slot = i;
            break;
        }
    }
    if (free_slot < 0) {                 /* cache full: drop everything (simple policy) */
        for (i = 0; i < GX_SCENE_TEXTURES; i++) {
            if (s->textures[i].used) C3D_TexDelete(&s->textures[i].tex);
            s->textures[i].used = 0;
        }
        free_slot = 0;
    }
    t = &s->textures[free_slot];
    pw = tiling_pow2(b->tex_w);
    ph = tiling_pow2(b->tex_h);
    linear = (uint8_t *)malloc(4u * b->tex_w * b->tex_h);
    tiled = (uint8_t *)malloc(4u * pw * ph);
    if (!linear || !tiled || !gx_tex_decode(b->tex_fmt, raw, b->tex_w, b->tex_h, linear) ||
        !C3D_TexInit(&t->tex, (u16)pw, (u16)ph, GPU_RGBA8)) {
        free(raw);
        free(linear);
        free(tiled);
        return NULL;
    }
    tiling_linear_to_tiled_rgba8(tiled, pw, ph, linear, b->tex_w, b->tex_h);
    C3D_TexLoadImage(&t->tex, tiled, GPU_TEXFACE_2D, 0);
    C3D_TexSetFilter(&t->tex, GPU_LINEAR, GPU_LINEAR);
    C3D_TexSetWrap(&t->tex, pica_wrap(b->wrap_s), pica_wrap(b->wrap_t));
    free(raw);
    free(linear);
    free(tiled);
    t->used = 1;
    t->addr = b->tex_addr;
    t->signature = sig;
    t->fmt = b->tex_fmt;
    t->w = b->tex_w;
    t->h = b->tex_h;
    t->wrap_s = b->wrap_s;
    t->wrap_t = b->wrap_t;
    t->scale_u = (float)b->tex_w / (float)pw;
    t->scale_v = (float)b->tex_h / (float)ph;
    s->texture_uploads++;
    return t;
}

int gx_scene_create(gx_scene *s, ppc_ctx *ctx, spm3ds_hw *hw) {
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
    AttrInfo_AddLoader(&s->attr, 2, GPU_FLOAT, 2); /* v2 texture coordinate */
    BufInfo_Init(&s->buf);
    BufInfo_Add(&s->buf, s->vbo, sizeof(gx_scene_vertex), 3, 0x210);

    s->ctx = ctx;
    s->hw = hw;
    s->cp = (gx_cp *)malloc(sizeof *s->cp);
    if (!s->cp) return -5;
    gx_cp_init(s->cp, read_guest, s->ctx);
    return 0;
}

void gx_scene_present(gx_scene *s, C3D_RenderTarget *target) {
    gx_scene_prepare(s);
    gx_scene_draw(s, target);
}

void gx_scene_prepare(gx_scene *s) {
    u32 i, n;
    size_t used;
    gx_scene_texture **bound;

    /* 1-2. Decode what the guest sent since the last frame into clip-space triangles. */
    s->fifo_bytes = (u32)s->hw->fifo_size;
    gx_cp_clear_output(s->cp);
    used = gx_cp_run(s->cp, s->hw->fifo, s->hw->fifo_size);
    if (used < s->hw->fifo_size && !s->cp->error) {
        memmove(s->hw->fifo, s->hw->fifo + used, s->hw->fifo_size - used);
        s->hw->fifo_size -= used;
    } else {
        spm3ds_hw_fifo_clear(s->hw);
    }
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
        o->tc[0] = v->u;
        o->tc[1] = v->v;
    }
    s->triangles = n / 3u;

    /* 3b. Textures: upload what the batches use, and scale their coordinates for the
     * power-of-two padding. Done before the frame starts so uploads do not split it. */
    bound = (gx_scene_texture **)calloc(s->cp->batch_count ? s->cp->batch_count : 1,
                                        sizeof *bound);
    {
        u32 bi;
        for (bi = 0; bi < s->cp->batch_count; bi++) {
            const gx_batch *b = &s->cp->batches[bi];
            gx_scene_texture *t = (b->textured && bound) ? scene_texture(s, b) : NULL;
            u32 k, end = b->first + b->count;
            if (bound) bound[bi] = t;
            if (!t) continue;
            for (k = b->first; k < end && k < n; k++) {
                s->vbo[k].tc[0] *= t->scale_u;
                s->vbo[k].tc[1] *= t->scale_v;
            }
        }
        GSPGPU_FlushDataCache(s->vbo, n * sizeof(gx_scene_vertex));
    }
    free(s->bound);
    s->bound = bound;
    s->vertices = n;
}

void gx_scene_draw(gx_scene *s, C3D_RenderTarget *target) {
    u32 n = s->vertices;
    C3D_TexEnv *env;
    gx_scene_texture **bound = s->bound;

    /* 4. Draw with citro3d, one call per batch; depth test as in the citro3d examples. */
    C3D_BindProgram(&s->program);
    C3D_SetAttrInfo(&s->attr);
    C3D_SetBufInfo(&s->buf);
    C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
    C3D_CullFace(GPU_CULL_NONE);

    if (!s->external_frame) {
        C3D_FrameBegin(C3D_FRAME_SYNCDRAW);
        C3D_FrameDrawOn(target);
        C3D_RenderTargetClear(target, C3D_CLEAR_ALL, 0x202028FFu, 0);
    }
    {
        u32 bi;
        for (bi = 0; bi < s->cp->batch_count; bi++) {
            const gx_batch *b = &s->cp->batches[bi];
            gx_scene_texture *t = bound ? bound[bi] : NULL;
            u32 count = b->count;
            if (b->first >= n) break;
            if (b->first + count > n) count = n - b->first;
            env = C3D_GetTexEnv(0);
            C3D_TexEnvInit(env);
            if (t) {
                C3D_TexBind(0, &t->tex);
                C3D_TexEnvSrc(env, C3D_Both, GPU_TEXTURE0, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
                C3D_TexEnvFunc(env, C3D_Both, b->modulate ? GPU_MODULATE : GPU_REPLACE);
            } else {
                C3D_TexEnvSrc(env, C3D_Both, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR,
                              GPU_PRIMARY_COLOR);
                C3D_TexEnvFunc(env, C3D_Both, GPU_REPLACE);
            }
            if (count) C3D_DrawArrays(GPU_TRIANGLES, (int)b->first, (int)count);
        }
    }
    if (!s->external_frame) C3D_FrameEnd(0);
}

void gx_scene_destroy(gx_scene *s) {
    int i;
    for (i = 0; i < GX_SCENE_TEXTURES; i++)
        if (s->textures[i].used) C3D_TexDelete(&s->textures[i].tex);
    if (s->cp) {
        gx_cp_free(s->cp);
        free(s->cp);
    }
    free(s->bound);
    if (s->vbo) linearFree(s->vbo);
    shaderProgramFree(&s->program);
    if (s->dvlb) DVLB_Free(s->dvlb);
    memset(s, 0, sizeof *s);
}
