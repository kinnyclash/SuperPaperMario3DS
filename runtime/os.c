/* Wii OS compatibility layer: guest heap, timers, and a sandboxed file reader.
 *
 * Heap: the allocator keeps all its state in guest memory, so guest pointers stay valid on the
 * host and on the 3DS. The heap is one contiguous range of blocks. Each block has a 32-byte
 * header (size, used flag) and a 32-byte-aligned payload. Allocation is first-fit. Freeing
 * merges neighbouring free blocks. There is no per-thread cache and no locking.
 *
 * Timers: OSGetTick and OSGetTime return time in Wii timebase ticks. Host time comes from a
 * clock source the platform sets with spm3ds_set_clock. The default is CPU time from clock(),
 * which is not wall time, so a real build must set a wall clock.
 *
 * Files: spm3ds_FSOpen and friends read files under one root directory. Paths that are
 * absolute, contain "..", or contain ':' are refused.
 *
 * MIT License. Written from the public documentation of these functions, not from SDK source. */
#include "hle.h"

#include <stdio.h>
#include <string.h>
#include <time.h>

/* ---- heap ------------------------------------------------------------------------- */

#define OS_HEAP_MAGIC 0x48454150u /* "HEAP" */
#define OS_BLOCK_HDR 32u
#define OS_ALIGN 32u

/* Heap header layout (guest memory, at the heap handle): magic, end, first block. */
#define HEAP_MAGIC_OFF 0u
#define HEAP_END_OFF 4u
#define HEAP_FIRST_OFF 8u

static uint32_t align_up(uint32_t x, uint32_t a) {
    return (x + a - 1u) & ~(a - 1u);
}

/* OSCreateHeap(lo, hi): returns the heap handle, or 0 if the range is too small. */
void hle_OSCreateHeap(ppc_ctx *c) {
    uint32_t lo = c->r[3], hi = c->r[4];
    uint32_t first = align_up(lo + 32u, OS_ALIGN);
    uint32_t size;
    if (hi <= first || hi - first < OS_BLOCK_HDR * 2u) {
        c->r[3] = 0;
        return;
    }
    size = (hi - first) & ~(OS_ALIGN - 1u);
    ppc_w32(c, lo + HEAP_MAGIC_OFF, OS_HEAP_MAGIC);
    ppc_w32(c, lo + HEAP_END_OFF, hi);
    ppc_w32(c, lo + HEAP_FIRST_OFF, first);
    ppc_w32(c, first, size);      /* one free block covering the whole range */
    ppc_w32(c, first + 4u, 0u);
    c->r[3] = lo;
}

static int heap_valid(ppc_ctx *c, uint32_t heap) {
    return ppc_r32(c, heap + HEAP_MAGIC_OFF) == OS_HEAP_MAGIC;
}

/* OSAllocFromHeap(heap, size): returns the payload address, or 0. */
void hle_OSAllocFromHeap(ppc_ctx *c) {
    uint32_t heap = c->r[3], size = c->r[4];
    uint32_t end, blk, need;
    c->r[3] = 0;
    if (!heap_valid(c, heap) || size == 0 || size > 0x7FFFFFE0u) return;
    end = ppc_r32(c, heap + HEAP_END_OFF);
    blk = ppc_r32(c, heap + HEAP_FIRST_OFF);
    need = align_up(size + OS_BLOCK_HDR, OS_ALIGN);
    while (blk + OS_BLOCK_HDR <= end) {
        uint32_t bsize = ppc_r32(c, blk);
        uint32_t used = ppc_r32(c, blk + 4u);
        if (bsize < OS_BLOCK_HDR || bsize > end - blk) return; /* corrupt heap: fail the call */
        if (!used && bsize >= need) {
            if (bsize - need >= 2u * OS_BLOCK_HDR) {
                ppc_w32(c, blk + need, bsize - need);  /* split off the remainder */
                ppc_w32(c, blk + need + 4u, 0u);
                bsize = need;
            }
            ppc_w32(c, blk, bsize);
            ppc_w32(c, blk + 4u, 1u);
            c->r[3] = blk + OS_BLOCK_HDR;
            return;
        }
        blk += bsize;
    }
}

/* OSFreeToHeap(heap, ptr): returns 0 on success, -1 if ptr is not a used block. */
void hle_OSFreeToHeap(ppc_ctx *c) {
    uint32_t heap = c->r[3], ptr = c->r[4];
    uint32_t end, first, blk;
    c->r[3] = 0xFFFFFFFFu;
    if (!heap_valid(c, heap) || ptr < OS_BLOCK_HDR) return;
    end = ppc_r32(c, heap + HEAP_END_OFF);
    first = ppc_r32(c, heap + HEAP_FIRST_OFF);
    blk = ptr - OS_BLOCK_HDR;
    if (blk < first || blk >= end || ((blk - first) % OS_ALIGN) != 0u) return;
    if (ppc_r32(c, blk + 4u) != 1u) return;
    ppc_w32(c, blk + 4u, 0u);

    /* Merge runs of free blocks. The walk stops at the first corrupt header. */
    blk = first;
    while (blk + OS_BLOCK_HDR <= end) {
        uint32_t bsize = ppc_r32(c, blk);
        if (bsize < OS_BLOCK_HDR || bsize > end - blk) break;
        if (ppc_r32(c, blk + 4u) == 0u) {
            uint32_t next = blk + bsize;
            while (next + OS_BLOCK_HDR <= end && ppc_r32(c, next + 4u) == 0u) {
                uint32_t nsize = ppc_r32(c, next);
                if (nsize < OS_BLOCK_HDR || nsize > end - next) break;
                bsize += nsize;
                next = blk + bsize;
            }
            ppc_w32(c, blk, bsize);
        }
        blk += bsize;
    }
    c->r[3] = 0;
}

/* ---- timers ----------------------------------------------------------------------- */

/* Wii timebase: 60.75 MHz, so ticks = ns * 243 / 4000. The constant is taken from public
 * documentation and must be verified against hardware measurements. */
#define OS_TB_NUM 243u
#define OS_TB_DEN 4000u

static uint64_t default_now_ns(void) {
    return (uint64_t)clock() * (1000000000ull / CLOCKS_PER_SEC);
}

static uint64_t (*now_ns)(void) = default_now_ns;

void spm3ds_set_clock(uint64_t (*source_ns)(void)) {
    now_ns = source_ns ? source_ns : default_now_ns;
}

static uint64_t timebase(void) {
    uint64_t ns = now_ns();
    /* Split to avoid overflow: ns * 243 can exceed 64 bits after long uptimes. */
    return (ns / OS_TB_DEN) * OS_TB_NUM + ((ns % OS_TB_DEN) * OS_TB_NUM) / OS_TB_DEN;
}

/* OSGetTick(): low 32 bits of the timebase. */
void hle_OSGetTick(ppc_ctx *c) {
    c->r[3] = (uint32_t)timebase();
}

/* OSGetTime(): full 64-bit timebase, returned in r3 (high) and r4 (low). */
void hle_OSGetTime(ppc_ctx *c) {
    uint64_t t = timebase();
    c->r[3] = (uint32_t)(t >> 32);
    c->r[4] = (uint32_t)t;
}

/* ---- files ------------------------------------------------------------------------ */

#define FS_MAX_HANDLES 8
#define FS_PATH_MAX 256
#define FS_CHUNK 4096

static FILE *fs_files[FS_MAX_HANDLES];
static char fs_root[FS_PATH_MAX] = ".";

void spm3ds_fs_set_root(const char *dir) {
    strncpy(fs_root, dir, sizeof fs_root - 1);
    fs_root[sizeof fs_root - 1] = '\0';
}

static int path_is_safe(const char *p) {
    if (p[0] == '\0' || p[0] == '/' || p[0] == '\\') return 0;
    if (strchr(p, ':') || strstr(p, "..")) return 0;
    return 1;
}

/* spm3ds_FSOpen(path): path is a guest string relative to the root. Returns a handle, or
 * 0xFFFFFFFF on failure. */
void hle_spm3ds_FSOpen(ppc_ctx *c) {
    char path[FS_PATH_MAX], full[FS_PATH_MAX * 2];
    uint32_t ea = c->r[3];
    unsigned i;
    size_t n = 0;
    c->r[3] = 0xFFFFFFFFu;
    while (n < sizeof path - 1u) {
        uint32_t ch = ppc_r8(c, ea + (uint32_t)n);
        if (ch == 0) break;
        path[n++] = (char)ch;
    }
    path[n] = '\0';
    if (ppc_r8(c, ea + (uint32_t)n) != 0 || !path_is_safe(path)) return;
    snprintf(full, sizeof full, "%s/%s", fs_root, path);
    for (i = 0; i < FS_MAX_HANDLES; i++) {
        if (!fs_files[i]) {
            fs_files[i] = fopen(full, "rb");
            if (fs_files[i]) c->r[3] = i;
            return;
        }
    }
}

/* spm3ds_FSSize(handle): file length in bytes, or 0 for a bad handle. */
void hle_spm3ds_FSSize(ppc_ctx *c) {
    uint32_t h = c->r[3];
    long size = 0;
    c->r[3] = 0;
    if (h >= FS_MAX_HANDLES || !fs_files[h]) return;
    if (fseek(fs_files[h], 0, SEEK_END) == 0) size = ftell(fs_files[h]);
    c->r[3] = size > 0 ? (uint32_t)size : 0u;
}

/* spm3ds_FSRead(handle, dst, offset, n): copies up to n bytes into guest memory. Returns the
 * number of bytes read. */
void hle_spm3ds_FSRead(ppc_ctx *c) {
    uint32_t h = c->r[3], dst = c->r[4], offset = c->r[5], want = c->r[6];
    uint32_t total = 0;
    unsigned char buf[FS_CHUNK];
    c->r[3] = 0;
    if (h >= FS_MAX_HANDLES || !fs_files[h]) return;
    if (fseek(fs_files[h], (long)offset, SEEK_SET) != 0) return;
    while (total < want) {
        uint32_t chunk = want - total < FS_CHUNK ? want - total : FS_CHUNK;
        size_t got = fread(buf, 1, chunk, fs_files[h]);
        uint32_t k;
        for (k = 0; k < got; k++) ppc_w8(c, dst + total + k, buf[k]);
        total += (uint32_t)got;
        if (got < chunk) break;
    }
    c->r[3] = total;
}

/* spm3ds_FSClose(handle). */
void hle_spm3ds_FSClose(ppc_ctx *c) {
    uint32_t h = c->r[3];
    c->r[3] = 0;
    if (h >= FS_MAX_HANDLES || !fs_files[h]) return;
    fclose(fs_files[h]);
    fs_files[h] = NULL;
}
