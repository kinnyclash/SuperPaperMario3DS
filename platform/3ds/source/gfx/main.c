/* spm3ds graphics demo: a textured, rotating cube on the New 3DS / New 2DS XL top screen.
 *
 * Press START to exit. The bottom screen shows the status and the frame count.
 * The texture is generated in code, so the demo ships no art. Nothing here comes from a game.
 *
 * UNVERIFIED ON HARDWARE. API names were checked against the devkitPro citro3d and libctru
 * headers, not against a device run. MIT License. */
#include <3ds.h>
#include <citro3d.h>
#include <stdio.h>

#include "gx_citro3d.h"

int main(void) {
    gfx_cube cube;
    int rc;
    u32 frames = 0;
    float angle = 0.0f;

    if (gfx_init() != 0) return 1;
    consoleInit(GFX_BOTTOM, NULL);
    printf("spm3ds graphics demo\n");

    rc = gfx_cube_create(&cube);
    if (rc != 0) {
        printf("setup failed (step %d)\nPress START to exit.\n", rc);
    } else {
        printf("textured cube running\nPress START to exit.\n");
    }

    while (aptMainLoop()) {
        hidScanInput();
        if (hidKeysDown() & KEY_START) break;
        if (rc != 0) {
            gspWaitForVBlank();
            continue;
        }
        gfx_cube_frame(&cube, angle * 0.7f, angle);
        angle += 0.02f;
        frames++;
        if ((frames % 60u) == 0u) {
            printf("\x1b[3;0Hframes: %lu     ", (unsigned long)frames);
        }
    }

    if (rc == 0) gfx_cube_destroy(&cube);
    gfx_shutdown();
    return 0;
}
