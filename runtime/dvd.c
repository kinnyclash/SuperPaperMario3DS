/* Wii DVD file access for translated code. See dvd.h. MIT License. */
#include "dvd.h"

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Provided by the generated code: calls the translated function at a guest address. */
void ppc_dispatch(ppc_ctx *c, uint32_t target);

#define DVD_PATH_MAX 256
#define DVD_HANDLE_TAG 0x5D000000u

static char dvd_root[DVD_PATH_MAX] = ".";
static uint8_t *fst;
static uint32_t fst_entries;
static size_t fst_size;
static FILE *open_files[SPM3DS_DVD_MAX_OPEN];

void spm3ds_dvd_set_root(const char *dir) {
    strncpy(dvd_root, dir, sizeof dvd_root - 1);
    dvd_root[sizeof dvd_root - 1] = '\0';
}

void spm3ds_dvd_reset(void) {
    int i;
    for (i = 0; i < SPM3DS_DVD_MAX_OPEN; i++) {
        if (open_files[i]) fclose(open_files[i]);
        open_files[i] = NULL;
    }
    free(fst);
    fst = NULL;
    fst_entries = 0;
    fst_size = 0;
}

static uint32_t rd32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

int spm3ds_dvd_load_fst(const uint8_t *data, size_t size) {
    uint32_t n;
    if (size < 12 || !(data[0] & 1)) return -1;
    n = rd32(data + 8);
    if (n == 0 || (size_t)n * 12u > size) return -1;
    free(fst);
    fst = (uint8_t *)malloc(size);
    if (!fst) return -1;
    memcpy(fst, data, size);
    fst_size = size;
    fst_entries = n;
    return (int)n;
}

int spm3ds_dvd_load_fst_file(const char *path) {
    FILE *f = fopen(path, "rb");
    long n;
    uint8_t *buf;
    int result;
    if (!f) return -1;
    if (fseek(f, 0, SEEK_END) != 0 || (n = ftell(f)) <= 0 || fseek(f, 0, SEEK_SET) != 0) {
        fclose(f);
        return -1;
    }
    buf = (uint8_t *)malloc((size_t)n);
    if (!buf || fread(buf, 1, (size_t)n, f) != (size_t)n) {
        free(buf);
        fclose(f);
        return -1;
    }
    fclose(f);
    result = spm3ds_dvd_load_fst(buf, (size_t)n);
    free(buf);
    return result;
}

/* ---- FST lookups ---- */

static const char *fst_name(uint32_t i) {
    uint32_t off = rd32(fst + 12u * i) & 0xFFFFFFu;
    size_t strings = (size_t)fst_entries * 12u;
    if (strings + off >= fst_size) return "";
    return (const char *)fst + strings + off;
}

static int fst_is_dir(uint32_t i) {
    return fst[12u * i] & 1;
}

static uint32_t fst_word(uint32_t i, unsigned w) {
    return rd32(fst + 12u * i + 4u * w);
}

static int name_equal(const char *a, size_t alen, const char *b) {
    size_t i;
    for (i = 0; i < alen; i++) {
        if (!b[i] || tolower((unsigned char)a[i]) != tolower((unsigned char)b[i])) return 0;
    }
    return b[alen] == '\0';
}

int32_t spm3ds_dvd_entrynum(const char *path) {
    uint32_t dir = 0;
    const char *p = path;
    if (!fst) return -1;
    while (*p == '/') p++;
    if (!*p) return 0;
    for (;;) {
        const char *end = strchr(p, '/');
        size_t len = end ? (size_t)(end - p) : strlen(p);
        uint32_t i = dir + 1, stop = fst_word(dir, 2), found = 0;
        if (len == 1 && p[0] == '.') {
            found = dir;
        } else {
            while (i < stop && i < fst_entries) {
                if (name_equal(p, len, fst_name(i))) {
                    found = i;
                    break;
                }
                i = fst_is_dir(i) ? fst_word(i, 2) : i + 1;
            }
            if (!found) return -1;
        }
        if (!end) return (int32_t)found;
        if (!fst_is_dir(found)) return -1;
        dir = found;
        p = end + 1;
        while (*p == '/') p++;
        if (!*p) return (int32_t)dir;
    }
}

/* Builds the host-relative path of an FST entry ("dir/file"). Returns 0 on failure. */
static int fst_path(uint32_t entry, char *out, size_t cap) {
    uint32_t chain[64];
    unsigned depth = 0, k;
    size_t pos = 0;
    uint32_t i = entry;
    if (!fst || entry == 0 || entry >= fst_entries) return 0;
    while (i != 0 && depth < 64) {
        uint32_t parent;
        chain[depth++] = i;
        if (fst_is_dir(i)) {
            parent = fst_word(i, 1);
        } else {
            /* A file's parent is the nearest directory before it that contains it. */
            uint32_t j = i;
            parent = 0;
            while (j-- > 0) {
                if (fst_is_dir(j) && fst_word(j, 2) > i) {
                    parent = j;
                    break;
                }
            }
        }
        if (parent >= i) return 0;
        i = parent;
    }
    for (k = depth; k-- > 0;) {
        const char *name = fst_name(chain[k]);
        size_t len = strlen(name);
        if (pos + len + 2 > cap) return 0;
        memcpy(out + pos, name, len);
        pos += len;
        if (k) out[pos++] = '/';
    }
    out[pos] = '\0';
    return 1;
}

/* ---- guest helpers ---- */

static int guest_string(ppc_ctx *c, uint32_t ea, char *out, size_t cap) {
    size_t i;
    for (i = 0; i + 1 < cap; i++) {
        out[i] = (char)ppc_r8(c, ea + (uint32_t)i);
        if (!out[i]) return 1;
    }
    out[cap - 1] = '\0';
    return 0;
}

static int path_is_safe(const char *p) {
    return !strstr(p, "..") && !strchr(p, ':') && !strchr(p, '\\');
}

static int open_slot(const char *relative) {
    char full[2 * DVD_PATH_MAX + 2];
    int i;
    FILE *f;
    if (!path_is_safe(relative)) return -1;
    while (*relative == '/') relative++;
    snprintf(full, sizeof full, "%s/%s", dvd_root, relative);
    for (i = 0; i < SPM3DS_DVD_MAX_OPEN; i++) {
        if (!open_files[i]) break;
    }
    if (i == SPM3DS_DVD_MAX_OPEN) return -1;
    f = fopen(full, "rb");
    if (!f) return -1;
    open_files[i] = f;
    return i;
}

static FILE *file_of(ppc_ctx *c, uint32_t fi, int *slot) {
    uint32_t tag = ppc_r32(c, fi + SPM3DS_DVD_FI_START);
    uint32_t s = tag & 0xFFu;
    if ((tag & 0xFFFFFF00u) != DVD_HANDLE_TAG || s >= SPM3DS_DVD_MAX_OPEN) return NULL;
    if (slot) *slot = (int)s;
    return open_files[s];
}

static uint32_t finish_open(ppc_ctx *c, uint32_t fi, int slot) {
    long len;
    FILE *f;
    if (slot < 0) return 0;
    f = open_files[slot];
    if (fseek(f, 0, SEEK_END) != 0 || (len = ftell(f)) < 0) {
        fclose(f);
        open_files[slot] = NULL;
        return 0;
    }
    ppc_w32(c, fi + SPM3DS_DVD_FI_START, DVD_HANDLE_TAG | (uint32_t)slot);
    ppc_w32(c, fi + SPM3DS_DVD_FI_LENGTH, (uint32_t)len);
    ppc_w32(c, fi + SPM3DS_DVD_FI_CALLBACK, 0);
    ppc_w32(c, fi + SPM3DS_DVD_FI_STATE, 0);
    return 1;
}

/* Copies file bytes into guest memory in pieces that do not cross a page. */
static int32_t read_into_guest(ppc_ctx *c, FILE *f, uint32_t addr, uint32_t length, uint32_t off) {
    uint32_t done = 0;
    if (fseek(f, (long)off, SEEK_SET) != 0) return -1;
    while (done < length) {
        uint32_t ea = addr + done;
        uint32_t chunk = length - done;
        uint32_t room = PPC_PAGE_SIZE - (ea & (PPC_PAGE_SIZE - 1u));
        uint8_t *p;
        size_t got;
        if (chunk > room) chunk = room;
        p = ppc_try(c, ea, chunk);
        if (!p) {
            ppc_ptr(c, ea, chunk); /* reports the fault */
            return -1;
        }
        got = fread(p, 1, chunk, f);
        done += (uint32_t)got;
        if (got < chunk) break;
    }
    return (int32_t)done;
}

/* ---- HLE entry points ---- */

void hle_DVDOpen(ppc_ctx *c) {
    char path[DVD_PATH_MAX], rel[DVD_PATH_MAX];
    uint32_t fi = c->r[4];
    int slot = -1;
    if (!guest_string(c, c->r[3], path, sizeof path)) {
        c->r[3] = 0;
        return;
    }
    if (fst) {
        int32_t e = spm3ds_dvd_entrynum(path);
        if (e > 0 && !fst_is_dir((uint32_t)e) && fst_path((uint32_t)e, rel, sizeof rel))
            slot = open_slot(rel);
    } else {
        slot = open_slot(path);
    }
    c->r[3] = finish_open(c, fi, slot);
}

void hle_DVDFastOpen(ppc_ctx *c) {
    char rel[DVD_PATH_MAX];
    int32_t e = (int32_t)c->r[3];
    int slot = -1;
    if (fst && e > 0 && (uint32_t)e < fst_entries && !fst_is_dir((uint32_t)e) &&
        fst_path((uint32_t)e, rel, sizeof rel))
        slot = open_slot(rel);
    c->r[3] = finish_open(c, c->r[4], slot);
}

void hle_DVDConvertPathToEntrynum(ppc_ctx *c) {
    char path[DVD_PATH_MAX];
    if (!guest_string(c, c->r[3], path, sizeof path)) {
        c->r[3] = 0xFFFFFFFFu;
        return;
    }
    c->r[3] = (uint32_t)spm3ds_dvd_entrynum(path);
}

void hle_DVDClose(ppc_ctx *c) {
    int slot;
    FILE *f = file_of(c, c->r[3], &slot);
    if (f) {
        fclose(f);
        open_files[slot] = NULL;
        ppc_w32(c, c->r[3] + SPM3DS_DVD_FI_START, 0);
    }
    c->r[3] = 1;
}

void hle_DVDReadPrio(ppc_ctx *c) {
    FILE *f = file_of(c, c->r[3], NULL);
    c->r[3] = f ? (uint32_t)read_into_guest(c, f, c->r[4], c->r[5], c->r[6]) : 0xFFFFFFFFu;
}

/* The read completes before the call returns; the callback (if any) runs immediately with
 * (result, fileInfo), as it would after the drive interrupt. */
void hle_DVDReadAsyncPrio(ppc_ctx *c) {
    uint32_t fi = c->r[3], cb = c->r[7];
    FILE *f = file_of(c, fi, NULL);
    int32_t result = f ? read_into_guest(c, f, c->r[4], c->r[5], c->r[6]) : -1;
    if (!f) {
        c->r[3] = 0;
        return;
    }
    ppc_w32(c, fi + SPM3DS_DVD_FI_CALLBACK, cb);
    ppc_w32(c, fi + SPM3DS_DVD_FI_STATE, 0);
    if (cb) {
        uint32_t saved_lr = c->lr;
        c->r[3] = (uint32_t)result;
        c->r[4] = fi;
        ppc_dispatch(c, cb);
        c->lr = saved_lr;
    }
    c->r[3] = 1;
}

void hle_DVDGetCommandBlockStatus(ppc_ctx *c) {
    c->r[3] = 0;
}

void hle_DVDGetDriveStatus(ppc_ctx *c) {
    c->r[3] = 0;
}
