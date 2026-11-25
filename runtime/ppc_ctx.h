/* Runtime context and helpers shared by generated guest code, host tests and the 3DS port.
 * Each helper mirrors a Python definition in spm3ds/ppc.py; keep them in step.
 * MIT License, see LICENSE. Contains no Nintendo code. */
#ifndef SPM3DS_PPC_CTX_H
#define SPM3DS_PPC_CTX_H

#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PPC_MAX_REGIONS 8
/* Halfway between FLT_MAX and 2^128: doubles at or above this round to infinity as single. */
#define PPC_SINGLE_OVERFLOW ((2.0 - 0x1p-23) * 0x1p127 + 0x1p103)

/* Guest memory is a list of regions. A region is flat (data holds size bytes) or paged
 * (pages holds size/PPC_PAGE_SIZE pointers, and each page is allocated on first touch). */
#define PPC_PAGE_SHIFT 16
#define PPC_PAGE_SIZE (1u << PPC_PAGE_SHIFT)

typedef struct ppc_region {
    uint32_t base;
    uint32_t size;
    uint8_t *data;       /* flat region, or NULL when paged */
    uint8_t **pages;     /* paged region: one pointer per page, NULL until touched */
    uint32_t pages_used; /* paged region: number of allocated pages */
} ppc_region;

typedef struct ppc_ctx {
    uint32_t r[32];
    uint64_t f[32];      /* FPR bit patterns; ppc_d() and ppc_b() convert */
    uint32_t lr, ctr, cr;
    uint32_t xer;        /* SO (bit 31), OV (bit 30) and CA (bit 29) are modelled */
    uint32_t msr;        /* stored only; interrupts are not modelled */
    uint32_t fpscr;      /* stored and moved; arithmetic does not set exception bits */
    uint64_t tb;         /* time base read by mftb; the platform layer advances it */
    uint32_t spr[1024];  /* SPRs without their own field (plain storage) */
    uint32_t gqr[8];     /* graphics quantization registers (SPRs 912..919) */
    uint32_t pc;         /* address of the last instruction started (PPC_DIAG) */
    uint64_t icount;     /* translated instructions executed (PPC_DIAG) */
    ppc_region regions[PPC_MAX_REGIONS];
    unsigned nregions;
} ppc_ctx;

typedef void (*ppc_fn)(ppc_ctx *);

/* ---- memory ---- */

/* Returns a pointer to n contiguous guest bytes at ea, allocating the page if needed, or NULL
 * when the bytes are not mapped or would cross a page boundary of a paged region. */
static inline uint8_t *ppc_try(ppc_ctx *c, uint32_t ea, uint32_t n) {
    unsigned i;
    for (i = 0; i < c->nregions; i++) {
        ppc_region *g = &c->regions[i];
        uint32_t off = ea - g->base;
        uint32_t pi, po;
        if (off >= g->size || g->size - off < n) continue;
        if (!g->pages) return g->data + off;
        pi = off >> PPC_PAGE_SHIFT;
        po = off & (PPC_PAGE_SIZE - 1u);
        if (po + n > PPC_PAGE_SIZE) return NULL;
        if (!g->pages[pi]) {
            g->pages[pi] = (uint8_t *)calloc(PPC_PAGE_SIZE, 1);
            if (!g->pages[pi]) {
                fprintf(stderr, "out of host memory allocating a guest page at %08x\n", (unsigned)ea);
                abort();
            }
            g->pages_used++;
        }
        return g->pages[pi] + po;
    }
    return NULL;
}

/* Crash diagnostics: prints the guest state, then the caller aborts. Reads only the context, so
 * it links with any program. Committed memory counts flat regions in full and paged regions by
 * their allocated pages. */
static inline void ppc_crash_report(ppc_ctx *c, const char *why) {
    unsigned i, j;
    uint64_t committed = 0;
    for (i = 0; i < c->nregions; i++) {
        ppc_region *g = &c->regions[i];
        committed += g->data ? g->size : (uint64_t)g->pages_used * PPC_PAGE_SIZE;
    }
    fprintf(stderr, "\n=== spm3ds guest crash: %s ===\n", why);
    fprintf(stderr, "pc=0x%08X lr=0x%08X ctr=0x%08X cr=0x%08X xer=0x%08X\n",
            (unsigned)c->pc, (unsigned)c->lr, (unsigned)c->ctr, (unsigned)c->cr, (unsigned)c->xer);
    for (i = 0; i < 32; i += 4) {
        for (j = i; j < i + 4; j++) fprintf(stderr, "r%-2u=%08X  ", j, (unsigned)c->r[j]);
        fprintf(stderr, "\n");
    }
    fprintf(stderr, "instructions executed=%llu  guest memory committed=%llu bytes in %u region(s)\n",
            (unsigned long long)c->icount, (unsigned long long)committed, c->nregions);
}

static inline uint8_t *ppc_ptr(ppc_ctx *c, uint32_t ea, uint32_t n) {
    uint8_t *p = ppc_try(c, ea, n);
    if (!p) {
        fprintf(stderr, "guest memory fault: %u bytes at %08x\n", (unsigned)n, (unsigned)ea);
        ppc_crash_report(c, "unmapped or page-crossing access");
        abort();
    }
    return p;
}

/* Emitted before each translated instruction. Define SPM3DS_NO_DIAG to remove the cost. */
#ifdef SPM3DS_NO_DIAG
#  define PPC_DIAG(addr) ((void)0)
#else
#  define PPC_DIAG(addr) do { c->pc = (addr); c->icount++; } while (0)
#endif

static inline uint32_t ppc_r8(ppc_ctx *c, uint32_t ea) {
    return *ppc_ptr(c, ea, 1);
}

static inline uint32_t ppc_r16(ppc_ctx *c, uint32_t ea) {
    uint8_t *p = ppc_try(c, ea, 2);
    if (!p) return (ppc_r8(c, ea) << 8) | ppc_r8(c, ea + 1);
    return ((uint32_t)p[0] << 8) | (uint32_t)p[1];
}

static inline uint32_t ppc_r32(ppc_ctx *c, uint32_t ea) {
    uint8_t *p = ppc_try(c, ea, 4);
    if (!p) {
        return (ppc_r8(c, ea) << 24) | (ppc_r8(c, ea + 1) << 16) |
               (ppc_r8(c, ea + 2) << 8) | ppc_r8(c, ea + 3);
    }
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static inline uint64_t ppc_r64(ppc_ctx *c, uint32_t ea) {
    return ((uint64_t)ppc_r32(c, ea) << 32) | (uint64_t)ppc_r32(c, ea + 4);
}

static inline void ppc_w8(ppc_ctx *c, uint32_t ea, uint32_t v) {
    *ppc_ptr(c, ea, 1) = (uint8_t)v;
}

static inline void ppc_w16(ppc_ctx *c, uint32_t ea, uint32_t v) {
    uint8_t *p = ppc_try(c, ea, 2);
    if (!p) {
        ppc_w8(c, ea, v >> 8);
        ppc_w8(c, ea + 1, v);
        return;
    }
    p[0] = (uint8_t)(v >> 8);
    p[1] = (uint8_t)v;
}

static inline void ppc_w32(ppc_ctx *c, uint32_t ea, uint32_t v) {
    uint8_t *p = ppc_try(c, ea, 4);
    if (!p) {
        ppc_w8(c, ea, v >> 24);
        ppc_w8(c, ea + 1, v >> 16);
        ppc_w8(c, ea + 2, v >> 8);
        ppc_w8(c, ea + 3, v);
        return;
    }
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static inline void ppc_w64(ppc_ctx *c, uint32_t ea, uint64_t v) {
    ppc_w32(c, ea, (uint32_t)(v >> 32));
    ppc_w32(c, ea + 4, (uint32_t)v);
}

/* Paged regions (runtime/mem.c). ppc_map_paged maps [base, base+size) with no pages
 * allocated; size must be a multiple of PPC_PAGE_SIZE. Returns 0 on success. */
int ppc_map_paged(ppc_ctx *c, uint32_t base, uint32_t size);
/* Bytes of host memory currently committed to paged regions. */
size_t ppc_committed_bytes(const ppc_ctx *c);
/* Frees all pages and page tables of paged regions. */
void ppc_unmap_paged(ppc_ctx *c);

/* ---- floating point ---- */

static inline double ppc_d(uint64_t bits) {
    double d;
    memcpy(&d, &bits, 8);
    return d;
}

static inline uint64_t ppc_b(double d) {
    uint64_t bits;
    memcpy(&bits, &d, 8);
    return bits;
}

static inline double ppc_round_single(double x) {
    if (x != x) return x;
    if (fabs(x) >= PPC_SINGLE_OVERFLOW) return copysign(INFINITY, x);
    return (double)(float)x;
}

static inline uint32_t ppc_single_bits(uint64_t bits) {
    float f = (float)ppc_round_single(ppc_d(bits));
    uint32_t v;
    memcpy(&v, &f, 4);
    return v;
}

static inline uint64_t ppc_from_single_bits(uint32_t v) {
    float f;
    memcpy(&f, &v, 4);
    return ppc_b((double)f);
}

static inline uint64_t ppc_fctiwz(double x) {
    int32_t v;
    if (x != x) v = INT32_MIN;
    else if (x >= 2147483648.0) v = INT32_MAX;
    else if (x < -2147483648.0) v = INT32_MIN;
    else v = (int32_t)x;
    return 0xFFF8000000000000ull | (uint32_t)v;
}

/* Exact reciprocal and reciprocal square root (hardware returns estimates; see ppc_ext.py). */
static inline double ppc_fres(double b) {
    return ppc_round_single(1.0 / b);
}

static inline double ppc_frsqrte(double b) {
    if (b != b) return b;
    if (b < 0.0) return ppc_d(0x7FF8000000000000ull);
    if (b == 0.0) return copysign(INFINITY, b);
    if (isinf(b)) return 0.0;
    return 1.0 / sqrt(b);
}

/* Round to nearest even (FPSCR RN is not modelled), saturate, NaN -> INT32_MIN. */
static inline uint64_t ppc_fctiw(double x) {
    int32_t v;
    double r;
    if (x != x) v = INT32_MIN;
    else {
        r = rint(x);
        if (r >= 2147483648.0) v = INT32_MAX;
        else if (r < -2147483648.0) v = INT32_MIN;
        else v = (int32_t)r;
    }
    return 0xFFF8000000000000ull | (uint32_t)v;
}

static inline uint32_t ppc_fcmp(uint32_t cr, unsigned crf, double a, double b) {
    unsigned shift = 28u - 4u * crf;
    uint32_t field;
    if (a != a || b != b) field = 1u;
    else field = a < b ? 8u : (a > b ? 4u : 2u);
    return (cr & ~(0xFu << shift)) | (field << shift);
}

/* ---- integer ---- */

/* Integer compares copy XER.SO (bit 31 of xer) into the low bit of the CR field. */
static inline uint32_t ppc_cmp_s(uint32_t cr, unsigned crf, int32_t a, int32_t b, uint32_t xer) {
    unsigned shift = 28u - 4u * crf;
    uint32_t field = (a < b ? 8u : (a > b ? 4u : 2u)) | (xer >> 31);
    return (cr & ~(0xFu << shift)) | (field << shift);
}

static inline uint32_t ppc_cmp_u(uint32_t cr, unsigned crf, uint32_t a, uint32_t b, uint32_t xer) {
    unsigned shift = 28u - 4u * crf;
    uint32_t field = (a < b ? 8u : (a > b ? 4u : 2u)) | (xer >> 31);
    return (cr & ~(0xFu << shift)) | (field << shift);
}

/* ---- CR and FPSCR bit and field access (bit 0 is the most significant) ---- */

static inline uint32_t ppc_crbit(uint32_t cr, unsigned bit) {
    return (cr >> (31u - bit)) & 1u;
}

static inline uint32_t ppc_set_crbit(uint32_t cr, unsigned bit, uint32_t v) {
    unsigned sh = 31u - bit;
    return (cr & ~(1u << sh)) | ((v & 1u) << sh);
}

static inline uint32_t ppc_set_crfield(uint32_t cr, unsigned field, uint32_t v) {
    unsigned sh = 28u - 4u * field;
    return (cr & ~(0xFu << sh)) | ((v & 0xFu) << sh);
}

/* ---- overflow (XER.OV bit 30, sticky XER.SO bit 31) ---- */

static inline void ppc_set_ov(ppc_ctx *c, uint32_t ov) {
    if (ov) c->xer |= 0xC0000000u;
    else c->xer &= ~0x40000000u;
}

/* ---- byte reversal, cache block zero ---- */

static inline uint32_t ppc_bswap16(uint32_t v) {
    return ((v & 0xFFu) << 8) | ((v >> 8) & 0xFFu);
}

static inline uint32_t ppc_bswap32(uint32_t v) {
    return (v << 24) | ((v & 0xFF00u) << 8) | ((v >> 8) & 0xFF00u) | (v >> 24);
}

static inline void ppc_dcbz(ppc_ctx *c, uint32_t ea) {
    uint32_t i;
    ea &= ~31u;
    for (i = 0; i < 32u; i += 4u) ppc_w32(c, ea + i, 0u);
}

static inline uint32_t ppc_rotl32(uint32_t x, uint32_t n) {
    n &= 31u;
    return n ? ((x << n) | (x >> (32u - n))) : x;
}

static inline uint32_t ppc_slw(uint32_t a, uint32_t b) {
    uint32_t n = b & 63u;
    return (n & 32u) ? 0u : (a << n);
}

static inline uint32_t ppc_srw(uint32_t a, uint32_t b) {
    uint32_t n = b & 63u;
    return (n & 32u) ? 0u : (a >> n);
}

static inline uint32_t ppc_sraw(uint32_t a, uint32_t b) {
    uint32_t n = b & 63u;
    int32_t x = (int32_t)a;
    if (n >= 32u) return x < 0 ? 0xFFFFFFFFu : 0u;
    return (uint32_t)(x >> n);
}

static inline uint32_t ppc_divw(uint32_t a, uint32_t b) {
    int32_t x = (int32_t)a, y = (int32_t)b;
    if (y == 0 || (x == INT32_MIN && y == -1)) return 0u;
    return (uint32_t)(x / y);
}

static inline uint32_t ppc_divwu(uint32_t a, uint32_t b) {
    return b == 0u ? 0u : a / b;
}

static inline uint32_t ppc_cntlzw(uint32_t x) {
    return x ? (uint32_t)__builtin_clz(x) : 32u;
}

static inline uint32_t ppc_extsb(uint32_t x) {
    return (uint32_t)(int32_t)(int8_t)(uint8_t)x;
}

static inline uint32_t ppc_extsh(uint32_t x) {
    return (uint32_t)(int32_t)(int16_t)(uint16_t)x;
}

/* ---- carry (XER.CA, bit 29) ---- */

static inline uint32_t ppc_ca(const ppc_ctx *c) {
    return (c->xer >> 29) & 1u;
}

static inline void ppc_set_ca(ppc_ctx *c, uint32_t carry) {
    c->xer = (c->xer & ~0x20000000u) | ((carry & 1u) << 29);
}

/* a + b + cin, setting CA to the carry out. */
static inline uint32_t ppc_addx(ppc_ctx *c, uint32_t a, uint32_t b, uint32_t cin) {
    uint64_t s = (uint64_t)a + (uint64_t)b + (uint64_t)cin;
    ppc_set_ca(c, (uint32_t)(s >> 32));
    return (uint32_t)s;
}

/* Arithmetic right shift by n (the low 6 bits of n, as sraw uses). CA is set when a
 * negative value loses one or more 1 bits. */
static inline uint32_t ppc_sraw_ca(ppc_ctx *c, uint32_t a, uint32_t n) {
    uint32_t n6 = n & 63u;
    int32_t x = (int32_t)a;
    uint32_t value, lost_bits, carry;
    if (n6 >= 32u) {
        value = x < 0 ? 0xFFFFFFFFu : 0u;
        carry = (x < 0 && a != 0u) ? 1u : 0u;
    } else {
        value = (uint32_t)(x >> n6);
        lost_bits = a & ((1u << n6) - 1u);
        carry = (x < 0 && lost_bits != 0u) ? 1u : 0u;
    }
    ppc_set_ca(c, carry);
    return value;
}

/* ---- multiply high ---- */

static inline uint32_t ppc_mulhw(uint32_t a, uint32_t b) {
    int64_t p = (int64_t)(int32_t)a * (int64_t)(int32_t)b;
    return (uint32_t)((uint64_t)p >> 32);
}

static inline uint32_t ppc_mulhwu(uint32_t a, uint32_t b) {
    uint64_t p = (uint64_t)a * (uint64_t)b;
    return (uint32_t)(p >> 32);
}

/* ---- paired singles ----
 * A paired-single FPR holds two 32-bit single-precision patterns: high word = ps0, low word = ps1. */

/* Value of one single-precision pattern as a double (exact). */
static inline double ppc_s_d(uint32_t bits) {
    float f;
    memcpy(&f, &bits, 4);
    return (double)f;
}

/* Rounds a double to single precision (round-to-nearest) and returns its pattern. */
static inline uint32_t ppc_s_b(double d) {
    float f = (float)ppc_round_single(d);
    uint32_t v;
    memcpy(&v, &f, 4);
    return v;
}

/* Quantization (GQR types 4..7). Mirrors spm3ds/quant.py exactly: loads divide by 2^scale,
 * stores multiply by 2^scale, store truncates toward zero, saturates, and NaN stores 0. */
static inline int32_t ppc_sext6(uint32_t v) {
    v &= 63u;
    return (v & 32u) ? (int32_t)v - 64 : (int32_t)v;
}

static inline double ppc_dequant(uint32_t raw, unsigned size, int is_signed, int32_t scale) {
    int64_t v = (int64_t)raw;
    if (is_signed && (raw & (1u << (size * 8 - 1)))) v -= (int64_t)1 << (size * 8);
    return (double)v * ldexp(1.0, -scale);
}

static inline uint32_t ppc_quant(double x, unsigned size, int is_signed, int32_t scale) {
    double lo, hi, y;
    if (x != x) return 0u;
    if (is_signed) {
        lo = -(double)((int64_t)1 << (size * 8 - 1));
        hi = (double)(((int64_t)1 << (size * 8 - 1)) - 1);
    } else {
        lo = 0.0;
        hi = (double)(((int64_t)1 << (size * 8)) - 1);
    }
    y = x * ldexp(1.0, scale);
    if (y < lo) y = lo;
    if (y > hi) y = hi;
    return (uint32_t)(int64_t)y & (uint32_t)((((int64_t)1 << (size * 8)) - 1));
}

/* psq_l: returns the paired-single FPR value for the load at ea, using GQR number idx. */
static inline uint64_t ppc_psq_load(ppc_ctx *c, uint32_t ea, unsigned idx, int w) {
    uint32_t g = c->gqr[idx & 7u];
    uint32_t kind = (g >> 16) & 7u;
    int32_t scale = ppc_sext6(g >> 24);
    uint32_t hi, lo;
    if (kind == 0u) {
        hi = ppc_r32(c, ea);
        lo = w ? 0x3F800000u : ppc_r32(c, ea + 4u);
    } else {
        unsigned size = (kind == 4u || kind == 6u) ? 1u : 2u;
        int is_signed = kind >= 6u;
        if (kind < 4u || kind > 7u) {
            fprintf(stderr, "psq_l: load type %u is reserved\n", (unsigned)kind);
            abort();
        }
        hi = ppc_s_b(ppc_dequant(size == 1u ? ppc_r8(c, ea) : ppc_r16(c, ea), size, is_signed, scale));
        if (w) {
            lo = 0x3F800000u;
        } else {
            uint32_t raw = size == 1u ? ppc_r8(c, ea + 1u) : ppc_r16(c, ea + 2u);
            lo = ppc_s_b(ppc_dequant(raw, size, is_signed, scale));
        }
    }
    return ((uint64_t)hi << 32) | lo;
}

/* psq_st: stores the paired-single value f at ea, using GQR number idx. */
static inline void ppc_psq_store(ppc_ctx *c, uint32_t ea, uint64_t f, unsigned idx, int w) {
    uint32_t g = c->gqr[idx & 7u];
    uint32_t kind = g & 7u;
    int32_t scale = ppc_sext6(g >> 8);
    if (kind == 0u) {
        ppc_w32(c, ea, (uint32_t)(f >> 32));
        if (!w) ppc_w32(c, ea + 4u, (uint32_t)f);
        return;
    }
    if (kind < 4u || kind > 7u) {
        fprintf(stderr, "psq_st: store type %u is reserved\n", (unsigned)kind);
        abort();
    }
    {
        unsigned size = (kind == 4u || kind == 6u) ? 1u : 2u;
        int is_signed = kind >= 6u;
        uint32_t q0 = ppc_quant(ppc_s_d((uint32_t)(f >> 32)), size, is_signed, scale);
        if (size == 1u) ppc_w8(c, ea, q0); else ppc_w16(c, ea, q0);
        if (!w) {
            uint32_t q1 = ppc_quant(ppc_s_d((uint32_t)f), size, is_signed, scale);
            if (size == 1u) ppc_w8(c, ea + 1u, q1); else ppc_w16(c, ea + 2u, q1);
        }
    }
}

#endif /* SPM3DS_PPC_CTX_H */
