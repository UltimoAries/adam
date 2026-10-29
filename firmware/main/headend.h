#pragma once
#include <stdbool.h>
#include <stdint.h>
void headend_init(void);
bool headend_configure(const char *url,const char *unit_id,const char *token);
bool headend_start(const char *wake_model);
void headend_stop(void);
void headend_feed(const int16_t *stereo,unsigned frames);
// Called in the microphone task, never from its ISR.
void headend_audio_gap(void);
bool headend_selftest(void);
bool headend_listening(void);
bool headend_busy(void);
const char *headend_state(void);
void headend_status(void);
