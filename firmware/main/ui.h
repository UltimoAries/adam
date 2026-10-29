#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "touch.h"
void ui_init(void);
void ui_render(int64_t now);
void ui_touch(const touch_event_t *event,int64_t now);
void ui_page(bool settings);
void ui_status(void);
const char *ui_name(void);
