#pragma once
#include <stdbool.h>
#include <stdint.h>
#define SCREEN_W 360
#define SCREEN_H 360
void gfx_init(void);
uint16_t *gfx_buffer(void);
uint16_t gfx_color(unsigned rgb);
void gfx_clear(unsigned rgb);
void gfx_pixel(int x, int y, unsigned rgb);
void gfx_rect(int x, int y, int w, int h, unsigned rgb);
void gfx_line(int x0, int y0, int x1, int y1, unsigned rgb);
void gfx_circle(int x, int y, int radius, unsigned rgb, bool filled);
void gfx_text(int x, int y, int scale, unsigned rgb, const char *text);
void gfx_pattern(bool grid);
