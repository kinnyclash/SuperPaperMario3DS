/* The translated GX demo. See gx_demo.h. MIT License. */
#include <math.h>
#include <stdlib.h>
#include <string.h>

#include "gx_demo.h"

void spm3ds_gx_demo_frame(ppc_ctx *c, float cy, float sy, float cx, float sx);
extern const uint32_t spm3ds_gx_demo_texture_addr;
extern const uint8_t spm3ds_gx_demo_texture[2048];

/* Guest memory for the demo: one 4 KiB region holding the demo texture. */
static uint8_t demo_memory[4096];

int gx_demo_create(gx_demo *d) {
    int rc;
    memset(d, 0, sizeof *d);
    d->ctx = (ppc_ctx *)calloc(1, sizeof *d->ctx);
    d->hw = (spm3ds_hw *)malloc(sizeof *d->hw);
    if (!d->ctx || !d->hw) return -10;
    memcpy(demo_memory, spm3ds_gx_demo_texture, sizeof spm3ds_gx_demo_texture);
    d->ctx->regions[0].base = spm3ds_gx_demo_texture_addr;
    d->ctx->regions[0].size = sizeof demo_memory;
    d->ctx->regions[0].data = demo_memory;
    d->ctx->nregions = 1;
    spm3ds_hw_init(d->hw);
    spm3ds_hw_attach(d->ctx, d->hw);
    rc = gx_scene_create(&d->scene, d->ctx, d->hw);
    return rc;
}

void gx_demo_frame(gx_demo *d, C3D_RenderTarget *target, float angle_y, float angle_x) {
    spm3ds_hw_fifo_clear(d->hw);
    spm3ds_gx_demo_frame(d->ctx, cosf(angle_y), sinf(angle_y), cosf(angle_x), sinf(angle_x));
    gx_scene_present(&d->scene, target);
}

void gx_demo_destroy(gx_demo *d) {
    gx_scene_destroy(&d->scene);
    if (d->hw) {
        spm3ds_hw_free(d->hw);
        free(d->hw);
    }
    free(d->ctx);
    memset(d, 0, sizeof *d);
}
