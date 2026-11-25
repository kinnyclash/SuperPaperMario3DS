/* Wii Remote to 3DS key conversion (foundation). Mirrors spm3ds/input.py. */
#ifndef SPM3DS_INPUT_H
#define SPM3DS_INPUT_H

#include <stdint.h>

#define SPM3DS_KEY_A        (1u << 0)
#define SPM3DS_KEY_B        (1u << 1)
#define SPM3DS_KEY_SELECT   (1u << 2)
#define SPM3DS_KEY_START    (1u << 3)
#define SPM3DS_KEY_DRIGHT   (1u << 4)
#define SPM3DS_KEY_DLEFT    (1u << 5)
#define SPM3DS_KEY_DUP      (1u << 6)
#define SPM3DS_KEY_DDOWN    (1u << 7)
#define SPM3DS_KEY_R        (1u << 8)
#define SPM3DS_KEY_L        (1u << 9)
#define SPM3DS_KEY_X        (1u << 10)
#define SPM3DS_KEY_Y        (1u << 11)

#define SPM3DS_WM_LEFT   0x0001u
#define SPM3DS_WM_RIGHT  0x0002u
#define SPM3DS_WM_DOWN   0x0004u
#define SPM3DS_WM_UP     0x0008u
#define SPM3DS_WM_PLUS   0x0010u
#define SPM3DS_WM_TWO    0x0100u
#define SPM3DS_WM_ONE    0x0200u
#define SPM3DS_WM_B      0x0400u
#define SPM3DS_WM_A      0x0800u
#define SPM3DS_WM_MINUS  0x1000u

/* Same mapping as DEFAULT_MAP in spm3ds/input.py. */
static inline uint32_t spm3ds_wiimote_to_keys(uint32_t wii) {
    uint32_t k = 0;
    if (wii & SPM3DS_WM_LEFT)  k |= SPM3DS_KEY_DLEFT;
    if (wii & SPM3DS_WM_RIGHT) k |= SPM3DS_KEY_DRIGHT;
    if (wii & SPM3DS_WM_UP)    k |= SPM3DS_KEY_DUP;
    if (wii & SPM3DS_WM_DOWN)  k |= SPM3DS_KEY_DDOWN;
    if (wii & SPM3DS_WM_A)     k |= SPM3DS_KEY_A;
    if (wii & SPM3DS_WM_B)     k |= SPM3DS_KEY_B;
    if (wii & SPM3DS_WM_ONE)   k |= SPM3DS_KEY_X;
    if (wii & SPM3DS_WM_TWO)   k |= SPM3DS_KEY_Y;
    if (wii & SPM3DS_WM_PLUS)  k |= SPM3DS_KEY_START;
    if (wii & SPM3DS_WM_MINUS) k |= SPM3DS_KEY_SELECT;
    return k;
}

#endif
