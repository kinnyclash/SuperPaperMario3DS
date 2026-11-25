/* HLE implementations. Written from the public behaviour of these SDK-style functions.
 * MIT License. */
#include "hle.h"

#include <stdarg.h>

/* Log output: stdout is the 3DS console (libctru redirects it), stderr on the host. */
#ifdef __3DS__
#define SPM_LOG stdout
#else
#define SPM_LOG stderr
#endif

/* Read a NUL-terminated string from guest memory into a host buffer. */
static size_t guest_strlen(ppc_ctx *c, uint32_t ea) {
    size_t n = 0;
    while (ppc_r8(c, ea + (uint32_t)n) != 0) n++;
    return n;
}

/* Integer arguments are r4..r10 (7 registers). Past that, the value is 0 and the index still advances. */
static uint32_t int_arg(const ppc_ctx *c, unsigned *next) {
    unsigned i = (*next)++;
    return i <= 10 ? c->r[i] : 0u;
}

/* Floating point arguments are f1..f8. Past that, the value is 0.0. */
static double fp_arg(const ppc_ctx *c, unsigned *next) {
    unsigned i = (*next)++;
    return i <= 8 ? ppc_d(c->f[i]) : 0.0;
}

/* OSReport(fmt, ...): printf-style output to SPM_LOG. Integer arguments come from r4..r10
 * in order, floating point arguments from f1..f8. Supports %d %i %u %x %X %o %c %s %f %e %g
 * and %%, with an optional l, ll or h length modifier that is ignored. */
void hle_OSReport(ppc_ctx *c) {
    uint32_t fmt_ea = c->r[3];
    size_t len = guest_strlen(c, fmt_ea);
    char *fmt = (char *)malloc(len + 1);
    unsigned next_int = 4, next_fp = 1;
    size_t i;
    if (!fmt) abort();
    for (i = 0; i < len; i++) fmt[i] = (char)ppc_r8(c, fmt_ea + (uint32_t)i);
    fmt[len] = '\0';

    for (i = 0; fmt[i]; i++) {
        char spec[32];
        size_t k = 0;
        if (fmt[i] != '%') {
            fputc(fmt[i], SPM_LOG);
            continue;
        }
        if (fmt[i + 1] == '%') {
            fputc('%', SPM_LOG);
            i++;
            continue;
        }
        spec[k++] = '%';
        i++;
        while (fmt[i] && strchr("-+ #0123456789.", fmt[i]) && k < sizeof spec - 4) spec[k++] = fmt[i++];
        while (fmt[i] == 'l' || fmt[i] == 'h') i++;
        if (!fmt[i]) break;
        switch (fmt[i]) {
        case 'd': case 'i':
            spec[k++] = 'l'; spec[k++] = 'd'; spec[k] = '\0';
            fprintf(SPM_LOG, spec, (long)(int32_t)int_arg(c, &next_int));
            break;
        case 'u': case 'x': case 'X': case 'o':
            spec[k++] = 'l'; spec[k++] = fmt[i]; spec[k] = '\0';
            fprintf(SPM_LOG, spec, (unsigned long)int_arg(c, &next_int));
            break;
        case 'c':
            spec[k++] = 'c'; spec[k] = '\0';
            fprintf(SPM_LOG, spec, (int)(int_arg(c, &next_int) & 0xFF));
            break;
        case 's': {
            uint32_t ea = int_arg(c, &next_int);
            size_t n = guest_strlen(c, ea);
            char *s = (char *)malloc(n + 1);
            size_t j;
            if (!s) abort();
            for (j = 0; j < n; j++) s[j] = (char)ppc_r8(c, ea + (uint32_t)j);
            s[n] = '\0';
            spec[k++] = 's'; spec[k] = '\0';
            fprintf(SPM_LOG, spec, s);
            free(s);
            break;
        }
        case 'f': case 'e': case 'g':
            spec[k++] = fmt[i]; spec[k] = '\0';
            fprintf(SPM_LOG, spec, fp_arg(c, &next_fp));
            break;
        default:
            fputc('%', SPM_LOG);
            fputc(fmt[i], SPM_LOG);
            break;
        }
    }
    free(fmt);
    c->r[3] = 0;
}

void hle_OSHalt(ppc_ctx *c) {
    (void)c;
    fprintf(SPM_LOG, "OSHalt called by guest\n");
    exit(1);
}

void hle_memcpy(ppc_ctx *c) {
    uint32_t dst = c->r[3], src = c->r[4], n = c->r[5];
    uint32_t i;
    for (i = 0; i < n; i++) ppc_w8(c, dst + i, ppc_r8(c, src + i));
    c->r[3] = dst;
}

void hle_memset(ppc_ctx *c) {
    uint32_t dst = c->r[3], value = c->r[4] & 0xFF, n = c->r[5];
    uint32_t i;
    for (i = 0; i < n; i++) ppc_w8(c, dst + i, value);
    c->r[3] = dst;
}

/* Cache maintenance has no effect on the host, because guest memory is coherent here. */
void hle_DCFlushRange(ppc_ctx *c) {
    (void)c;
}

void hle_DCInvalidateRange(ppc_ctx *c) {
    (void)c;
}

void hle_ICInvalidateRange(ppc_ctx *c) {
    (void)c;
}
