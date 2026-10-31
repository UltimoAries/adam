#pragma once
#include <stdbool.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void wake_init(void);
void wake_feed(const int16_t *stereo,unsigned frames);
void wake_enable(bool enabled);
bool wake_enabled(void);
const char *wake_model_id(void);
const char *wake_label(void);
void wake_status(void);
#ifdef __cplusplus
}
#endif
