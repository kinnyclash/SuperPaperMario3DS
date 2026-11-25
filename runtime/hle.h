/* High-level emulation (HLE) of Wii OS functions, implemented on the host.
 * A guest call to an address bound by a symbol file is compiled to a call to hle_<name>.
 * Arguments follow the PowerPC convention: integers in r3..r10, floats in f1..f8.
 * Results are returned in r3 (or f1). MIT License. */
#ifndef SPM3DS_HLE_H
#define SPM3DS_HLE_H

#include "ppc_ctx.h"

void hle_OSReport(ppc_ctx *c);
void hle_OSHalt(ppc_ctx *c);
void hle_memcpy(ppc_ctx *c);
void hle_memset(ppc_ctx *c);
void hle_DCFlushRange(ppc_ctx *c);
void hle_DCInvalidateRange(ppc_ctx *c);
void hle_ICInvalidateRange(ppc_ctx *c);

/* Wii OS compatibility layer (runtime/os.c). */
void hle_OSCreateHeap(ppc_ctx *c);
void hle_OSAllocFromHeap(ppc_ctx *c);
void hle_OSFreeToHeap(ppc_ctx *c);
void hle_OSGetTick(ppc_ctx *c);
void hle_OSGetTime(ppc_ctx *c);
void hle_spm3ds_FSOpen(ppc_ctx *c);
void hle_spm3ds_FSSize(ppc_ctx *c);
void hle_spm3ds_FSRead(ppc_ctx *c);
void hle_spm3ds_FSClose(ppc_ctx *c);

/* Platform hooks (see runtime/os.c). */
void spm3ds_set_clock(uint64_t (*source_ns)(void));
void spm3ds_fs_set_root(const char *dir);

#endif /* SPM3DS_HLE_H */
