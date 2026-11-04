#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "sdkconfig.h"

#define VOICE_QUIET_BLOCKS ((CONFIG_ADAM_ENDPOINT_SILENCE_MS+9)/10)

enum { VOICE_BLOCK_SAMPLES=160, VOICE_HISTORY_BLOCKS=200, VOICE_LIVE_BLOCKS=1300,
       VOICE_STORAGE_BLOCKS=2*VOICE_HISTORY_BLOCKS+VOICE_LIVE_BLOCKS };
typedef struct { int16_t pcm[VOICE_BLOCK_SAMPLES]; } voice_block_t;
// The caller serializes operations. All copies are at most one 20-ms packet;
// beginning a session freezes one history bank and swaps banks without a copy.
typedef struct {
    voice_block_t *history[2],*live;
    unsigned bank,head,have,frozen_bank,first,prefix,recorded;
    uint32_t energies[VOICE_HISTORY_BLOCKS],threshold;
    unsigned limit,quiet,voiced,reason;
    bool automatic,speech_seen,use_vad,vad_speech,followup;
    bool session,capturing,failed;
} voice_buffer_t;
void voice_buffer_init(voice_buffer_t *buffer,voice_block_t *storage);
void voice_buffer_feed(voice_buffer_t *buffer,const int16_t *mono,bool cache_history);
bool voice_buffer_begin(voice_buffer_t *buffer);
bool voice_buffer_begin_auto(voice_buffer_t *buffer);
bool voice_buffer_begin_followup(voice_buffer_t *buffer);
typedef struct { unsigned remaining, settle, voiced; uint32_t threshold; } voice_followup_t;
void voice_followup_arm(voice_followup_t *gate,uint32_t threshold,unsigned seconds);
bool voice_followup_feed(voice_followup_t *gate,uint32_t energy,bool speech);
bool voice_buffer_packet(const voice_buffer_t *buffer,unsigned sequence,uint8_t *pcm);
void voice_buffer_gap(voice_buffer_t *buffer);
void voice_buffer_end(voice_buffer_t *buffer);
bool voice_buffer_selftest(void);
