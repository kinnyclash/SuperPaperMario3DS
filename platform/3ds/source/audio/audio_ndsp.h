/* 3DS audio output through NDSP. Translated audio code (and, for now, a test tone) writes
 * interleaved stereo PCM16 frames into a ring buffer; spm3ds_audio_pump() moves them to the
 * DSP in double-buffered blocks. MIT License. */
#ifndef SPM3DS_AUDIO_NDSP_H
#define SPM3DS_AUDIO_NDSP_H

#include <stdint.h>

#define SPM3DS_AUDIO_RATE 32000u      /* the Wii mixes its output at 32 kHz */

/* Returns 1 when NDSP started. NDSP needs the DSP firmware dump at sdmc:/3ds/dspfirm.cdc
 * (made on the console with the DSP1 homebrew tool); without it the app runs silently. */
int spm3ds_audio_init(void);
void spm3ds_audio_exit(void);
/* Queues finished blocks again with new samples. Call once per frame. */
void spm3ds_audio_pump(void);
/* Appends stereo frames (left, right interleaved). Returns frames accepted. */
uint32_t spm3ds_audio_write(const int16_t *frames, uint32_t count);
/* Stereo frames waiting in the ring buffer. */
uint32_t spm3ds_audio_queued(void);
/* Test tone: when enabled, pump() keeps the ring filled with a 440 Hz tone. */
void spm3ds_audio_tone(int enabled);
/* Blocks handed to the DSP so far, and blocks that had to be padded with silence. */
uint32_t spm3ds_audio_blocks(void);
uint32_t spm3ds_audio_underruns(void);

#endif /* SPM3DS_AUDIO_NDSP_H */
