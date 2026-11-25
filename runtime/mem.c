/* Paged guest memory: regions whose 64 KiB pages are allocated on first touch.
 * A flat region needs its whole size in host memory up front. A paged region only needs
 * the pages the guest actually uses, which matters on the 3DS, where the application
 * memory limit is far below the Wii's 88 MiB map. MIT License. */
#include "ppc_ctx.h"

int ppc_map_paged(ppc_ctx *c, uint32_t base, uint32_t size) {
    ppc_region *g;
    uint32_t npages, i;
    if (c->nregions >= PPC_MAX_REGIONS || size == 0 || (size & (PPC_PAGE_SIZE - 1u)) != 0)
        return -1;
    for (i = 0; i < c->nregions; i++) {  /* refuse overlap with an existing region */
        const ppc_region *o = &c->regions[i];
        if (base < o->base + o->size && o->base < base + size) return -1;
    }
    npages = size >> PPC_PAGE_SHIFT;
    g = &c->regions[c->nregions];
    g->pages = (uint8_t **)calloc(npages, sizeof(uint8_t *));
    if (!g->pages) return -1;
    g->base = base;
    g->size = size;
    g->data = NULL;
    g->pages_used = 0;
    c->nregions++;
    return 0;
}

size_t ppc_committed_bytes(const ppc_ctx *c) {
    size_t total = 0;
    unsigned i;
    for (i = 0; i < c->nregions; i++) total += (size_t)c->regions[i].pages_used * PPC_PAGE_SIZE;
    return total;
}

void ppc_unmap_paged(ppc_ctx *c) {
    unsigned i;
    for (i = 0; i < c->nregions; i++) {
        ppc_region *g = &c->regions[i];
        uint32_t p, npages;
        if (!g->pages) continue;
        npages = g->size >> PPC_PAGE_SHIFT;
        for (p = 0; p < npages; p++) free(g->pages[p]);
        free(g->pages);
        g->pages = NULL;
        g->pages_used = 0;
    }
}
