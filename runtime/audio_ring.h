/* Audio foundation: big-endian Wii PCM16 to little-endian 3DS samples, and a ring buffer.
 * Header-only so it compiles in host tests and in the 3DS build. UNTESTED ON HARDWARE. */
#ifndef SPM3DS_AUDIO_RING_H
#define SPM3DS_AUDIO_RING_H

#include <stdint.h>

/* Converts n big-endian signed 16-bit samples from src into little-endian samples in dst,
 * scaling by volume / 256 (256 = unity; values are clamped). */
static inline void spm3ds_pcm16_be_to_le(int16_t *dst, const uint8_t *src, uint32_t n,
                                         uint32_t volume) {
    uint32_t i;
    for (i = 0; i < n; i++) {
        int32_t v = (int16_t)((src[2 * i] << 8) | src[2 * i + 1]);
        v = (v * (int32_t)volume) / 256;
        if (v > 32767) v = 32767;
        if (v < -32768) v = -32768;
        dst[i] = (int16_t)v;
    }
}

typedef struct {
    int16_t *buf;
    uint32_t capacity;   /* in samples; must be a power of two */
    uint32_t head;       /* next write index (monotonic) */
    uint32_t tail;       /* next read index (monotonic) */
} spm3ds_ring;

static inline void spm3ds_ring_init(spm3ds_ring *r, int16_t *storage, uint32_t capacity) {
    r->buf = storage;
    r->capacity = capacity;
    r->head = r->tail = 0;
}

static inline uint32_t spm3ds_ring_fill(const spm3ds_ring *r) { return r->head - r->tail; }

/* Writes up to n samples. Returns how many were stored (the rest are dropped). */
static inline uint32_t spm3ds_ring_write(spm3ds_ring *r, const int16_t *src, uint32_t n) {
    uint32_t space = r->capacity - spm3ds_ring_fill(r);
    uint32_t k = n < space ? n : space, i;
    for (i = 0; i < k; i++) r->buf[(r->head + i) & (r->capacity - 1u)] = src[i];
    r->head += k;
    return k;
}

/* Reads up to n samples, zero-filling any shortfall (silence, not stale data). */
static inline uint32_t spm3ds_ring_read(spm3ds_ring *r, int16_t *dst, uint32_t n) {
    uint32_t fill = spm3ds_ring_fill(r);
    uint32_t k = n < fill ? n : fill, i;
    for (i = 0; i < k; i++) dst[i] = r->buf[(r->tail + i) & (r->capacity - 1u)];
    for (; i < n; i++) dst[i] = 0;
    r->tail += k;
    return k;
}

/* Writes `frames` stereo frames (interleaved left, right) of a sine tone into dst and
 * advances *phase (cycles, 0..1). Used for the audio output test on the console. */
static inline void spm3ds_tone_fill(int16_t *dst, uint32_t frames, float *phase, float freq,
                                    float rate, int16_t amplitude) {
    uint32_t i;
    float step = freq / rate;
    for (i = 0; i < frames; i++) {
        /* Parabolic sine approximation: within 0.1% of sin over a full cycle, no libm. */
        float x = *phase * 2.0f - 1.0f;                  /* -1..1 maps to -pi..pi */
        float y = 4.0f * x * (1.0f - (x < 0.0f ? -x : x));
        int16_t v;
        y = 0.225f * (y * (y < 0.0f ? -y : y) - y) + y;
        v = (int16_t)(-y * (float)amplitude);
        dst[2 * i] = v;
        dst[2 * i + 1] = v;
        *phase += step;
        if (*phase >= 1.0f) *phase -= 1.0f;
    }
}

#endif
