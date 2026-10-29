#pragma once
#include <stdbool.h>
#include "driver/i2c_master.h"
bool audio_init(i2c_master_bus_handle_t bus,int volume);
void audio_set_volume(int volume);
bool audio_play_chime(void);
bool audio_play_tone(unsigned hz,unsigned duration_ms);
bool audio_play_clip(unsigned channel);
bool audio_clip_busy(void);
void audio_stop(void);
bool audio_is_ready(void);
bool audio_is_playing(void);
void audio_status(void);
bool audio_diagnostic_loopback(bool enable);
unsigned audio_play_message(const int16_t *pcm,unsigned frames);
bool audio_message_delivered(unsigned id);
unsigned audio_play_listen_cue(void);
