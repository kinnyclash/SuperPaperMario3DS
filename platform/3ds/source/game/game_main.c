/* spm3ds game app (make APP=game): boots the user's own main.dol on the New 3DS.
 *
 * The translated code (source/game_generated, made on the user's computer from their own
 * main.dol with `python -m spm3ds.recomp compile main.dol DIR --split N --game-info`) is
 * linked in; at run time this file reads the same main.dol from the SD card, checks it is
 * the one the code was generated from (size and CRC-32), loads it into guest memory with the
 * Wii boot globals, attaches the GX FIFO capture, disc files, threads and audio, and jumps to
 * the translated entry point. Each call the game makes to VIWaitForRetrace presents a frame.
 *
 * Files on the SD card (all copied by the user from their own disc; none ship with spm3ds):
 *   sdmc:/spm3ds/disc/sys/main.dol   required
 *   sdmc:/spm3ds/disc/sys/boot.bin   optional (game ID and region)
 *   sdmc:/spm3ds/disc/sys/fst.bin    optional (disc file table)
 *   sdmc:/spm3ds/disc/files/         disc files
 * Hold START + SELECT to quit.
 *
 * Stand-in builds (tools/make_game_stub.py) also link game_embedded.c, which holds the stub
 * DOL; the app then boots it without any SD card files. Real game builds never contain it.
 *
 * UNVERIFIED ON HARDWARE; the host tests run the same boot sequence (tests/test_game_boot.py).
 * MIT License. */
#include <3ds.h>
#include <citro3d.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "audio_ndsp.h"
#include "gx_scene.h"
#include "hle.h"
#include "wii_boot.h"

#define DISPLAY_TRANSFER_FLAGS \
    (GX_TRANSFER_FLIP_VERT(0) | GX_TRANSFER_OUT_TILED(0) | GX_TRANSFER_RAW_COPY(0) | \
     GX_TRANSFER_IN_FORMAT(GX_TRANSFER_FMT_RGBA8) | GX_TRANSFER_OUT_FORMAT(GX_TRANSFER_FMT_RGB8) | \
     GX_TRANSFER_SCALING(GX_TRANSFER_SCALE_NO))
#ifndef SPM3DS_TICK_HZ
#  ifdef SYSCLOCK_ARM11
#    define SPM3DS_TICK_HZ ((uint64_t)SYSCLOCK_ARM11)
#  else
#    define SPM3DS_TICK_HZ 268111856ull
#  endif
#endif
#define MAIN_THREAD_GUEST 0x80001800u  /* unused exception area (WiiBrew), holds no data */

extern const uint32_t spm3ds_game_dol_size, spm3ds_game_dol_crc32, spm3ds_game_entry_address;
/* Present only in stand-in builds (weak: real game builds leave them undefined). */
extern const uint32_t spm3ds_game_embedded_dol_size __attribute__((weak));
extern const uint8_t spm3ds_game_embedded_dol[] __attribute__((weak));
void spm3ds_game_entry(ppc_ctx *c);

static ppc_ctx *ctx;
static spm3ds_hw hw;
static gx_scene scene;
static C3D_RenderTarget *target;
static u32 frames;

static uint64_t now_ns(void) {
    uint64_t ticks = svcGetSystemTick();
    uint64_t seconds = ticks / SPM3DS_TICK_HZ;
    uint64_t rest = ticks % SPM3DS_TICK_HZ;
    return seconds * 1000000000ull + (rest * 1000000000ull) / SPM3DS_TICK_HZ;
}

static uint8_t *read_file(const char *path, size_t *size) {
    FILE *f = fopen(path, "rb");
    long n;
    uint8_t *buf;
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0 || (n = ftell(f)) <= 0 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return NULL;
    }
    buf = (uint8_t *)malloc((size_t)n);
    if (buf && fread(buf, 1, (size_t)n, f) != (size_t)n) {
        free(buf);
        buf = NULL;
    }
    fclose(f);
    if (buf) *size = (size_t)n;
    return buf;
}

static void shutdown_all(void) {
    spm3ds_audio_exit();
    C3D_Fini();
    gfxExit();
}

static void stop(const char *message) {
    printf("\n%s\n\nPress START to exit.\n", message);
    while (aptMainLoop()) {
        hidScanInput();
        if (hidKeysDown() & KEY_START) break;
        gspWaitForVBlank();
    }
    shutdown_all();
    exit(1);
}

/* The game calls this once per video frame: present what it drew, then service the system. */
void hle_VIWaitForRetrace(ppc_ctx *c) {
    gx_scene_present(&scene, target);
    spm3ds_audio_pump();
    hidScanInput();
    if (!aptMainLoop() ||
        (hidKeysHeld() & (KEY_START | KEY_SELECT)) == (KEY_START | KEY_SELECT)) {
        shutdown_all();
        exit(0);
    }
    if ((++frames % 60u) == 0u) {
        printf("\x1b[20;0Hframe %lu  triangles %lu  FIFO %lu  GX errors %d   \n"
               "guest memory %lu KiB  textures %lu   ",
               (unsigned long)frames, (unsigned long)scene.triangles,
               (unsigned long)scene.fifo_bytes, scene.error,
               (unsigned long)(ppc_committed_bytes(c) / 1024u),
               (unsigned long)scene.texture_uploads);
    }
    spm3ds_os_poll(c);
}

int main(void) {
    size_t dol_size = 0, boot_size = 0, fst_size = 0;
    uint8_t *dol, *boot, *fst;
    wii_dol d;
    wii_boot_info info;
    const char *why = "";
    int rc;

    osSetSpeedupEnable(true);        /* New 3DS: 804 MHz and the extra cache */
    gfxInitDefault();
    C3D_Init(C3D_DEFAULT_CMDBUF_SIZE);
    consoleInit(GFX_BOTTOM, NULL);
    printf("spm3ds game app\n");
    spm3ds_set_clock(now_ns);

    target = C3D_RenderTargetCreate(240, 400, GPU_RB_RGBA8, GPU_RB_DEPTH24_STENCIL8);
    if (!target) stop("Could not create the render target.");
    C3D_RenderTargetSetOutput(target, GFX_TOP, GFX_LEFT, DISPLAY_TRANSFER_FLAGS);

    if (&spm3ds_game_embedded_dol_size != NULL) {
        dol_size = spm3ds_game_embedded_dol_size;
        dol = (uint8_t *)malloc(dol_size);
        if (dol) memcpy(dol, spm3ds_game_embedded_dol, dol_size);
        printf("stand-in build: booting the embedded test DOL\n");
    } else {
        dol = read_file("sdmc:/spm3ds/disc/sys/main.dol", &dol_size);
    }
    if (!dol) {
        stop("sdmc:/spm3ds/disc/sys/main.dol not found.\n"
             "Copy main.dol from your own extracted disc (see docs/REQUIRED_FILES.md).");
    }
    if (dol_size != spm3ds_game_dol_size || wii_crc32(dol, dol_size) != spm3ds_game_dol_crc32) {
        printf("main.dol: %lu bytes, CRC-32 %08lX\nexpected: %lu bytes, CRC-32 %08lX\n",
               (unsigned long)dol_size, (unsigned long)wii_crc32(dol, dol_size),
               (unsigned long)spm3ds_game_dol_size, (unsigned long)spm3ds_game_dol_crc32);
        stop("This main.dol is not the one this app was built from. Rebuild the app from\n"
             "the main.dol on the SD card.");
    }
    rc = wii_dol_parse(dol, dol_size, &d, &why);
    if (rc != 0 || d.entry != spm3ds_game_entry_address) stop(why);

    ctx = (ppc_ctx *)calloc(1, sizeof *ctx);
    if (!ctx || wii_boot_map_memory(ctx) != 0) stop("Could not map guest memory.");
    if (wii_boot_load_dol(ctx, dol, &d) != 0) stop("Could not load main.dol into memory.");
    free(dol);

    memset(&info, 0, sizeof info);
    boot = read_file("sdmc:/spm3ds/disc/sys/boot.bin", &boot_size);
    if (boot && boot_size >= 6) {
        info.game_id = boot;
        /* Region letter: E (USA) and J (Japan) are NTSC; the others use PAL timing. */
        info.video_mode = (boot[3] == 'E' || boot[3] == 'J') ? 0u : 1u;
        printf("game ID %.6s\n", (const char *)boot);
    }
    fst = read_file("sdmc:/spm3ds/disc/sys/fst.bin", &fst_size);
    if (fst) {
        info.fst = fst;
        info.fst_size = (uint32_t)fst_size;
        spm3ds_dvd_load_fst(fst, fst_size);
    }
    wii_boot_globals(ctx, &d, &info);
    spm3ds_dvd_set_root("sdmc:/spm3ds/disc/files");
    spm3ds_fs_set_root("sdmc:/spm3ds/data");

    spm3ds_hw_init(&hw);
    spm3ds_hw_attach(ctx, &hw);
    spm3ds_os_threads_init(ctx, MAIN_THREAD_GUEST);
    printf("%s", spm3ds_audio_init() ? "audio: NDSP ready\n" : "audio: off (no DSP firmware dump)\n");
    if (gx_scene_create(&scene, ctx, &hw) != 0) stop("Could not set up the GX renderer.");

    printf("booting entry %08lX...\nHold START + SELECT to quit.\n", (unsigned long)d.entry);
    spm3ds_game_entry(ctx);
    stop("The game's entry point returned.");
    return 0;
}
