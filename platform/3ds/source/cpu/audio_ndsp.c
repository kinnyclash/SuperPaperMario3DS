/* 3DS audio output through NDSP. See audio_ndsp.h.
 *
 * Names checked against libctru's ndsp/ndsp.h, ndsp/channel.h and services/dsp.h; the buffer
 * set-up follows the 3ds-examples audio/filters program (nsamples counts stereo frames).
 * UNVERIFIED ON HARDWARE. MIT License. */
#include <3ds.h>
#include <string.h>

#include "audio_ndsp.h"
#include "audio_ring.h"

#define BLOCK_FRAMES 1024u                  /* 32 ms at 32 kHz */
#define BLOCKS 3u
#define RING_SAMPLES (1u << 15)             /* 16384 stereo frames, about half a second */
#define CHANNEL 0

static int16_t *pcm;                        /* linear memory: BLOCKS blocks of stereo frames */
static ndspWaveBuf wave[BLOCKS];
static int16_t ring_storage[RING_SAMPLES];
static spm3ds_ring ring;
static int running;
static int tone_on;
static float tone_phase;
static uint32_t blocks_sent, underruns;

int spm3ds_audio_init(void) {
    float mix[12];
    unsigned i;
    spm3ds_ring_init(&ring, ring_storage, RING_SAMPLES);
    if (R_FAILED(ndspInit())) return 0;
    pcm = (int16_t *)linearAlloc(BLOCKS * BLOCK_FRAMES * 2u * sizeof(int16_t));
    if (!pcm) {
        ndspExit();
        return 0;
    }
    memset(pcm, 0, BLOCKS * BLOCK_FRAMES * 2u * sizeof(int16_t));
    ndspSetOutputMode(NDSP_OUTPUT_STEREO);
    ndspChnReset(CHANNEL);
    ndspChnSetInterp(CHANNEL, NDSP_INTERP_LINEAR);
    ndspChnSetRate(CHANNEL, (float)SPM3DS_AUDIO_RATE);
    ndspChnSetFormat(CHANNEL, NDSP_FORMAT_STEREO_PCM16);
    memset(mix, 0, sizeof mix);
    mix[0] = 1.0f;   /* front left */
    mix[1] = 1.0f;   /* front right */
    ndspChnSetMix(CHANNEL, mix);
    memset(wave, 0, sizeof wave);
    for (i = 0; i < BLOCKS; i++) {
        wave[i].data_vaddr = pcm + i * BLOCK_FRAMES * 2u;
        wave[i].nsamples = BLOCK_FRAMES;
        wave[i].status = NDSP_WBUF_FREE;
    }
    running = 1;
    return 1;
}

void spm3ds_audio_exit(void) {
    if (!running) return;
    ndspChnWaveBufClear(CHANNEL);
    ndspExit();
    linearFree(pcm);
    pcm = NULL;
    running = 0;
}

uint32_t spm3ds_audio_write(const int16_t *frames, uint32_t count) {
    return spm3ds_ring_write(&ring, frames, 2u * count) / 2u;
}

uint32_t spm3ds_audio_queued(void) {
    return spm3ds_ring_fill(&ring) / 2u;
}

void spm3ds_audio_tone(int enabled) {
    tone_on = enabled;
}

uint32_t spm3ds_audio_blocks(void) {
    return blocks_sent;
}

uint32_t spm3ds_audio_underruns(void) {
    return underruns;
}

void spm3ds_audio_pump(void) {
    unsigned i;
    if (!running) return;
    if (tone_on) {
        static int16_t tone[2u * BLOCK_FRAMES];
        while (spm3ds_audio_queued() + BLOCK_FRAMES <= RING_SAMPLES / 2u &&
               spm3ds_audio_queued() < BLOCKS * BLOCK_FRAMES) {
            spm3ds_tone_fill(tone, BLOCK_FRAMES, &tone_phase, 440.0f, (float)SPM3DS_AUDIO_RATE,
                             6000);
            spm3ds_audio_write(tone, BLOCK_FRAMES);
        }
    }
    for (i = 0; i < BLOCKS; i++) {
        ndspWaveBuf *w = &wave[i];
        if (w->status != NDSP_WBUF_FREE && w->status != NDSP_WBUF_DONE) continue;
        {
            /* An empty ring is silence (nothing playing); a partly filled one is an underrun. */
            uint32_t got = spm3ds_ring_read(&ring, w->data_pcm16, 2u * BLOCK_FRAMES);
            if (got && got < 2u * BLOCK_FRAMES) underruns++;
        }
        DSP_FlushDataCache(w->data_pcm16, BLOCK_FRAMES * 2u * sizeof(int16_t));
        ndspChnWaveBufAdd(CHANNEL, w);
        blocks_sent++;
    }
}
