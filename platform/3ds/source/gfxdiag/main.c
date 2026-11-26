/* spm3ds graphics diagnostic (APP=gfxdiag).
 *
 * The graphics test froze a New 2DS XL right after launch, so this app walks through the
 * same graphics work one operation at a time. Before each operation it prints a numbered
 * checkpoint on the bottom screen and appends it to sdmc:/spm3ds/gfxdiag.log (the file is
 * closed after every line, so it survives a forced power-off). The last checkpoint shown
 * or logged is the operation that did not return.
 *
 * Extra safety nets:
 *   - GPU frames are started with C3D_FRAME_NONBLOCK and a 2 s timeout, so a GPU that never
 *     finishes is reported ("GPU TIMEOUT") instead of blocking forever;
 *   - a watchdog thread notices when no checkpoint is reached for 3 s, and prints and logs
 *     which one is stuck;
 *   - the decoded translated-GX vertices are checked (NaN, infinity, w <= 0, huge values)
 *     before they are drawn;
 *   - guest faults (stderr) are printed on the bottom screen too.
 *
 * Buttons at launch: hold L for step mode (press A to run each operation), hold R to skip
 * the native citro3d cube stages. At the end: START exits (exit is checkpointed too).
 * MIT License. */
#include <3ds.h>
#include <citro3d.h>
#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "cube_shbin.h"
#include "gx_demo.h"
#include "tiling.h"

#define LOG_PATH "sdmc:/spm3ds/gfxdiag.log"
#define TRANSFER_FLAGS \
    (GX_TRANSFER_FLIP_VERT(0) | GX_TRANSFER_OUT_TILED(0) | GX_TRANSFER_RAW_COPY(0) | \
     GX_TRANSFER_IN_FORMAT(GX_TRANSFER_FMT_RGBA8) | GX_TRANSFER_OUT_FORMAT(GX_TRANSFER_FMT_RGB8) | \
     GX_TRANSFER_SCALING(GX_TRANSFER_SCALE_NO))

void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx);

/* ---- checkpoints ---------------------------------------------------------- */

static int console_ready, step_mode;
static volatile int cp_number;          /* read by the watchdog */
static volatile u64 cp_time;
static char cp_text[96];
static volatile int watchdog_run = 1;
static int failures;

static void log_line(const char *line) {
    FILE *f = fopen(LOG_PATH, "a");
    if (!f) return;
    fputs(line, f);
    fputc('\n', f);
    fclose(f);
}

/* Makes what the console wrote visible: the bottom screen is single-buffered, so flushing
 * the CPU cache over it is enough (no GSP call, so the watchdog can use it too). */
static void show_bottom(void) {
    u16 w, h;
    u8 *fb;
    if (!console_ready) return;
    fb = gfxGetFramebuffer(GFX_BOTTOM, GFX_LEFT, &w, &h);
    svcFlushProcessDataCache(CUR_PROCESS_HANDLE, (u32)fb,
                             (u32)w * h * gspGetBytesPerPixel(gfxGetScreenFormat(GFX_BOTTOM)));
}

static void wait_frames(int n) {
    while (n-- > 0) gspWaitForVBlank();
}

/* Announces the operation about to run. */
static void cp(const char *fmt, ...) {
    char line[128];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(cp_text, sizeof cp_text, fmt, ap);
    va_end(ap);
    cp_number++;
    snprintf(line, sizeof line, "[%02d] %s", cp_number, cp_text);
    log_line(line);
    if (console_ready) {
        printf("%s\n", line);
        show_bottom();
    }
    cp_time = osGetTime();
    if (console_ready) {
        if (step_mode) {
            printf("     A: run it\n");
            show_bottom();
            for (;;) {
                hidScanInput();
                if (hidKeysDown() & KEY_A) break;
                gspWaitForVBlank();
            }
        } else {
            wait_frames(2);   /* let the line reach the screen before the next operation */
        }
    }
    cp_time = osGetTime();
}

/* Result of the last operation, on the same log. */
static void note(const char *fmt, ...) {
    char line[128], text[110];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    snprintf(line, sizeof line, "     %s", text);
    log_line(line);
    if (console_ready) {
        printf("%s\n", line);
        show_bottom();
    }
}

static void fail(const char *fmt, ...) {
    char line[128], text[110];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(text, sizeof text, fmt, ap);
    va_end(ap);
    failures++;
    snprintf(line, sizeof line, " !!! %s", text);
    log_line(line);
    if (console_ready) {
        printf("\x1b[31;1m%s\x1b[0m\n", line);
        show_bottom();
    }
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
            log_line(line);
            if (console_ready) {
                printf("\x1b[31;1m%s\x1b[0m\n", line);
                show_bottom();
            }
        }
    }
}

static void memory_report(const char *when) {
    note("%s: linear %luK vram %luK app %luK", when,
         (unsigned long)(linearSpaceFree() >> 10), (unsigned long)(vramSpaceFree() >> 10),
         (unsigned long)(osGetMemRegionFree(MEMREGION_APPLICATION) >> 10));
}

/* ---- GPU frames with a timeout -------------------------------------------- */

/* Starts a frame without blocking forever on a GPU that never finishes. */
static int frame_begin(int verbose) {
    u64 start = osGetTime();
    if (verbose) cp("C3D_FrameBegin (non-blocking, 2 s timeout)");
    while (!C3D_FrameBegin(C3D_FRAME_NONBLOCK)) {
        if (osGetTime() - start > 2000u) {
            fail("GPU TIMEOUT: previous frame never finished");
            return 0;
        }
        svcSleepThread(1000000LL);
    }
    return 1;
}

/* ---- native cube data (same geometry as gfx/gx_citro3d.c) ----------------- */

typedef struct {
    float pos[3];
    float tc[2];
} cube_vertex;

static void build_cube(cube_vertex *v) {
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
                cube_vertex *g = &v[n++];
                g->pos[axis] = 0.5f * (float)s;
                g->pos[ua] = 0.5f * cu[c];
                g->pos[va] = 0.5f * cv[c];
                g->tc[0] = tu[c];
                g->tc[1] = tv[c];
            }
        }
}

/* ---- stages --------------------------------------------------------------- */

static C3D_RenderTarget *target;

static int stage_clear(void) {
    int f;
    static const u32 colours[3] = {0xC03030FFu, 0x30C030FFu, 0x3030C0FFu};
    note("-- stage: clear-only frames (red, green, blue) --");
    for (f = 0; f < 90; f++) {
        int verbose = f < 2;
        if (!frame_begin(verbose)) return -1;
        if (verbose) cp("C3D_FrameDrawOn");
        C3D_FrameDrawOn(target);
        if (verbose) cp("C3D_RenderTargetClear");
        C3D_RenderTargetClear(target, C3D_CLEAR_ALL, colours[f / 30], 0);
        if (verbose) cp("C3D_FrameEnd");
        C3D_FrameEnd(0);
        cp_time = osGetTime();
    }
    note("90 clear frames done (top screen red/green/blue?)");
    return 0;
}

static int stage_cube(void) {
    DVLB_s *dvlb;
    shaderProgram_s prog;
    C3D_Tex tex;
    cube_vertex *vbo;
    uint8_t *pixels;
    C3D_AttrInfo *attr;
    C3D_BufInfo *buf;
    C3D_Mtx proj, mv;
    s8 u_proj, u_mv;
    int f;

    note("-- stage: native citro3d cube --");
    cp("DVLB_ParseFile (cube shader, %lu bytes)", (unsigned long)cube_shbin_size);
    if (((u32)cube_shbin & 3u) != 0) fail("shader binary not 4-byte aligned");
    dvlb = DVLB_ParseFile((u32 *)cube_shbin, cube_shbin_size);
    if (!dvlb) { fail("DVLB_ParseFile returned NULL"); return -1; }
    note("DVLEs: %lu", (unsigned long)dvlb->numDVLE);
    cp("shaderProgramInit");
    if (R_FAILED(shaderProgramInit(&prog))) { fail("shaderProgramInit"); return -1; }
    cp("shaderProgramSetVsh");
    if (R_FAILED(shaderProgramSetVsh(&prog, &dvlb->DVLE[0]))) { fail("SetVsh"); return -1; }
    cp("C3D_BindProgram");
    C3D_BindProgram(&prog);
    cp("uniform locations");
    u_proj = shaderInstanceGetUniformLocation(prog.vertexShader, "projection");
    u_mv = shaderInstanceGetUniformLocation(prog.vertexShader, "modelView");
    note("projection=%d modelView=%d", u_proj, u_mv);
    if (u_proj < 0 || u_mv < 0) { fail("uniform missing"); return -1; }

    cp("C3D_TexInit 64x64 RGBA8");
    if (!C3D_TexInit(&tex, 64, 64, GPU_RGBA8)) { fail("C3D_TexInit"); return -1; }
    note("texture data at %08lx (%s)", (unsigned long)(u32)tex.data,
         ((u32)tex.data >= 0x1F000000u && (u32)tex.data < 0x1F600000u) ? "VRAM" : "linear");
    cp("fill checker texture (CPU)");
    pixels = (uint8_t *)malloc(64u * 64u * 4u);
    if (!pixels) { fail("malloc"); return -1; }
    tiling_fill_checker_rgba8(pixels, 64, 64);
    cp("C3D_TexLoadImage");
    C3D_TexLoadImage(&tex, pixels, GPU_TEXFACE_2D, 0);
    free(pixels);
    cp("C3D_TexSetFilter");
    C3D_TexSetFilter(&tex, GPU_LINEAR, GPU_NEAREST);

    cp("linearAlloc vertex buffer (%u bytes)", (unsigned)(sizeof(cube_vertex) * 36u));
    vbo = (cube_vertex *)linearAlloc(sizeof(cube_vertex) * 36u);
    if (!vbo) { fail("linearAlloc"); return -1; }
    note("vbo at %08lx", (unsigned long)(u32)vbo);
    build_cube(vbo);
    cp("GSPGPU_FlushDataCache vbo");
    GSPGPU_FlushDataCache(vbo, sizeof(cube_vertex) * 36u);
    memory_report("after cube setup");

    cp("attribute and buffer setup");
    attr = C3D_GetAttrInfo();
    AttrInfo_Init(attr);
    AttrInfo_AddLoader(attr, 0, GPU_FLOAT, 3);
    AttrInfo_AddLoader(attr, 1, GPU_FLOAT, 2);
    buf = C3D_GetBufInfo();
    BufInfo_Init(buf);
    BufInfo_Add(buf, vbo, sizeof(cube_vertex), 2, 0x10);
    cp("texenv, depth test, texture bind");
    C3D_TexBind(0, &tex);
    {
        C3D_TexEnv *env = C3D_GetTexEnv(0);
        C3D_TexEnvInit(env);
        C3D_TexEnvSrc(env, C3D_Both, GPU_TEXTURE0, GPU_PRIMARY_COLOR, GPU_PRIMARY_COLOR);
        C3D_TexEnvFunc(env, C3D_Both, GPU_MODULATE);
    }
    C3D_DepthTest(true, GPU_GREATER, GPU_WRITE_ALL);
    C3D_CullFace(GPU_CULL_NONE);
    Mtx_PerspTilt(&proj, C3D_AngleFromDegrees(80.0f), C3D_AspectRatioTop, 0.01f, 1000.0f, false);

    for (f = 0; f < 120; f++) {
        int verbose = f < 2;
        float a = 0.03f * (float)f;
        Mtx_Identity(&mv);
        Mtx_Translate(&mv, 0.0f, 0.0f, -2.5f, true);
        Mtx_RotateX(&mv, a * 0.7f, true);
        Mtx_RotateY(&mv, a, true);
        if (!frame_begin(verbose)) break;
        if (verbose) cp("FrameDrawOn + clear");
        C3D_FrameDrawOn(target);
        C3D_RenderTargetClear(target, C3D_CLEAR_ALL, 0x68B0D8FFu, 0);
        if (verbose) cp("upload uniforms");
        C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, u_proj, &proj);
        C3D_FVUnifMtx4x4(GPU_VERTEX_SHADER, u_mv, &mv);
        if (verbose) cp("C3D_DrawArrays 36 vertices");
        C3D_DrawArrays(GPU_TRIANGLES, 0, 36);
        if (verbose) cp("C3D_FrameEnd");
        C3D_FrameEnd(0);
        cp_time = osGetTime();
    }
    if (f == 120) note("120 cube frames done (spinning cube?)");

    cp("wait for GPU idle, then free cube resources");
    if (frame_begin(0)) {   /* an empty frame: returns once the GPU finished the last one */
        C3D_FrameDrawOn(target);
        C3D_FrameEnd(0);
    }
    if (frame_begin(0)) {
        C3D_FrameDrawOn(target);
        C3D_FrameEnd(0);
    }
    C3D_TexDelete(&tex);
    linearFree(vbo);
    shaderProgramFree(&prog);
    DVLB_Free(dvlb);
    memory_report("after cube cleanup");
    return f == 120 ? 0 : -1;
}

/* Counts problems in the prepared vertices; prints the first bad one. */
static int check_vertices(const gx_scene *s) {
    u32 i, bad = 0, nan = 0, wneg = 0, huge = 0;
    for (i = 0; i < s->vertices; i++) {
        const gx_scene_vertex *v = &s->vbo[i];
        int k, b = 0;
        for (k = 0; k < 4; k++) {
            if (!isfinite(v->pos[k]) || !isfinite(v->clr[k])) { nan++; b = 1; }
            if (fabsf(v->pos[k]) > 1.0e6f) { huge++; b = 1; }
        }
        if (!isfinite(v->tc[0]) || !isfinite(v->tc[1])) { nan++; b = 1; }
        if (v->pos[3] <= 0.0f) { wneg++; b = 1; }
        if (b && bad++ == 0)
            note("first bad vertex %lu: %g %g %g %g", (unsigned long)i, (double)v->pos[0],
                 (double)v->pos[1], (double)v->pos[2], (double)v->pos[3]);
    }
    note("vertices %lu, batches %lu, bad %lu (nan %lu, w<=0 %lu, huge %lu)",
         (unsigned long)s->vertices, (unsigned long)s->cp->batch_count, (unsigned long)bad,
         (unsigned long)nan, (unsigned long)wneg, (unsigned long)huge);
    return (int)bad;
}

static int stage_gx(void) {
    gx_demo d;
    int rc, f;
    note("-- stage: translated PowerPC -> GX scene --");
    cp("gx_demo_create (guest context, GX shader, vbo)");
    rc = gx_demo_create(&d);
    if (rc != 0) { fail("gx_demo_create step %d", rc); return -1; }
    memory_report("after GX setup");
    d.scene.external_frame = 1;

    for (f = 0; f < 120; f++) {
        int verbose = f < 2;
        float a = 0.02f * (float)f;
        if (verbose) cp("run translated guest frame (CPU only)");
        spm3ds_hw_fifo_clear(d.hw);
        spm3ds_gx_demo_frame(d.ctx, cosf(a), sinf(a), cosf(a * 0.7f), sinf(a * 0.7f));
        if (verbose) {
            note("FIFO bytes %lu (dropped %lu)", (unsigned long)d.hw->fifo_size,
                 (unsigned long)d.hw->fifo_dropped);
            cp("gx_scene_prepare (decode, textures; CPU only)");
        }
        gx_scene_prepare(&d.scene);
        if (verbose) {
            note("GX errors %d, texture uploads %lu", d.scene.error,
                 (unsigned long)d.scene.texture_uploads);
            if (check_vertices(&d.scene) != 0) {
                fail("bad vertices: not drawing them");
                break;
            }
        }
        if (!frame_begin(verbose)) break;
        if (verbose) cp("FrameDrawOn + clear");
        C3D_FrameDrawOn(target);
        C3D_RenderTargetClear(target, C3D_CLEAR_ALL, 0x202028FFu, 0);
        if (verbose) cp("gx_scene_draw (%lu triangles)", (unsigned long)d.scene.triangles);
        gx_scene_draw(&d.scene, target);
        if (verbose) cp("C3D_FrameEnd");
        C3D_FrameEnd(0);
        cp_time = osGetTime();
    }
    if (f == 120) note("120 GX frames done (spinning cube?)");
    cp("wait for GPU idle, then gx_demo_destroy");
    if (frame_begin(0)) {
        C3D_FrameDrawOn(target);
        C3D_FrameEnd(0);
    }
    if (frame_begin(0)) {
        C3D_FrameDrawOn(target);
        C3D_FrameEnd(0);
    }
    gx_demo_destroy(&d);
    return f == 120 ? 0 : -1;
}

int main(void) {
    Thread dog;
    u32 held;
    bool is_new = false;
    u8 model = 0;
    int skip_cube;
    s32 main_prio = 0x30, dog_prio;

    mkdir("sdmc:/spm3ds", 0777);
    remove(LOG_PATH);
    log_line("spm3ds gfxdiag: graphics checkpoints (last line = where it stopped)");
    hidScanInput();
    held = hidKeysHeld();
    step_mode = (held & KEY_L) != 0;
    skip_cube = (held & KEY_R) != 0;
    APT_CheckNew3DS(&is_new);
    if (R_SUCCEEDED(cfguInit())) {
        CFGU_GetSystemModel(&model);
        cfguExit();
    }
    svcGetThreadPriority(&main_prio, CUR_THREAD_HANDLE);
    dog_prio = main_prio - 1 < 0x18 ? 0x18 : main_prio - 1;  /* above main when allowed */
    {
        char line[96];
        snprintf(line, sizeof line, "new3ds=%d model=%u step=%d skipcube=%d prio=%ld/%ld",
                 is_new, (unsigned)model, step_mode, skip_cube, (long)main_prio, (long)dog_prio);
        log_line(line);
    }
    dog = threadCreate(watchdog, NULL, 8 * 1024, dog_prio, -2, true);

    cp("gfxInitDefault");
    gfxInitDefault();
    cp("gfxSetDoubleBuffering(bottom, off)");
    gfxSetDoubleBuffering(GFX_BOTTOM, false);
    cp("consoleInit(bottom)");
    consoleInit(GFX_BOTTOM, NULL);
    consoleDebugInit(debugDevice_CONSOLE);  /* guest fault messages (stderr) appear here */
    console_ready = 1;
    printf("\x1b[1mspm3ds graphics diagnostic\x1b[0m  log: %s\n", LOG_PATH);
    printf("model %u, New 3DS %s, %s\n", (unsigned)model, is_new ? "yes" : "no",
           step_mode ? "step mode" : "auto");
    note("watchdog %s", dog ? "running" : "NOT started");
    note("top fb format %d, bottom %d", (int)gfxGetScreenFormat(GFX_TOP),
         (int)gfxGetScreenFormat(GFX_BOTTOM));
    memory_report("before C3D_Init");

    cp("C3D_Init (command buffer %u bytes)", (unsigned)C3D_DEFAULT_CMDBUF_SIZE);
    if (!C3D_Init(C3D_DEFAULT_CMDBUF_SIZE)) {
        fail("C3D_Init returned false");
        goto done;
    }
    memory_report("after C3D_Init");
    cp("C3D_RenderTargetCreate 240x400 RGBA8 + D24S8");
    target = C3D_RenderTargetCreate(240, 400, GPU_RB_RGBA8, GPU_RB_DEPTH24_STENCIL8);
    if (!target) {
        fail("render target creation failed");
        goto done;
    }
    cp("C3D_RenderTargetSetOutput(top, left)");
    C3D_RenderTargetSetOutput(target, GFX_TOP, GFX_LEFT, TRANSFER_FLAGS);
    memory_report("after render target");

    if (stage_clear() != 0) goto done;
    if (!skip_cube && stage_cube() != 0) goto done;
    stage_gx();

done:
    note("== %s: %d failure(s). START: exit ==", failures ? "FINISHED WITH ERRORS" : "ALL PASSED",
         failures);
    while (aptMainLoop()) {
        hidScanInput();
        if (hidKeysDown() & KEY_START) break;
        gspWaitForVBlank();
        cp_time = osGetTime();
    }
    step_mode = 0;
    if (target) {
        cp("C3D_RenderTargetDelete");
        C3D_RenderTargetDelete(target);
    }
    cp("C3D_Fini");
    C3D_Fini();
    watchdog_run = 0;
    if (dog) {
        threadJoin(dog, 1000000000ULL);
        threadFree(dog);
    }
    cp("gfxExit");
    gfxExit();
    log_line("exited normally");
    return 0;
}
