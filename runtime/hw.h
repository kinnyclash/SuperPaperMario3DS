/* Emulated Wii hardware registers and GX FIFO capture for translated code.
 *
 * Translated game code drives the GPU the way the original did: the game's GX library stores
 * command bytes to the write-gather pipe at 0xCC008000 (libogc gx.c: wgPipe = 0xCC008000).
 * spm3ds_hw_attach() installs a ppc_ctx MMIO hook that captures those stores, in order, into a
 * FIFO buffer for the GX command processor. Other addresses in the two hardware blocks
 * (0xCC000000..0xCC00FFFF and 0xCD000000..0xCD00FFFF) are plain storage: a read returns the
 * last value written. Status registers that real hardware updates by itself (for example GPU
 * idle bits or the video beam position) are NOT modelled yet; code that polls them may wait.
 * MIT License. */
#ifndef SPM3DS_HW_H
#define SPM3DS_HW_H

#include <stddef.h>
#include <stdint.h>

#include "ppc_ctx.h"

#define SPM3DS_WGPIPE 0xCC008000u
#define SPM3DS_WGPIPE_SIZE 0x20u
#define SPM3DS_HW_BLOCK_SIZE 0x10000u
#define SPM3DS_FIFO_LIMIT (8u << 20) /* bytes kept before further writes are dropped */

typedef struct spm3ds_hw {
    uint8_t *fifo;          /* captured GX command bytes */
    size_t fifo_size;
    size_t fifo_cap;
    uint64_t fifo_dropped;  /* bytes dropped after SPM3DS_FIFO_LIMIT or allocation failure */
    uint64_t fifo_total;    /* bytes ever written to the pipe */
    uint8_t regs_cc[SPM3DS_HW_BLOCK_SIZE]; /* 0xCC000000 block */
    uint8_t regs_cd[SPM3DS_HW_BLOCK_SIZE]; /* 0xCD000000 block (Hollywood) */
} spm3ds_hw;

void spm3ds_hw_init(spm3ds_hw *hw);
void spm3ds_hw_free(spm3ds_hw *hw);
/* Installs the MMIO hook on c. hw must outlive every guest call on c. */
void spm3ds_hw_attach(ppc_ctx *c, spm3ds_hw *hw);
/* Empties the FIFO buffer (the command processor has consumed it). */
void spm3ds_hw_fifo_clear(spm3ds_hw *hw);
/* The MMIO hook itself, exposed for tests. */
int spm3ds_hw_mmio(ppc_ctx *c, uint32_t ea, uint8_t *byte, int is_write);

#endif /* SPM3DS_HW_H */
