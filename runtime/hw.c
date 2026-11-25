/* Emulated Wii hardware registers and GX FIFO capture. See hw.h. MIT License. */
#include "hw.h"

#include <stdlib.h>
#include <string.h>

void spm3ds_hw_init(spm3ds_hw *hw) {
    memset(hw, 0, sizeof *hw);
}

void spm3ds_hw_free(spm3ds_hw *hw) {
    free(hw->fifo);
    hw->fifo = NULL;
    hw->fifo_size = hw->fifo_cap = 0;
}

void spm3ds_hw_fifo_clear(spm3ds_hw *hw) {
    hw->fifo_size = 0;
}

static void fifo_push(spm3ds_hw *hw, uint8_t b) {
    hw->fifo_total++;
    if (hw->fifo_size == hw->fifo_cap) {
        size_t cap = hw->fifo_cap ? hw->fifo_cap * 2u : 4096u;
        uint8_t *p;
        if (cap > SPM3DS_FIFO_LIMIT) cap = SPM3DS_FIFO_LIMIT;
        if (cap <= hw->fifo_size) {
            hw->fifo_dropped++;
            return;
        }
        p = (uint8_t *)realloc(hw->fifo, cap);
        if (!p) {
            hw->fifo_dropped++;
            return;
        }
        hw->fifo = p;
        hw->fifo_cap = cap;
    }
    hw->fifo[hw->fifo_size++] = b;
}

int spm3ds_hw_mmio(ppc_ctx *c, uint32_t ea, uint8_t *byte, int is_write) {
    spm3ds_hw *hw = (spm3ds_hw *)c->mmio_user;
    uint8_t *reg;
    if (!hw) return 0;
    if (ea - SPM3DS_WGPIPE < SPM3DS_WGPIPE_SIZE) {
        if (is_write) fifo_push(hw, *byte);
        else *byte = 0;
        return 1;
    }
    if (ea - 0xCC000000u < SPM3DS_HW_BLOCK_SIZE) reg = &hw->regs_cc[ea - 0xCC000000u];
    else if (ea - 0xCD000000u < SPM3DS_HW_BLOCK_SIZE) reg = &hw->regs_cd[ea - 0xCD000000u];
    else return 0;
    if (is_write) *reg = *byte;
    else *byte = *reg;
    return 1;
}

void spm3ds_hw_attach(ppc_ctx *c, spm3ds_hw *hw) {
    c->mmio = spm3ds_hw_mmio;
    c->mmio_user = hw;
}
