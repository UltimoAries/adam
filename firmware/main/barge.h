#pragma once
#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
void barge_init(void);
bool barge_begin(void);
bool barge_active(void);
bool barge_ready(void);
bool barge_interrupt(void);
void barge_cancel(void);
bool barge_end(void);
void barge_feed(const int16_t *stereo,unsigned frames);
void barge_status(void);
// Explicit USB diagnostics only: arm one reply, then retrieve 3-channel PCM.
bool barge_record(void);
const int16_t *barge_clip(size_t *bytes);
