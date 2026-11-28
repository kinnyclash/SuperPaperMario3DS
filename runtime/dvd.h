/* Wii DVD file access for translated code (HLE), backed by files the user extracted from
 * their own disc.
 *
 * Files come from one root directory (on the 3DS: sdmc:/spm3ds/disc/files). If the disc's
 * file system table (sys/fst.bin, also extracted by the user) is loaded, paths are resolved
 * through it, case-insensitively, and entry numbers work (DVDFastOpen,
 * DVDConvertPathToEntrynum). Without it, paths map straight to the root directory.
 *
 * FST format (public, WiiBrew "Wii disc" and YAGCD): 12-byte entries; byte 0 is 1 for a
 * directory; bytes 1..3 are the name offset into the string table that follows the entries;
 * for a directory, word 1 is the parent entry and word 2 the index after its last child; for
 * a file, word 2 is its length. Entry 0 is the root and its word 2 is the entry count.
 *
 * UNVERIFIED: the DVDFileInfo field offsets below come from the GameCube/Wii SDK layout as
 * described by community decompilations (command block of 0x30 bytes, then startAddr,
 * length, callback). They are not checked against a public header here. Only `length` is
 * read by typical game code (DVDGetLength); the HLE keeps its own handle in `startAddr`.
 * The Nintendo SDK function names are also unverified in this project.
 * MIT License. */
#ifndef SPM3DS_DVD_H
#define SPM3DS_DVD_H

#include <stddef.h>
#include <stdint.h>

#include "ppc_ctx.h"

#define SPM3DS_DVD_FI_STATE 0x0Cu    /* DVDCommandBlock.state (UNVERIFIED) */
#define SPM3DS_DVD_FI_START 0x30u    /* DVDFileInfo.startAddr (UNVERIFIED; holds our handle) */
#define SPM3DS_DVD_FI_LENGTH 0x34u   /* DVDFileInfo.length (UNVERIFIED) */
#define SPM3DS_DVD_FI_CALLBACK 0x38u /* DVDFileInfo.callback (UNVERIFIED) */
#define SPM3DS_DVD_MAX_OPEN 32

/* Root directory of the extracted disc files. */
void spm3ds_dvd_set_root(const char *dir);
/* Loads an FST image (copied). Returns the number of entries, or -1 if it is malformed. */
int spm3ds_dvd_load_fst(const uint8_t *fst, size_t size);
/* Loads an FST from a host file. Returns the number of entries, or -1. */
int spm3ds_dvd_load_fst_file(const char *path);
/* Closes all files and forgets the FST. */
void spm3ds_dvd_reset(void);

/* Entry number of a path through the loaded FST, or -1. */
int32_t spm3ds_dvd_entrynum(const char *path);

/* HLE entry points (arguments in r3..r8, result in r3). */
void hle_DVDOpen(ppc_ctx *c);                 /* (path, fileInfo) -> BOOL */
void hle_DVDFastOpen(ppc_ctx *c);             /* (entrynum, fileInfo) -> BOOL */
void hle_DVDConvertPathToEntrynum(ppc_ctx *c);/* (path) -> entrynum or -1 */
void hle_DVDClose(ppc_ctx *c);                /* (fileInfo) -> BOOL */
void hle_DVDReadPrio(ppc_ctx *c);             /* (fileInfo, addr, length, offset, prio) -> bytes */
void hle_DVDReadAsyncPrio(ppc_ctx *c);        /* (fileInfo, addr, length, offset, cb, prio) */
void hle_DVDGetCommandBlockStatus(ppc_ctx *c);/* (block) -> 0 (done) */
void hle_DVDGetDriveStatus(ppc_ctx *c);       /* () -> 0 (ready) */

#endif /* SPM3DS_DVD_H */
