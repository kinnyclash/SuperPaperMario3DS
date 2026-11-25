/* GX command processor in C. See gx_cp.h. MIT License. */
#include "gx_cp.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define VCD_NONE 0u
#define VCD_DIRECT 1u
#define VCD_INDEX8 2u

static uint32_t be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

static uint32_t be16(const uint8_t *p) {
    return ((uint32_t)p[0] << 8) | p[1];
}

static float f32_of(uint32_t w) {
    float f;
    memcpy(&f, &w, 4);
    return f;
}

static uint32_t bitsof(uint32_t v, unsigned shift, unsigned width) {
    return (v >> shift) & ((1u << width) - 1u);
}

void gx_cp_init(gx_cp *g, gx_read_fn read, void *user) {
    memset(g, 0, sizeof *g);
    g->bp_mask = 0xFFFFFFu;
    g->read = read;
    g->read_user = user;
    /* Identity position matrix 0, as in cp.py. */
    g->xf_mem[0] = g->xf_mem[5] = g->xf_mem[10] = 1.0f;
}

void gx_cp_free(gx_cp *g) {
    free(g->out);
    free(g->batches);
    g->out = NULL;
    g->batches = NULL;
    g->out_cap = g->out_count = 0;
    g->batch_cap = g->batch_count = 0;
}

void gx_cp_clear_output(gx_cp *g) {
    g->out_count = 0;
    g->batch_count = 0;
}

/* ---- materials ---- */

static const uint8_t tex_image0_ids[8] = {0x88, 0x89, 0x8A, 0x8B, 0xA8, 0xA9, 0xAA, 0xAB};
static const uint8_t tex_image3_ids[8] = {0x94, 0x95, 0x96, 0x97, 0xB4, 0xB5, 0xB6, 0xB7};
static const uint8_t tex_mode0_ids[8] = {0x80, 0x81, 0x82, 0x83, 0xA0, 0xA1, 0xA2, 0xA3};

static int supported_tex_format(unsigned fmt) {
    return fmt <= 6u || fmt == 14u;
}

/* Material for the next draw, from TEV stage 0 (see gx_cp.h). */
static void current_material(gx_cp *g, gx_batch *m) {
    unsigned order, texmap, i, a, b, c, d;
    uint32_t env;
    int any_tev = 0;
    memset(m, 0, sizeof *m);
    for (i = 0xC0; i <= 0xDF; i++) any_tev |= g->bp_set[i];
    if (!any_tev) return;
    order = g->bp[0x28];
    if (!bitsof(order, 6, 1)) return;               /* stage 0 has no texture */
    texmap = bitsof(order, 0, 3);
    if (!g->bp_set[tex_image0_ids[texmap]] || !g->bp_set[tex_image3_ids[texmap]]) return;
    m->tex_w = bitsof(g->bp[tex_image0_ids[texmap]], 0, 10) + 1u;
    m->tex_h = bitsof(g->bp[tex_image0_ids[texmap]], 10, 10) + 1u;
    m->tex_fmt = bitsof(g->bp[tex_image0_ids[texmap]], 20, 4);
    m->tex_addr = (g->bp[tex_image3_ids[texmap]] & 0xFFFFFFu) << 5;
    m->wrap_s = bitsof(g->bp[tex_mode0_ids[texmap]], 0, 2);
    m->wrap_t = bitsof(g->bp[tex_mode0_ids[texmap]], 2, 2);
    if (!supported_tex_format(m->tex_fmt)) {
        g->unsupported++;
        return;
    }
    m->textured = 1;
    env = g->bp[0xC0];
    a = bitsof(env, 12, 4); b = bitsof(env, 8, 4); c = bitsof(env, 4, 4); d = bitsof(env, 0, 4);
    if (d == 8u && a == 15u && b == 15u && c == 15u) {
        m->modulate = 0;                            /* colour = TEXC */
    } else {
        m->modulate = 1;                            /* TEXC x RASC, or approximated as it */
        if (!(d == 15u && a == 15u && ((b == 8u && c == 10u) || (b == 10u && c == 8u))))
            g->unsupported++;
    }
}

static int same_material(const gx_batch *x, const gx_batch *y) {
    if (x->textured != y->textured) return 0;
    if (!x->textured) return 1;
    return x->modulate == y->modulate && x->tex_addr == y->tex_addr &&
           x->tex_fmt == y->tex_fmt && x->tex_w == y->tex_w && x->tex_h == y->tex_h &&
           x->wrap_s == y->wrap_s && x->wrap_t == y->wrap_t;
}

/* Makes the last batch match the current material, starting a new one if needed. */
static gx_batch *begin_batch(gx_cp *g) {
    gx_batch m;
    current_material(g, &m);
    if (g->batch_count) {
        gx_batch *last = &g->batches[g->batch_count - 1];
        if (same_material(last, &m) && last->first + last->count == g->out_count) return last;
    }
    if (g->batch_count == g->batch_cap) {
        uint32_t cap = g->batch_cap ? g->batch_cap * 2u : 64u;
        gx_batch *p = (gx_batch *)realloc(g->batches, cap * sizeof *p);
        if (!p) return NULL;
        g->batches = p;
        g->batch_cap = cap;
    }
    m.first = g->out_count;
    m.count = 0;
    g->batches[g->batch_count] = m;
    return &g->batches[g->batch_count++];
}

static void fail(gx_cp *g, const char *what, unsigned value) {
    g->error = 1;
    snprintf(g->message, sizeof g->message, "%s 0x%02X", what, value);
}

/* ---- vertex decoding ---- */

typedef struct {
    float pos[3];
    float color[4];
    float uv[2];
    int mtx;               /* -1: use the current matrix index */
} gx_vin;

static const unsigned comp_size[5] = {1, 1, 2, 2, 4};       /* U8, S8, U16, S16, F32 */
static const unsigned color_size[6] = {2, 3, 4, 2, 3, 4};   /* RGB565 .. RGBA8 */

/* Fetches nbytes of an attribute, direct from the stream or through its array. Returns the
 * number of stream bytes used, or -1 on error. */
static int attr_bytes(gx_cp *g, const uint8_t *s, size_t avail, unsigned kind, unsigned array,
                      unsigned nbytes, uint8_t *dst) {
    uint32_t index, used;
    if (kind == VCD_DIRECT) {
        if (avail < nbytes) return -1;
        memcpy(dst, s, nbytes);
        return (int)nbytes;
    }
    if (kind == VCD_INDEX8) {
        if (avail < 1) return -1;
        index = s[0];
        used = 1;
    } else {
        if (avail < 2) return -1;
        index = be16(s);
        used = 2;
    }
    if (!g->read || !g->read(g->read_user, g->cp[0xA0 + array] + index * g->cp[0xB0 + array],
                             dst, nbytes)) {
        memset(dst, 0, nbytes);
        g->unsupported++;
    }
    return (int)used;
}

static void components(const uint8_t *raw, unsigned compsize, unsigned count, unsigned frac,
                       float *out) {
    unsigned i;
    float scale = 1.0f / (float)(1u << frac);
    for (i = 0; i < count; i++) {
        switch (compsize) {
        case 0: out[i] = raw[i] * scale; break;
        case 1: out[i] = (int8_t)raw[i] * scale; break;
        case 2: out[i] = be16(raw + 2 * i) * scale; break;
        case 3: out[i] = (int16_t)be16(raw + 2 * i) * scale; break;
        default: out[i] = f32_of(be32(raw + 4 * i)); break;
        }
    }
}

static unsigned x5(unsigned v) { return (v << 3) | (v >> 2); }
static unsigned x6(unsigned v) { return (v << 2) | (v >> 4); }

static void decode_color(const uint8_t *raw, unsigned fmt, float *rgba) {
    unsigned c[4] = {255, 255, 255, 255}, v, i;
    switch (fmt) {
    case 0: /* RGB565 */
        v = be16(raw);
        c[0] = x5(v >> 11);
        c[1] = x6((v >> 5) & 63u);
        c[2] = x5(v & 31u);
        break;
    case 1: /* RGB8 */
    case 2: /* RGBX8 */
        c[0] = raw[0];
        c[1] = raw[1];
        c[2] = raw[2];
        break;
    case 3: /* RGBA4 */
        v = be16(raw);
        for (i = 0; i < 4; i++) c[i] = ((v >> (12 - 4 * i)) & 15u) * 17u;
        break;
    case 4: /* RGBA6 */
        v = ((unsigned)raw[0] << 16) | ((unsigned)raw[1] << 8) | raw[2];
        for (i = 0; i < 4; i++) c[i] = x6((v >> (18 - 6 * i)) & 63u);
        break;
    default: /* RGBA8 */
        for (i = 0; i < 4; i++) c[i] = raw[i];
        break;
    }
    for (i = 0; i < 4; i++) rgba[i] = c[i] / 255.0f;
}

/* Reads one vertex. Returns stream bytes used, or -1 if the stream ends early. */
static int read_vertex(gx_cp *g, const uint8_t *s, size_t avail, unsigned vat, gx_vin *v) {
    uint32_t lo = g->cp[0x50], hi = g->cp[0x60];
    uint32_t g0 = g->cp[0x70 + vat], g1 = g->cp[0x80 + vat], g2 = g->cp[0x90 + vat];
    size_t pos = 0;
    uint8_t raw[36];
    unsigned i, kind;
    int used;

    v->pos[0] = v->pos[1] = v->pos[2] = 0.0f;
    v->color[0] = v->color[1] = v->color[2] = v->color[3] = 1.0f;
    v->uv[0] = v->uv[1] = 0.0f;
    v->mtx = -1;

    /* PNMTXIDX and TEXnMTXIDX: one direct byte each. */
    for (i = 0; i < 9; i++) {
        if (!bitsof(lo, i, 1)) continue;
        if (pos >= avail) return -1;
        if (i == 0) v->mtx = s[pos] & 0x3F;
        pos++;
    }
    /* POS */
    kind = bitsof(lo, 9, 2);
    if (kind != VCD_NONE) {
        unsigned n = bitsof(g0, 0, 1) ? 3u : 2u, cs = bitsof(g0, 1, 3);
        if (cs > 4) cs = 4;
        used = attr_bytes(g, s + pos, avail - pos, kind, 0, comp_size[cs] * n, raw);
        if (used < 0) return -1;
        pos += (size_t)used;
        components(raw, cs, n, bitsof(g0, 4, 5), v->pos);
    }
    /* NRM: skipped (lighting is not modelled) */
    kind = bitsof(lo, 11, 2);
    if (kind != VCD_NONE) {
        unsigned n = bitsof(g0, 9, 1) ? 9u : 3u, cs = bitsof(g0, 10, 3);
        if (cs > 4) cs = 4;
        used = attr_bytes(g, s + pos, avail - pos, kind, 1, comp_size[cs] * n, raw);
        if (used < 0) return -1;
        pos += (size_t)used;
    }
    /* CLR0, CLR1 */
    for (i = 0; i < 2; i++) {
        unsigned fmt = bitsof(g0, 14 + 4 * i, 3);
        kind = bitsof(lo, 13 + 2 * i, 2);
        if (kind == VCD_NONE) continue;
        if (fmt > 5) fmt = 5;
        used = attr_bytes(g, s + pos, avail - pos, kind, 2 + i, color_size[fmt], raw);
        if (used < 0) return -1;
        pos += (size_t)used;
        if (i == 0) decode_color(raw, fmt, v->color);
    }
    /* TEX0..7: only TEX0 is kept */
    for (i = 0; i < 8; i++) {
        unsigned ct, cs, frac, n;
        kind = bitsof(hi, 2 * i, 2);
        if (kind == VCD_NONE) continue;
        switch (i) {
        case 0: ct = bitsof(g0, 21, 1); cs = bitsof(g0, 22, 3); frac = bitsof(g0, 25, 5); break;
        case 1: ct = bitsof(g1, 0, 1); cs = bitsof(g1, 1, 3); frac = bitsof(g1, 4, 5); break;
        case 2: ct = bitsof(g1, 9, 1); cs = bitsof(g1, 10, 3); frac = bitsof(g1, 13, 5); break;
        case 3: ct = bitsof(g1, 18, 1); cs = bitsof(g1, 19, 3); frac = bitsof(g1, 22, 5); break;
        case 4: ct = bitsof(g1, 27, 1); cs = bitsof(g1, 28, 3); frac = bitsof(g2, 0, 5); break;
        case 5: ct = bitsof(g2, 5, 1); cs = bitsof(g2, 6, 3); frac = bitsof(g2, 9, 5); break;
        case 6: ct = bitsof(g2, 14, 1); cs = bitsof(g2, 15, 3); frac = bitsof(g2, 18, 5); break;
        default: ct = bitsof(g2, 23, 1); cs = bitsof(g2, 24, 3); frac = bitsof(g2, 27, 5); break;
        }
        if (cs > 4) cs = 4;
        n = ct ? 2u : 1u;
        used = attr_bytes(g, s + pos, avail - pos, kind, 4 + i, comp_size[cs] * n, raw);
        if (used < 0) return -1;
        pos += (size_t)used;
        if (i == 0) {
            float uv[2] = {0.0f, 0.0f};
            components(raw, cs, n, frac, uv);
            v->uv[0] = uv[0];
            v->uv[1] = uv[1];
        }
    }
    return (int)pos;
}

/* ---- transform and output ---- */

static int all_set(const gx_cp *g, unsigned first, unsigned count) {
    unsigned i;
    for (i = 0; i < count; i++)
        if (!g->xf_set[first + i]) return 0;
    return 1;
}

static void transform(const gx_cp *g, const gx_vin *in, gx_vertex_out *o) {
    unsigned idx = in->mtx >= 0 ? (unsigned)in->mtx
                 : (g->xf_set[0x18] ? g->xf_regs[0x18] : g->cp[0x30]) & 0x3Fu;
    const float *m = &g->xf_mem[4u * idx];
    float x = in->pos[0], y = in->pos[1], z = in->pos[2];
    float vx = m[0] * x + m[1] * y + m[2] * z + m[3];
    float vy = m[4] * x + m[5] * y + m[6] * z + m[7];
    float vz = m[8] * x + m[9] * y + m[10] * z + m[11];
    if (all_set(g, 0x20, 7)) {
        float p[6];
        unsigned i;
        for (i = 0; i < 6; i++) p[i] = f32_of(g->xf_regs[0x20 + i]);
        if (g->xf_regs[0x26] == 0) {   /* perspective */
            o->x = p[0] * vx + p[1] * vz;
            o->y = p[2] * vy + p[3] * vz;
            o->z = p[4] * vz + p[5];
            o->w = -vz;
        } else {                       /* orthographic */
            o->x = p[0] * vx + p[1];
            o->y = p[2] * vy + p[3];
            o->z = p[4] * vz + p[5];
            o->w = 1.0f;
        }
    } else {
        o->x = vx; o->y = vy; o->z = vz; o->w = 1.0f;
    }
    o->r = in->color[0]; o->g = in->color[1]; o->b = in->color[2]; o->a = in->color[3];
    o->u = in->uv[0]; o->v = in->uv[1];
}

static int push_triangle(gx_cp *g, const gx_vertex_out *a, const gx_vertex_out *b,
                         const gx_vertex_out *c) {
    unsigned cull = bitsof(g->bp_set[0] ? g->bp[0] : (1u << 14), 14, 2); /* default: back */
    float wa = a->w != 0.0f ? a->w : 1e-9f, wb = b->w != 0.0f ? b->w : 1e-9f;
    float wc = c->w != 0.0f ? c->w : 1e-9f;
    float ax = a->x / wa, ay = a->y / wa, bx = b->x / wb, by = b->y / wb;
    float cx = c->x / wc, cy = c->y / wc;
    float area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax); /* NDC, y up */
    if (cull == 3) return 0;
    if (cull == 1 && area < 0.0f) return 0;   /* back faces are clockwise in NDC */
    if (cull == 2 && area > 0.0f) return 0;
    if (g->out_count + 3 > g->out_cap) {
        uint32_t cap = g->out_cap ? g->out_cap * 2u : 768u;
        gx_vertex_out *p = (gx_vertex_out *)realloc(g->out, cap * sizeof *p);
        if (!p) {
            g->unsupported++;
            return 0;
        }
        g->out = p;
        g->out_cap = cap;
    }
    g->out[g->out_count++] = *a;
    g->out[g->out_count++] = *b;
    g->out[g->out_count++] = *c;
    return 1;
}

/* Returns bytes consumed, or 0 if the draw is cut off. */
static size_t draw(gx_cp *g, const uint8_t *d, size_t n) {
    unsigned prim = (d[0] >> 3) & 15u, vat = d[0] & 7u;
    uint32_t count, k;
    size_t pos = 3;
    gx_vertex_out *v;
    gx_batch *batch;
    if (n < 3) return 0;
    count = be16(d + 1);
    if (prim == 1) prim = 0;
    v = (gx_vertex_out *)malloc((count ? count : 1) * sizeof *v);
    if (!v) {
        fail(g, "out of memory in draw", d[0]);
        return n;
    }
    for (k = 0; k < count; k++) {
        gx_vin in;
        int used = read_vertex(g, d + pos, n - pos, vat, &in);
        if (used < 0) {
            free(v);
            return 0;
        }
        pos += (size_t)used;
        transform(g, &in, &v[k]);
    }
    batch = begin_batch(g);
    switch (prim) {
    case 0: for (k = 0; k + 3 < count; k += 4) {
                push_triangle(g, &v[k], &v[k + 1], &v[k + 2]);
                push_triangle(g, &v[k], &v[k + 2], &v[k + 3]);
            }
            break;
    case 2: for (k = 0; k + 2 < count; k += 3) push_triangle(g, &v[k], &v[k + 1], &v[k + 2]); break;
    case 3: for (k = 0; k + 2 < count; k++) {
                if (k % 2 == 0) push_triangle(g, &v[k], &v[k + 1], &v[k + 2]);
                else push_triangle(g, &v[k + 1], &v[k], &v[k + 2]);
            }
            break;
    case 4: for (k = 1; k + 1 < count; k++) push_triangle(g, &v[0], &v[k], &v[k + 1]); break;
    default: g->unsupported++; break;   /* lines, line strips, points */
    }
    if (batch) batch->count = g->out_count - batch->first;
    free(v);
    return pos;
}

static void bp_write(gx_cp *g, uint32_t word) {
    unsigned reg = word >> 24;
    uint32_t value = word & 0xFFFFFFu, mask;
    if (reg == 0xFE) {
        g->bp_mask = value;
        return;
    }
    mask = g->bp_mask;
    g->bp_mask = 0xFFFFFFu;
    g->bp[reg] = (g->bp[reg] & ~mask & 0xFFFFFFu) | (value & mask);
    g->bp_set[reg] = 1;
}

static void xf_write(gx_cp *g, uint32_t addr, uint32_t word) {
    if (addr < 0x1000u) g->xf_mem[addr] = f32_of(word);
    else if (addr - 0x1000u < 0x100u) {
        g->xf_regs[addr - 0x1000u] = word;
        g->xf_set[addr - 0x1000u] = 1;
    }
}

static size_t run_depth(gx_cp *g, const uint8_t *d, size_t n, int depth) {
    size_t pos = 0;
    while (pos < n && !g->error) {
        uint8_t op = d[pos];
        size_t left = n - pos;
        if (op == 0x00) { pos++; g->commands++; continue; }
        if (op == 0x08) {
            if (left < 6) break;
            g->cp[d[pos + 1]] = be32(d + pos + 2);
            pos += 6;
        } else if (op == 0x10) {
            uint32_t head, count, base, i;
            if (left < 5) break;
            head = be32(d + pos + 1);
            count = (head >> 16) + 1u;
            base = head & 0xFFFFu;
            if (left < 5u + 4u * count) break;
            for (i = 0; i < count; i++) xf_write(g, base + i, be32(d + pos + 5 + 4 * i));
            pos += 5u + 4u * count;
        } else if (op == 0x20 || op == 0x28 || op == 0x30 || op == 0x38) {
            if (left < 5) break;
            g->unsupported++;
            pos += 5;
        } else if (op == 0x48) {
            uint32_t addr, size;
            if (left < 9) break;
            addr = be32(d + pos + 1);
            size = be32(d + pos + 5);
            pos += 9;
            if (depth == 0 && g->read && size) {
                uint8_t *list = (uint8_t *)malloc(size);
                if (list && g->read(g->read_user, addr, list, size)) run_depth(g, list, size, 1);
                else g->unsupported++;
                free(list);
            } else {
                g->unsupported++;
            }
        } else if (op == 0x61) {
            if (left < 5) break;
            bp_write(g, be32(d + pos + 1));
            pos += 5;
        } else if (op >= 0x80 && op <= 0xBF) {
            size_t used = draw(g, d + pos, left);
            if (used == 0) break;
            pos += used;
        } else {
            fail(g, "unknown GX command", op);
            break;
        }
        g->commands++;
    }
    return pos;
}

size_t gx_cp_run(gx_cp *g, const uint8_t *data, size_t n) {
    return run_depth(g, data, n, 0);
}
