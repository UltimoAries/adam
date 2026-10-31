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
typedef enum { HEADEND_LLM_UNKNOWN, HEADEND_LLM_PI, HEADEND_LLM_GPU } headend_llm_t;
typedef struct {
    bool clock_ready, weather_ready;
    int64_t unix_time, synced_us, observed_at;
    int utc_offset, temperature, weather_code;
    char units, location[33], weather_state[16], device_name[25];
    headend_llm_t llm_provider;
} headend_panel_t;
void headend_panel(headend_panel_t *out);
