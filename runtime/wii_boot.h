/* Booting a Wii executable on the guest context: memory map, DOL loading and the boot
 * globals the apploader normally writes. Portable C; host-tested in tests/test_wii_boot.py.
 *
 * Memory map and globals are taken from WiiBrew's "Memory map" page (public): MEM1 24 MiB at
 * 0x80000000 with an uncached mirror at 0xC0000000, MEM2 64 MiB at 0x90000000 with a mirror
 * at 0xD0000000, and the "Broadway / IOS Global Memory Locations" table. Values the table
 * lists as "typical" are used as given; the arena and FST placement are computed here the
 * way the table describes (arena high = FST start, near the top of MEM1).
 *
 * The DOL file is the user's own main.dol, read from their SD card at run time. Nothing
 * from a game is compiled into this file. MIT License. */
#ifndef SPM3DS_WII_BOOT_H
#define SPM3DS_WII_BOOT_H

#include <stddef.h>
#include <stdint.h>

#include "ppc_ctx.h"

#define WII_MEM1_BASE 0x80000000u
#define WII_MEM1_SIZE 0x01800000u
#define WII_MEM1_UNCACHED 0xC0000000u
#define WII_MEM2_BASE 0x90000000u
#define WII_MEM2_SIZE 0x04000000u
#define WII_MEM2_UNCACHED 0xD0000000u

typedef struct {
    uint32_t offset, address, size;
} wii_dol_section;

typedef struct {
    wii_dol_section text[7];
    wii_dol_section data[11];
    uint32_t bss_address, bss_size, entry;
    uint32_t end;              /* highest address any section or the bss reaches */
} wii_dol;

/* Parses and checks a DOL header (sections inside the file, entry in a text section).
 * Returns 0 on success, or a negative code with a message in *why. */
int wii_dol_parse(const uint8_t *file, size_t size, wii_dol *out, const char **why);

/* Maps MEM1 and MEM2 as paged regions plus their uncached mirrors. Returns 0 on success. */
int wii_boot_map_memory(ppc_ctx *c);

/* Copies every DOL section into guest memory and clears the bss. Returns 0 on success. */
int wii_boot_load_dol(ppc_ctx *c, const uint8_t *file, const wii_dol *dol);

typedef struct {
    const uint8_t *game_id;    /* 6 bytes from boot.bin (game code + maker), or NULL */
    const uint8_t *fst;        /* the disc's fst.bin, or NULL */
    uint32_t fst_size;
    uint32_t video_mode;       /* 0x800000CC: 0 NTSC, 1 PAL, 2 MPAL */
} wii_boot_info;

/* Writes the boot globals and places the FST at the top of MEM1. Returns the guest address
 * of the FST copy (0 if none). */
uint32_t wii_boot_globals(ppc_ctx *c, const wii_dol *dol, const wii_boot_info *info);

/* CRC-32 (the zlib/PNG polynomial), used to check that the DOL on the SD card is the one the
 * translated code was generated from. */
uint32_t wii_crc32(const uint8_t *data, size_t n);

#endif /* SPM3DS_WII_BOOT_H */
