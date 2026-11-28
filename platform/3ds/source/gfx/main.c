/* spm3ds graphics demo for the New 3DS / New 2DS XL top screen.
 *
 * Two scenes, switched with A:
 *   1. a textured cube drawn directly with citro3d (gx_citro3d.c);
 *   2. a cube drawn by translated PowerPC code through the Wii GX command path: the guest
 *      program writes GX commands to the write-gather pipe, runtime/hw.c captures them,
 *      runtime/gx_cp.c decodes them and gx_scene.c draws the triangles (gx_scene.c).
 * Press START to exit. The bottom screen shows the scene and its statistics.
 *
 * The texture and both cubes are generated in code, so the demo ships no art and nothing
 * here comes from a game.
 *
 * UNVERIFIED ON HARDWARE. API names were checked against the devkitPro citro3d and libctru
 * headers; both apps compile in CI with devkitARM. MIT License. */
#include <3ds.h>
#include <citro3d.h>
#include <stdio.h>

#include "gx_citro3d.h"
#include "gx_demo.h"

int main(void) {
    gfx_cube cube;
    gx_demo scene;
    int rc_cube, rc_scene;
    int which = 1;          /* start on the translated-GX scene: it is the new path */
    u32 frames = 0;
    float angle = 0.0f;

    if (gfx_init() != 0) return 1;
    consoleInit(GFX_BOTTOM, NULL);
    printf("spm3ds graphics demo\n");

    rc_cube = gfx_cube_create(&cube);
    rc_scene = rc_cube == 0 ? gx_demo_create(&scene) : -100;
    if (rc_cube != 0) printf("cube setup failed (step %d)\n", rc_cube);
    if (rc_scene != 0) printf("GX scene setup failed (step %d)\n", rc_scene);
    printf("A: switch scene   START: exit\n");

    while (aptMainLoop()) {
        u32 down;
        hidScanInput();
        down = hidKeysDown();
        if (down & KEY_START) break;
        if ((down & KEY_A) && rc_scene == 0) which ^= 1;
        if (rc_cube != 0) {
            gspWaitForVBlank();
            continue;
        }
        if (which == 1 && rc_scene == 0) {
            gx_demo_frame(&scene, cube.target, angle, angle * 0.7f);
        } else {
            gfx_cube_frame(&cube, angle * 0.7f, angle);
        }
        angle += 0.02f;
        frames++;
        if ((frames % 30u) == 0u) {
            printf("\x1b[5;0Hscene: %s          \n",
                   which == 1 ? "translated PowerPC -> GX" : "native citro3d cube");
            if (which == 1) {
                printf("GX FIFO bytes: %lu    \ntriangles: %lu    \nGX errors: %d    \n",
                       (unsigned long)scene.scene.fifo_bytes,
                       (unsigned long)scene.scene.triangles, scene.scene.error);
            } else {
                printf("                          \n                          \n"
                       "                          \n");
            }
            printf("frames: %lu     \n", (unsigned long)frames);
        }
    }

    if (rc_scene == 0) gx_demo_destroy(&scene);
    if (rc_cube == 0) gfx_cube_destroy(&cube);
    gfx_shutdown();
    return 0;
}
