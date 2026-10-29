#pragma once
#include <stdbool.h>
#include <stdint.h>
void eye_init(void);
void eye_auto(bool enabled);
void eye_look(int x, int y);
void eye_blink(int64_t now);
void eye_pupil(int radius);
void eye_render(int64_t now);
bool eye_is_auto(void);
int eye_x(void);
int eye_y(void);
int eye_lid_percent(void);
