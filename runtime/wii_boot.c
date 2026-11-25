/* Booting a Wii executable on the guest context. See wii_boot.h. MIT License. */
#include "wii_boot.h"

#include <string.h>

static uint32_t rd32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

int wii_dol_parse(const uint8_t *f, size_t size, wii_dol *d, const char **why) {
    unsigned i;
    int entry_ok = 0;
    const char *dummy;
    if (!why) why = &dummy;
    memset(d, 0, sizeof *d);
    if (size < 0x100) {
        *why = "file is shorter than the 0x100-byte DOL header";
        return -1;
    }
    for (i = 0; i < 7; i++) {
        d->text[i].offset = rd32(f + 0x00 + 4 * i);
        d->text[i].address = rd32(f + 0x48 + 4 * i);
        d->text[i].size = rd32(f + 0x90 + 4 * i);
    }
    for (i = 0; i < 11; i++) {
        d->data[i].offset = rd32(f + 0x1C + 4 * i);
        d->data[i].address = rd32(f + 0x64 + 4 * i);
        d->data[i].size = rd32(f + 0xAC + 4 * i);
    }
    d->bss_address = rd32(f + 0xD8);
    d->bss_size = rd32(f + 0xDC);
    d->entry = rd32(f + 0xE0);
    for (i = 0; i < 18; i++) {
        const wii_dol_section *s = i < 7 ? &d->text[i] : &d->data[i - 7];
        if (!s->size) continue;
        if ((uint64_t)s->offset + s->size > size) {
            *why = "a section extends past the end of the file";
            return -2;
        }
        if (s->address + s->size > d->end) d->end = s->address + s->size;
        if (i < 7 && d->entry >= s->address && d->entry < s->address + s->size) entry_ok = 1;
    }
    if (d->bss_size && d->bss_address + d->bss_size > d->end)
        d->end = d->bss_address + d->bss_size;
    if (!entry_ok) {
        *why = "the entry point is not inside a text section";
        return -3;
    }
    return 0;
}

int wii_boot_map_memory(ppc_ctx *c) {
    if (ppc_map_paged(c, WII_MEM1_BASE, WII_MEM1_SIZE) != 0) return -1;
    if (ppc_map_paged(c, WII_MEM2_BASE, WII_MEM2_SIZE) != 0) return -2;
    if (ppc_map_alias(c, WII_MEM1_UNCACHED, WII_MEM1_BASE) != 0) return -3;
    if (ppc_map_alias(c, WII_MEM2_UNCACHED, WII_MEM2_BASE) != 0) return -4;
    return 0;
}

/* Copies n bytes into guest memory, page by page. */
static int put(ppc_ctx *c, uint32_t ea, const uint8_t *src, uint32_t n) {
    while (n) {
        uint32_t room = PPC_PAGE_SIZE - (ea & (PPC_PAGE_SIZE - 1u));
        uint32_t k = n < room ? n : room;
        uint8_t *p = ppc_try(c, ea, k);
        if (!p) return -1;
        if (src) {
            memcpy(p, src, k);
            src += k;
        } else {
            memset(p, 0, k);
        }
        ea += k;
        n -= k;
    }
    return 0;
}

int wii_boot_load_dol(ppc_ctx *c, const uint8_t *file, const wii_dol *d) {
    unsigned i;
    for (i = 0; i < 18; i++) {
        const wii_dol_section *s = i < 7 ? &d->text[i] : &d->data[i - 7];
        if (s->size && put(c, s->address, file + s->offset, s->size) != 0) return -1;
    }
    if (d->bss_size && put(c, d->bss_address, NULL, d->bss_size) != 0) return -2;
    return 0;
}

static void w32(ppc_ctx *c, uint32_t ea, uint32_t v) { ppc_w32(c, ea, v); }

uint32_t wii_crc32(const uint8_t *data, size_t n) {
    uint32_t crc = 0xFFFFFFFFu;
    size_t i;
    unsigned k;
    for (i = 0; i < n; i++) {
        crc ^= data[i];
        for (k = 0; k < 8; k++) crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

uint32_t wii_boot_globals(ppc_ctx *c, const wii_dol *d, const wii_boot_info *info) {
    uint32_t arena_lo = (d->end + 31u) & ~31u;
    uint32_t fst_size = info && info->fst ? info->fst_size : 0;
    /* FST near the top of MEM1, below the bi2 area (WiiBrew's typical bi2 pointer is
     * 0x817FDF80 and typical FST start 0x817FEC60, i.e. both just under the top). */
    uint32_t top = 0x817FDF80u;
    uint32_t fst_addr = fst_size ? ((top - fst_size) & ~31u) : 0;
    uint32_t arena_hi = fst_size ? fst_addr : top;
    unsigned i;

    if (info && info->game_id)
        for (i = 0; i < 6; i++) ppc_w8(c, 0x80000000u + i, info->game_id[i]);
    w32(c, 0x80000018u, 0x5D1C9EA3u);   /* disc layout magic (Wii) */
    w32(c, 0x80000020u, 0x0D15EA5Eu);   /* Nintendo standard boot code */
    w32(c, 0x80000024u, 1);             /* version (set by apploader) */
    w32(c, 0x80000028u, WII_MEM1_SIZE); /* physical memory size */
    w32(c, 0x8000002Cu, 0x23);          /* production board model */
    w32(c, 0x80000030u, arena_lo);      /* arena low */
    w32(c, 0x80000034u, arena_hi);      /* arena high */
    w32(c, 0x80000038u, fst_addr);      /* start of FST */
    w32(c, 0x8000003Cu, fst_size);      /* maximum FST size */
    w32(c, 0x800000C4u, 0xFFFFFF00u);   /* user interrupt mask */
    w32(c, 0x800000C8u, 0);             /* OS interrupt mask */
    w32(c, 0x800000CCu, info ? info->video_mode : 0);
    w32(c, 0x800000F0u, WII_MEM1_SIZE); /* simulated memory size */
    w32(c, 0x800000F4u, 0);             /* no bi2.bin data */
    w32(c, 0x800000F8u, 0x0E7BE2C0u);   /* bus speed: 243 MHz */
    w32(c, 0x800000FCu, 0x2B73A840u);   /* CPU speed: 729 MHz */
    w32(c, 0x800030F0u, 0);             /* DOL execute parameters */
    w32(c, 0x80003100u, WII_MEM1_SIZE); /* physical MEM1 size */
    w32(c, 0x80003104u, WII_MEM1_SIZE); /* simulated MEM1 size */
    w32(c, 0x8000310Cu, arena_lo);      /* MEM1 arena start */
    w32(c, 0x80003110u, arena_hi);      /* MEM1 arena end */
    w32(c, 0x80003118u, WII_MEM2_SIZE); /* physical MEM2 size */
    w32(c, 0x8000311Cu, WII_MEM2_SIZE); /* simulated MEM2 size */
    w32(c, 0x80003120u, 0x93400000u);   /* end of MEM2 addressable by PPC */
    w32(c, 0x80003124u, 0x90000800u);   /* usable MEM2 start */
    w32(c, 0x80003128u, 0x933E0000u);   /* usable MEM2 end */
    w32(c, 0x80003130u, 0x933E0000u);   /* IOS IPC buffer start */
    w32(c, 0x80003134u, 0x93400000u);   /* IOS IPC buffer end */
    w32(c, 0x80003138u, 0x11);          /* Hollywood version */
    w32(c, 0x80003140u, 0x00090204u);   /* IOS version (typical value in the table) */
    ppc_w8(c, 0x8000315Cu, 0x80);       /* boot process marker */
    w32(c, 0x80003160u, 0);             /* init semaphore clear: main() may run */
    w32(c, 0x80003164u, 0);             /* not GameCube mode */
    if (info && info->game_id)
        for (i = 0; i < 4; i++) ppc_w8(c, 0x80003180u + i, info->game_id[i]);
    if (fst_size) put(c, fst_addr, info->fst, fst_size);
    return fst_addr;
}
