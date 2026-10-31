#pragma once
#include <stdint.h>
#include <stdbool.h>
typedef enum { SPACE_IDLE, SPACE_LISTENING, SPACE_PLAYING } space_state_t;
void space_init(void);
void space_render(int64_t now, space_state_t activity, bool controls);

// Diagnostic sky time preview only; expires after 60 seconds. -1 restores live time.
void space_preview(int minute, int64_t now);
// Preview background weather only; -1 restores live weather. Expires in 60 seconds.
void space_weather_preview(int code, int64_t now);
