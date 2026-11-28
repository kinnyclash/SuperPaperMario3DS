/* spm3ds graphics diagnostic, round 2 (APP=gfxdiag).
 *
 * Round 1 on a New 2DS XL: clear-only frames worked, then the GPU never finished the first
 * textured-cube frame (GPU TIMEOUT). This round adds one GPU feature per stage, so the first
 * stage that times out names the feature that hangs:
 *
 *   1 clear        framebuffer clear only, no shader draw
 *   1b clear-block the same clears with the blocking frame pattern (C3D_FrameBegin(0), GX_CMDLIST_FLUSH)
 *   2 imm-tri      one white triangle, minimal shader, immediate mode (no vertex buffer)
 *   3 vbo-tri      the same triangle from a vertex buffer (1 attribute, stride 12)
 *   4 vbo-rebase   vbo-tri with the buffer base address moved next to the buffer
 *   5 attribs      two attributes from one buffer (position + colour, stride 28)
 *   6 depth        two overlapping triangles with the depth test on
 *   7 texture      a textured quad (texture in linear memory, cache flushed)
 *   8 tex-vram     the same quad with the texture in VRAM
 *   9 cube         the full cube from round 1 (texture, depth, perspective, 36 vertices)
 *   10 gx          the cube drawn by translated PowerPC code through GX
 *
 * Before each operation a numbered checkpoint goes to the bottom screen and to
 * sdmc:/spm3ds/gfxdiag.log (closed after every line, so it survives a power-off). Every
 * frame starts non-blocking with a 2 s timeout. After a timeout the GPU is treated as hung:
 * no later stage runs and nothing it might still read is freed. Resources are freed only
 * after a successful non-blocking frame start has shown that the GPU queue is empty.
 * Buffer and texture addresses (virtual, physical, GPU offset) are printed for each stage.
 *
 * Menu: UP/DOWN choose a stage, A runs from it to the end, X runs only it, Y toggles step
 * mode (press A before each operation), START exits. MIT License. */
#include <3ds.h>
#include <citro3d.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "col_shbin.h"
#include "cube_shbin.h"
#include "gx_demo.h"
#include "tiling.h"
#include "tri_shbin.h"

#define LOG_PATH "sdmc:/spm3ds/gfxdiag.log"
#define TRANSFER_FLAGS \
    (GX_TRANSFER_FLIP_VERT(0) | GX_TRANSFER_OUT_TILED(0) | GX_TRANSFER_RAW_COPY(0) | \
     GX_TRANSFER_IN_FORMAT(GX_TRANSFER_FMT_RGBA8) | GX_TRANSFER_OUT_FORMAT(GX_TRANSFER_FMT_RGB8) | \
     GX_TRANSFER_SCALING(GX_TRANSFER_SCALE_NO))
#define BUFFER_BASE 0x18000000u      /* citro3d's BUFFER_BASE_PADDR */
#define FRAMES 60

void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx);

/* ---- checkpoints, log, watchdog -------------------------------------------- */

static int console_ready, step_mode, gpu_hung, failures;
static volatile int cp_number;
static volatile u64 cp_time;
static char cp_text[96];
static volatile int watchdog_run = 1;

static void log_line(const char *line) {
    FILE *f = fopen(LOG_PATH, "a");
    if (!f) return;
    fputs(line, f);
    fputc('\n', f);
    fclose(f);
}

static void show_bottom(void) {
    u16 w, h;
    u8 *fb;
    if (!console_ready) return;
    fb = gfxGetFramebuffer(GFX_BOTTOM, GFX_LEFT, &w, &h);
    svcFlushProcessDataCache(CUR_PROCESS_HANDLE, (u32)fb,
                             (u32)w * h * gspGetBytesPerPixel(gfxGetScreenFormat(GFX_BOTTOM)));
}

static void out(const char *colour, const char *line) {
    log_line(line);
    if (console_ready) {
        printf("%s%s\x1b[0m\n", colour, line);
        show_bottom();
    }
}

static void cp(const char *fmt, ...) {
    char line[128];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(cp_text, sizeof cp_text, fmt, ap);
    va_end(ap);
    cp_number++;
    snprintf(line, sizeof line, "[%02d] %s", cp_number, cp_text);
    out("", line);
    cp_time = osGetTime();
    if (console_ready && step_mode) {
        printf("     A: run it\n");
        show_bottom();
        for (;;) {
            hidScanInput();
            if (hidKeysDown() & KEY_A) break;
            gspWaitForVBlank();
        }
    } else if (console_ready) {
        gspWaitForVBlank();
        gspWaitForVBlank();
    }
    cp_time = osGetTime();
}

static void note(const char *fmt, ...) {
    char line[128], text[110];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    snprintf(line, sizeof line, "     %s", text);
    out("", line);
}

static void fail(const char *fmt, ...) {
    char line[128], text[110];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    failures++;
    snprintf(line, sizeof line, " !!! %s", text);
    out("\x1b[31;1m", line);
}

static void watchdog(void *arg) {
    int seen = -1, reported = -1;
    (void)arg;
    while (watchdog_run) {
        int now = cp_number;
        svcSleepThread(250000000LL);
        if (step_mode || now != seen) {
            seen = now;
            continue;
        }
        if (reported != now && osGetTime() - cp_time > 3000u) {
            char line[140];
            reported = now;
            snprintf(line, sizeof line, "STALL: [%02d] %s has not returned after 3 s", now,
                     cp_text);
            out("\x1b[31;1m", line);
        }
    }
}

/* ---- GPU helpers ------------------------------------------------------------ */

static C3D_RenderTarget *target;
static C3D_Mtx ortho;

/* Starts a frame; on a 2 s timeout marks the GPU hung and returns 0. */
static int frame_begin(int verbose) {
    u64 start = osGetTime();
    if (gpu_hung) return 0;
    if (verbose) cp("C3D_FrameBegin (non-blocking, 2 s timeout)");
    while (!C3D_FrameBegin(C3D_FRAME_NONBLOCK)) {
        if (osGetTime() - start > 2000u) {
            gpu_hung = 1;
            fail("GPU TIMEOUT: previous frame never finished");
            return 0;
        }
        svcSleepThread(1000000LL);
    }
    return 1;
}

/* Waits until the GPU queue is empty. While it returns 1 the caller is inside a frame with
 * an idle GPU: free resources, then call C3D_FrameEnd. */
static int gpu_idle_begin(void) {
    cp("wait for GPU idle before freeing");
    return frame_begin(0);
}

typedef void (*draw_fn)(void *user, int frame, int verbose);

/* Runs FRAMES frames: begin, draw on the top target, clear, draw_fn, end. */
static int run_frames(draw_fn draw, void *user, u32 clear) {
    int f;
    for (f = 0; f < FRAMES; f++) {
        int verbose = f < 2;
        if (!frame_begin(verbose)) return -1;
        if (verbose) cp("FrameDrawOn + clear");
        C3D_FrameDrawOn(target);
        C3D_RenderTargetClear(target, C3D_CLEAR_ALL, clear, 0);
        if (draw) draw(user, f, verbose);
        if (verbose) cp("C3D_FrameEnd");
        C3D_FrameEnd(0);
        cp_time = osGetTime();
    }
    /* The last frame is only known to be finished once the next one could start. */
    if (!frame_begin(1)) return -1;
    C3D_FrameEnd(0);
    note("%d frames finished, GPU %.2f ms/frame", FRAMES, (double)C3D_GetDrawingTime());
    return 0;
}

typedef struct {
    DVLB_s *dvlb;
    shaderProgram_s prog;
    int ok;
} shader;

static int shader_load(shader *s, const char *name, const u8 *bin, u32 size) {
    memset(s, 0, sizeof *s);
    cp("load shader %s (%lu bytes)", name, (unsigned long)size);
    if (((u32)bin & 3u) != 0) fail("shader binary not 4-byte aligned");
    s->dvlb = DVLB_ParseFile((u32 *)bin, size);
    if (!s->dvlb) { fail("DVLB_ParseFile returned NULL"); return -1; }
    if (R_FAILED(shaderProgramInit(&s->prog))) { fail("shaderProgramInit"); return -1; }
    if (R_FAILED(shaderProgramSetVsh(&s->prog, &s->dvlb->DVLE[0]))) { fail("SetVsh"); return -1; }
    note("DVLEs %lu, output table %lu, outmap mask %02x", (unsigned long)s->dvlb->numDVLE,
         (unsigned long)s->dvlb->DVLE[0].outTableSize, (unsigned)s->dvlb->DVLE[0].outmapMask);
    cp("C3D_BindProgram %s", name);
    C3D_BindProgram(&s->prog);
    s->ok = 1;
    return 0;
}

/* A bound program must outlive its binding: C3D_BindProgram reads the program that is bound
 * when the next one is bound. The round-2 crash (data abort in C3D_BindProgram, FAR 0) was
 * the vbo-tri stage binding over a freed imm-tri program. So each stage's program is freed
 * only after a long-lived "keep" program has been bound in its place. */
static shader keep_shader;

static void shader_free(shader *s) {
    if (keep_shader.ok) C3D_BindProgram(&keep_shader.prog);
    if (s->ok) shaderProgramFree(&s->prog);
    if (s->dvlb) DVLB_Free(s->dvlb);
    memset(s, 0, sizeof *s);
}

static s8 uniform(shader *s, const char *name) {
    s8 u = shaderInstanceGetUniformLocation(s->prog.vertexShader, name);
    note("uniform %s = %d", name, u);
    if (u < 0) fail("uniform %s missing", name);
    return u;
}

/* Allocates a GPU-readable copy of data and reports where it lives. */
static void *vbo_make(const void *data, u32 size) {
    void *v;
    u32 pa;
    cp("linearAlloc + fill + flush vertex buffer (%lu bytes)", (unsigned long)size);
    v = linearAlloc(size);
    if (!v) { fail("linearAlloc"); return NULL; }
    memcpy(v, data, size);
    GSPGPU_FlushDataCache(v, size);
    pa = osConvertVirtToPhys(v);
    note("vbo virt %08lx phys %08lx offset %08lx", (unsigned long)(u32)v, (unsigned long)pa,
         (unsigned long)(pa - BUFFER_BASE));
    if (pa == 0) fail("no physical address for the vertex buffer");
    else if (pa < BUFFER_BASE || pa - BUFFER_BASE >= 0x10000000u)
        fail("vertex buffer outside the 28-bit GPU offset range");
    return v;
}

static int rebase_next;   /* 1: the next buffers() call puts base_paddr next to the buffer */

static int buffers(const void *vbo, u32 stride, int attribs, u64 perm) {
    C3D_BufInfo *b = C3D_GetBufInfo();
    int rc;
    BufInfo_Init(b);
    if (rebase_next) {
        rebase_next = 0;
        b->base_paddr = osConvertVirtToPhys(vbo) & ~0xFFu;
        note("base_paddr moved to %08lx (offset now small)", (unsigned long)b->base_paddr);
    }
    rc = BufInfo_Add(b, vbo, (ptrdiff_t)stride, attribs, perm);
    note("BufInfo_Add stride %lu attribs %d perm %llx -> %d", (unsigned long)stride, attribs,
         (unsigned long long)perm, rc);
    if (rc < 0) { fail("BufInfo_Add failed"); return -1; }
    return 0;
}

static void env_colour(void) {
    C3D_TexEnv *env = C3D_GetTexEnv(0);
    C3D_TexEnvInit(env);
    C3D_TexEnvSrc(env, C3D_Both, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
    C3D_TexEnvFunc(env, C3D_Both, GPU_REPLACE);
}

static void env_texture(void) {
    C3D_TexEnv *env = C3D_GetTexEnv(0);
    C3D_TexEnvInit(env);
    C3D_TexEnvSrc(env, C3D_Both, GPU_TEXTURE0, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
    C3D_TexEnvFunc(env, C3D_Both, GPU_MODULATE);
}

/* State every stage starts from, so stages do not depend on each other. */
static void base_state(void) {
    C3D_DepthTest(false, GPU_ALWAYS, GPU_WRITE_ALL);
    C3D_CullFace(GPU_CULL_NONE);
    C3D_TexBind(0, NULL);
    env_colour();
}

/* ---- stages ------------------------------------------------------------------ */

typedef struct { float x, y, z; } v3;
typedef struct { float pos[3]; float clr[4]; } vcol;
typedef struct { float pos[3]; float tc[2]; } vtex;

static const v3 tri_verts[3] = {{200.0f, 200.0f, 0.5f}, {100.0f, 40.0f, 0.5f},
                                {300.0f, 40.0f, 0.5f}};

static int stage_clear(void) {
    return run_frames(NULL, NULL, 0x3030C0FFu);   /* top screen: plain blue */
}

/* Same clears, but the frame pattern is the blocking one used by common 3DS renderers:
 * C3D_FrameBegin(0) waits until the GPU queue is idle, and C3D_FrameEnd(GX_CMDLIST_FLUSH)
 * flushes the command list at the end of each frame. This isolates whether the
 * non-blocking start (or the lack of a flush) is what stalls the GPU. Written from the
 * public citro3d API, not copied from any port. */
static int stage_clear_blocking(void) {
    int f;
    note("-- blocking frame pattern: C3D_FrameBegin(0) / C3D_FrameEnd(GX_CMDLIST_FLUSH) --");
    for (f = 0; f < FRAMES; f++) {
        int verbose = f < 2;
        if (verbose) cp("C3D_FrameBegin(0) (blocking)");
        if (!C3D_FrameBegin(0)) { fail("C3D_FrameBegin(0) returned false"); return -1; }
        C3D_FrameDrawOn(target);
        C3D_RenderTargetClear(target, C3D_CLEAR_ALL, 0x30C030FFu, 0);
        if (verbose) cp("C3D_FrameEnd(GX_CMDLIST_FLUSH)");
        C3D_FrameEnd(GX_CMDLIST_FLUSH);
        cp_time = osGetTime();
    }
    note("%d blocking frames finished (expect: green top screen)", FRAMES);
    return 0;
}

typedef struct { s8 u_proj; } tri_ctx;

static void draw_imm(void *user, int f, int verbose) {
    tri_ctx *t = (tri_ctx *)user;
    int i;
    (void)f;
    if (verbose) cp("uniform + C3D_ImmDrawBegin/SendAttrib/End (3 vertices)");
    C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, t->u_proj, &ortho);
    C3D_ImmDrawBegin(GPU_TRIANGLES);
    for (i = 0; i < 3; i++) C3D_ImmSendAttrib(tri_verts[i].x, tri_verts[i].y, tri_verts[i].z, 1.0f);
    C3D_ImmDrawEnd();
}

static int stage_imm_tri(void) {
    shader s;
    tri_ctx t;
    C3D_AttrInfo *a;
    int rc;
    if (shader_load(&s, "tri", tri_shbin, tri_shbin_size) != 0) return -1;
    if ((t.u_proj = uniform(&s, "projection")) < 0) return -1;
    cp("AttrInfo: 1 loader float x3; no buffers");
    a = C3D_GetAttrInfo();
    AttrInfo_Init(a);
    AttrInfo_AddLoader(a, 0, GPU_FLOAT, 3);
    BufInfo_Init(C3D_GetBufInfo());
    base_state();
    rc = run_frames(draw_imm, &t, 0x202020FFu);
    if (!gpu_hung && gpu_idle_begin()) {
        shader_free(&s);
        C3D_FrameEnd(0);
    }
    if (rc == 0) note("expect: white triangle on dark grey");
    return rc;
}

typedef struct { s8 u_proj; int count; } vbo_ctx;

static void draw_vbo(void *user, int f, int verbose) {
    vbo_ctx *t = (vbo_ctx *)user;
    (void)f;
    if (verbose) cp("uniform + C3D_DrawArrays (%d vertices)", t->count);
    C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, t->u_proj, &ortho);
    C3D_DrawArrays(GPU_TRIANGLES, 0, t->count);
}

static int stage_vbo_tri(void) {
    shader s;
    vbo_ctx t = {0, 3};
    C3D_AttrInfo *a;
    void *vbo;
    int rc;
    if (shader_load(&s, "tri", tri_shbin, tri_shbin_size) != 0) return -1;
    if ((t.u_proj = uniform(&s, "projection")) < 0) return -1;
    if (!(vbo = vbo_make(tri_verts, sizeof tri_verts))) return -1;
    cp("AttrInfo: 1 loader float x3");
    a = C3D_GetAttrInfo();
    AttrInfo_Init(a);
    AttrInfo_AddLoader(a, 0, GPU_FLOAT, 3);
    if (buffers(vbo, sizeof(v3), 1, 0x0) != 0) return -1;
    base_state();
    rc = run_frames(draw_vbo, &t, 0x202020FFu);
    if (!gpu_hung && gpu_idle_begin()) {
        BufInfo_Init(C3D_GetBufInfo());
        linearFree(vbo);
        shader_free(&s);
        C3D_FrameEnd(0);
    }
    if (rc == 0) note("expect: white triangle on dark grey");
    return rc;
}

/* vbo-tri with citro3d's attribute buffer base moved next to the buffer: if vbo-tri hangs
 * and this passes, the GPU offset from 0x18000000 is the problem. */
static int stage_vbo_rebase(void) {
    rebase_next = 1;
    return stage_vbo_tri();
}

static int colour_stage(int depth) {
    static const vcol verts[6] = {
        {{200.0f, 200.0f, 0.3f}, {1, 0, 0, 1}}, {{100.0f, 40.0f, 0.3f}, {0, 1, 0, 1}},
        {{300.0f, 40.0f, 0.3f}, {0, 0, 1, 1}},
        /* second, overlapping triangle (yellow), at another depth */
        {{150.0f, 220.0f, 0.7f}, {1, 1, 0, 1}}, {{250.0f, 220.0f, 0.7f}, {1, 1, 0, 1}},
        {{200.0f, 20.0f, 0.7f}, {1, 1, 0, 1}}};
    shader s;
    vbo_ctx t = {0, depth ? 6 : 3};
    C3D_AttrInfo *a;
    void *vbo;
    int rc;
    if (shader_load(&s, "col", col_shbin, col_shbin_size) != 0) return -1;
    if ((t.u_proj = uniform(&s, "projection")) < 0) return -1;
    if (!(vbo = vbo_make(verts, sizeof verts))) return -1;
    cp("AttrInfo: float x3 position, float x4 colour");
    a = C3D_GetAttrInfo();
    AttrInfo_Init(a);
    AttrInfo_AddLoader(a, 0, GPU_FLOAT, 3);
    AttrInfo_AddLoader(a, 1, GPU_FLOAT, 4);
    if (buffers(vbo, sizeof(vcol), 2, 0x10) != 0) return -1;
    base_state();
    if (depth) {
        cp("C3D_DepthTest(on, GREATER, write all)");
        C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
    }
    rc = run_frames(draw_vbo, &t, 0x202020FFu);
    if (!gpu_hung && gpu_idle_begin()) {
        BufInfo_Init(C3D_GetBufInfo());
        linearFree(vbo);
        shader_free(&s);
        C3D_DepthTest(false, GPU_ALWAYS, GPU_WRITE_ALL);
        C3D_FrameEnd(0);
    }
    if (rc == 0)
        note(depth ? "expect: RGB triangle and a yellow one, one hiding part of the other"
                   : "expect: triangle shaded red/green/blue");
    return rc;
}

static int stage_attribs(void) { return colour_stage(0); }
static int stage_depth(void) { return colour_stage(1); }

typedef struct { s8 u_proj, u_mv; int count; C3D_Mtx *proj; float spin; } tex_ctx;

static void draw_tex(void *user, int f, int verbose) {
    tex_ctx *t = (tex_ctx *)user;
    C3D_Mtx mv;
    Mtx_Identity(&mv);
    if (t->spin != 0.0f) {
        float a = t->spin * (float)f;
        Mtx_Translate(&mv, 0.0f, 0.0f, -2.5f, true);
        Mtx_RotateX(&mv, a * 0.7f, true);
        Mtx_RotateY(&mv, a, true);
    }
    if (verbose) cp("uniforms + C3D_DrawArrays (%d vertices)", t->count);
    C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, t->u_proj, t->proj);
    C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, t->u_mv, &mv);
    C3D_DrawArrays(GPU_TRIANGLES, 0, t->count);
}

static void build_cube(vtex *v) {
    static const float cu[4] = {-1.0f, 1.0f, 1.0f, -1.0f}, cv[4] = {-1.0f, -1.0f, 1.0f, 1.0f};
    static const float tu[4] = {0.0f, 1.0f, 1.0f, 0.0f}, tv[4] = {1.0f, 1.0f, 0.0f, 0.0f};
    static const unsigned idx[6] = {0, 1, 2, 0, 2, 3};
    unsigned n = 0, axis, k;
    int s;
    for (axis = 0; axis < 3; axis++)
        for (s = -1; s <= 1; s += 2) {
            unsigned ua = (axis + 1) % 3, va = (axis + 2) % 3;
            for (k = 0; k < 6; k++) {
                unsigned c = idx[k];
                vtex *g = &v[n++];
                g->pos[axis] = 0.5f * (float)s;
                g->pos[ua] = 0.5f * cu[c];
                g->pos[va] = 0.5f * cv[c];
                g->tc[0] = tu[c];
                g->tc[1] = tv[c];
            }
        }
}

/* mode 0: quad, linear texture; 1: quad, VRAM texture; 2: full cube, linear texture. */
static int textured_stage(int mode) {
    static const vtex quad[6] = {
        {{120.0f, 40.0f, 0.5f}, {0, 0}}, {{280.0f, 40.0f, 0.5f}, {1, 0}},
        {{280.0f, 200.0f, 0.5f}, {1, 1}}, {{120.0f, 40.0f, 0.5f}, {0, 0}},
        {{280.0f, 200.0f, 0.5f}, {1, 1}}, {{120.0f, 200.0f, 0.5f}, {0, 1}}};
    vtex cube[36];
    shader s;
    tex_ctx t;
    C3D_Tex tex;
    C3D_Mtx persp;
    C3D_AttrInfo *a;
    uint8_t *pixels;
    void *vbo;
    int rc, vram = mode == 1, is_cube = mode == 2;
    u32 tpa;

    if (shader_load(&s, "cube", cube_shbin, cube_shbin_size) != 0) return -1;
    t.u_proj = uniform(&s, "projection");
    t.u_mv = uniform(&s, "modelView");
    if (t.u_proj < 0 || t.u_mv < 0) return -1;

    cp("%s 64x64 RGBA8", vram ? "C3D_TexInitVRAM" : "C3D_TexInit");
    if (!(vram ? C3D_TexInitVRAM(&tex, 64, 64, GPU_RGBA8) : C3D_TexInit(&tex, 64, 64, GPU_RGBA8))) {
        fail("texture init failed");
        return -1;
    }
    tpa = osConvertVirtToPhys(tex.data);
    note("texture virt %08lx phys %08lx size %lu", (unsigned long)(u32)tex.data,
         (unsigned long)tpa, (unsigned long)tex.size);
    if (tpa == 0) fail("no physical address for the texture");
    cp("fill checker in a linear buffer, C3D_TexLoadImage%s", vram ? " (DMA to VRAM)" : "");
    pixels = (uint8_t *)linearAlloc(64u * 64u * 4u);   /* linear: a DMA source for VRAM */
    if (!pixels) { fail("linearAlloc pixels"); return -1; }
    tiling_fill_checker_rgba8(pixels, 64, 64);
    GSPGPU_FlushDataCache(pixels, 64u * 64u * 4u);
    C3D_TexLoadImage(&tex, pixels, GPU_TEXFACE_2D, 0);
    cp("C3D_TexFlush + C3D_TexSetFilter");
    C3D_TexFlush(&tex);
    C3D_TexSetFilter(&tex, GPU_LINEAR, GPU_NEAREST);

    if (is_cube) build_cube(cube);
    if (!(vbo = is_cube ? vbo_make(cube, sizeof cube) : vbo_make(quad, sizeof quad))) return -1;
    cp("AttrInfo: float x3 position, float x2 texcoord");
    a = C3D_GetAttrInfo();
    AttrInfo_Init(a);
    AttrInfo_AddLoader(a, 0, GPU_FLOAT, 3);
    AttrInfo_AddLoader(a, 1, GPU_FLOAT, 2);
    if (buffers(vbo, sizeof(vtex), 2, 0x10) != 0) return -1;
    base_state();
    cp("C3D_TexBind(0) + texenv modulate%s", is_cube ? " + depth test" : "");
    C3D_TexBind(0, &tex);
    env_texture();
    if (is_cube) {
        C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
        Mtx_PerspTilt(&persp, C3D_AngleFromDegrees(80.0f), C3D_AspectRatioTop, 0.01f, 1000.0f,
                      false);
    }
    t.proj = is_cube ? &persp : &ortho;
    t.count = is_cube ? 36 : 6;
    t.spin = is_cube ? 0.03f : 0.0f;
    rc = run_frames(draw_tex, &t, 0x68B0D8FFu);
    if (!gpu_hung && gpu_idle_begin()) {
        C3D_TexBind(0, NULL);
        BufInfo_Init(C3D_GetBufInfo());
        C3D_TexDelete(&tex);
        linearFree(pixels);
        linearFree(vbo);
        shader_free(&s);
        base_state();
        C3D_FrameEnd(0);
    }
    if (rc == 0) note(is_cube ? "expect: spinning checkered cube" : "expect: checkered square");
    return rc;
}

static int stage_texture(void) { return textured_stage(0); }
static int stage_tex_vram(void) { return textured_stage(1); }
static int stage_cube(void) { return textured_stage(2); }

static int check_vertices(const gx_scene *s) {
    u32 i, bad = 0;
    for (i = 0; i < s->vertices; i++) {
        const gx_scene_vertex *v = &s->vbo[i];
        int k, b = 0;
        for (k = 0; k < 4; k++)
            if (!isfinite(v->pos[k]) || !isfinite(v->clr[k]) || fabsf(v->pos[k]) > 1.0e6f) b = 1;
        if (!isfinite(v->tc[0]) || !isfinite(v->tc[1]) || v->pos[3] <= 0.0f) b = 1;
        if (b && bad++ == 0)
            note("first bad vertex %lu: %g %g %g %g", (unsigned long)i, (double)v->pos[0],
                 (double)v->pos[1], (double)v->pos[2], (double)v->pos[3]);
    }
    note("vertices %lu, batches %lu, bad %lu", (unsigned long)s->vertices,
         (unsigned long)s->cp->batch_count, (unsigned long)bad);
    return (int)bad;
}

static int stage_gx(void) {
    gx_demo d;
    int rc = 0, f;
    u32 pa;
    cp("gx_demo_create (guest context, GX shader, vbo)");
    if (gx_demo_create(&d) != 0) { fail("gx_demo_create"); return -1; }
    pa = osConvertVirtToPhys(d.scene.vbo);
    note("GX vbo virt %08lx phys %08lx", (unsigned long)(u32)d.scene.vbo, (unsigned long)pa);
    base_state();
    for (f = 0; f < FRAMES && rc == 0; f++) {
        int verbose = f < 2;
        float a = 0.02f * (float)f;
        if (verbose) cp("translated guest frame (CPU only)");
        spm3ds_hw_fifo_clear(d.hw);
        spm3ds_gx_demo_frame(d.ctx, cosf(a), sinf(a), cosf(a * 0.7f), sinf(a * 0.7f));
        if (!frame_begin(verbose)) { rc = -1; break; }
        if (verbose) cp("gx_scene_prepare (decode, textures) with the GPU idle");
        gx_scene_prepare(&d.scene);
        if (verbose && check_vertices(&d.scene) != 0) {
            fail("bad vertices: not drawing them");
            C3D_FrameEnd(0);
            rc = -1;
            break;
        }
        C3D_FrameDrawOn(target);
        C3D_RenderTargetClear(target, C3D_CLEAR_ALL, 0x202028FFu, 0);
        if (verbose) cp("gx_scene_draw (%lu triangles)", (unsigned long)d.scene.triangles);
        gx_scene_draw(&d.scene, target);
        if (verbose) cp("C3D_FrameEnd");
        C3D_FrameEnd(0);
        cp_time = osGetTime();
    }
    if (rc == 0 && frame_begin(1)) {
        C3D_FrameEnd(0);
        note("%d GX frames finished (expect: spinning cube)", FRAMES);
    } else if (rc == 0) {
        rc = -1;
    }
    if (!gpu_hung && gpu_idle_begin()) {
        gx_demo_destroy(&d);
        base_state();
        C3D_FrameEnd(0);
    }
    return rc;
}

typedef struct { const char *name; int (*run)(void); } stage;
static const stage stages[] = {
    {"clear", stage_clear},     {"clear-block", stage_clear_blocking}, {"imm-tri", stage_imm_tri}, {"vbo-tri", stage_vbo_tri},
    {"vbo-rebase", stage_vbo_rebase}, {"attribs", stage_attribs}, {"depth", stage_depth},     {"texture", stage_texture},
    {"tex-vram", stage_tex_vram}, {"cube", stage_cube},     {"gx", stage_gx},
};
#define NSTAGES ((int)(sizeof stages / sizeof stages[0]))
static int results[NSTAGES];   /* 0 not run, 1 pass, -1 fail */

static void menu_draw(int sel) {
    int i;
    printf("\x1b[2J\x1b[1;1H\x1b[1mspm3ds graphics diagnostic 2\x1b[0m\n");
    printf("UP/DOWN stage  A: run from it  X: only it\nY: step mode (%s)  START: exit\n\n",
           step_mode ? "on" : "off");
    for (i = 0; i < NSTAGES; i++)
        printf("%s %d %-10s %s\n", i == sel ? ">" : " ", i + 1, stages[i].name,
               results[i] == 1 ? "\x1b[32mpass\x1b[0m"
                               : results[i] == -1 ? "\x1b[31mFAIL\x1b[0m" : "");
    if (gpu_hung)
        printf("\n\x1b[31;1mGPU hung: no more GPU stages can run.\n"
               "Exit, and power off if exit freezes.\x1b[0m\n");
    show_bottom();
}

static void run_stages(int first, int last) {
    int i;
    char line[96];
    printf("\x1b[2J\x1b[1;1H");
    for (i = first; i <= last && !gpu_hung; i++) {
        int before = failures, rc;
        snprintf(line, sizeof line, "== stage %d: %s ==", i + 1, stages[i].name);
        out("\x1b[1m", line);
        rc = stages[i].run();
        results[i] = (rc == 0 && failures == before) ? 1 : -1;
        snprintf(line, sizeof line, "== stage %d %s: %s ==", i + 1, stages[i].name,
                 results[i] == 1 ? "PASS" : "FAIL");
        out(results[i] == 1 ? "\x1b[32;1m" : "\x1b[31;1m", line);
        if (results[i] != 1) break;
    }
    printf("press B for the menu\n");
    show_bottom();
    while (aptMainLoop()) {
        hidScanInput();
        if (hidKeysDown() & KEY_B) break;
        gspWaitForVBlank();
        cp_time = osGetTime();
    }
}

int main(void) {
    Thread dog;
    bool is_new = false;
    u8 model = 0;
    s32 main_prio = 0x30, dog_prio;
    int sel = 0;
    char line[128];

    mkdir("sdmc:/spm3ds", 0777);
    remove(LOG_PATH);
    log_line("spm3ds gfxdiag 2: graphics checkpoints (last line = where it stopped)");
    APT_CheckNew3DS(&is_new);
    if (R_SUCCEEDED(cfguInit())) {
        CFGU_GetSystemModel(&model);
        cfguExit();
    }
    svcGetThreadPriority(&main_prio, CUR_THREAD_HANDLE);
    dog_prio = main_prio - 1 < 0x18 ? 0x18 : main_prio - 1;
    snprintf(line, sizeof line, "new3ds=%d model=%u prio=%ld/%ld", is_new, (unsigned)model,
             (long)main_prio, (long)dog_prio);
    log_line(line);
    dog = threadCreate(watchdog, NULL, 8 * 1024, dog_prio, -2, true);

    cp("gfxInitDefault");
    gfxInitDefault();
    cp("consoleInit(bottom)");
    consoleInit(GFX_BOTTOM, NULL);
    consoleDebugInit(debugDevice_CONSOLE);
    console_ready = 1;
    {
        void *probe = linearAlloc(64);
        note("linear free %luK, probe virt %08lx phys %08lx",
             (unsigned long)(linearSpaceFree() >> 10), (unsigned long)(u32)probe,
             (unsigned long)osConvertVirtToPhys(probe));
        linearFree(probe);
    }
    cp("C3D_Init");
    if (!C3D_Init(C3D_DEFAULT_CMDBUF_SIZE)) {
        fail("C3D_Init returned false");
        gpu_hung = 1;
    } else {
        cp("keep-alive shader program (stays bound between stages)");
        if (shader_load(&keep_shader, "keep", tri_shbin, tri_shbin_size) == 0) {
            C3D_BindProgram(&keep_shader.prog);
            keep_shader.ok = 1;
        }
        cp("render target 240x400 RGBA8 + D24S8, output top");
        target = C3D_RenderTargetCreate(240, 400, GPU_RB_RGBA8, GPU_RB_DEPTH24_STENCIL8);
        if (!target) {
            fail("render target creation failed");
            gpu_hung = 1;
        } else {
            C3D_RenderTargetSetOutput(target, GFX_TOP, GFX_LEFT, TRANSFER_FLAGS);
            note("colour buf phys %08lx",
                 (unsigned long)osConvertVirtToPhys(target->frameBuf.colorBuf));
        }
        Mtx_OrthoTilt(&ortho, 0.0f, 400.0f, 0.0f, 240.0f, 0.0f, 1.0f, true);
    }

    menu_draw(sel);
    while (aptMainLoop()) {
        u32 k;
        hidScanInput();
        k = hidKeysDown();
        cp_time = osGetTime();
        if (k & KEY_START) break;
        if (k & KEY_DOWN) sel = (sel + 1) % NSTAGES;
        if (k & KEY_UP) sel = (sel + NSTAGES - 1) % NSTAGES;
        if (k & KEY_Y) step_mode = !step_mode;
        if ((k & (KEY_A | KEY_X)) && !gpu_hung) run_stages(sel, (k & KEY_A) ? NSTAGES - 1 : sel);
        if (k) menu_draw(sel);
        gspWaitForVBlank();
    }

    step_mode = 0;
    if (keep_shader.ok && !gpu_hung) {
        C3D_BindProgram(&keep_shader.prog);
        shader_free(&keep_shader);
    }
    if (gpu_hung) {
        out("", "GPU hung: skipping C3D_Fini (it would wait for the GPU forever)");
    } else {
        cp("C3D_RenderTargetDelete + C3D_Fini");
        if (target) C3D_RenderTargetDelete(target);
        C3D_Fini();
    }
    watchdog_run = 0;
    if (dog) {
        threadJoin(dog, 1000000000ULL);
        threadFree(dog);
    }
    cp("gfxExit");
    console_ready = 0;
    gfxExit();
    log_line("exited normally");
    return 0;
}
