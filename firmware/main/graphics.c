#include "graphics.h"
#include <assert.h>
#include <ctype.h>
#include <stdlib.h>
#include "esp_heap_caps.h"

static uint16_t *frame;
uint16_t gfx_color(unsigned rgb)
{
    uint16_t c = ((rgb >> 8) & 0xf800) | ((rgb >> 5) & 0x07e0) | ((rgb >> 3) & 31);
    return (c >> 8) | (c << 8); // RGB565 in LCD wire byte order.
}
void gfx_init(void)
{
    frame = heap_caps_malloc(SCREEN_W * SCREEN_H * 2, MALLOC_CAP_SPIRAM);
    assert(frame);
}
uint16_t *gfx_buffer(void) { return frame; }
void gfx_clear(unsigned rgb)
{
    uint16_t c = gfx_color(rgb);
    for (int i = 0; i < SCREEN_W * SCREEN_H; ++i) frame[i] = c;
}
void gfx_pixel(int x, int y, unsigned rgb)
{
    if (x >= 0 && x < SCREEN_W && y >= 0 && y < SCREEN_H)
        frame[y * SCREEN_W + x] = gfx_color(rgb);
}
void gfx_rect(int x, int y, int w, int h, unsigned rgb)
{
    int right = x + w, bottom = y + h;
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (right > SCREEN_W) right = SCREEN_W;
    if (bottom > SCREEN_H) bottom = SCREEN_H;
    uint16_t c = gfx_color(rgb);
    for (int row = y; row < bottom; ++row)
        for (int col = x; col < right; ++col) frame[row * SCREEN_W + col] = c;
}
void gfx_line(int x0, int y0, int x1, int y1, unsigned rgb)
{
    int dx = abs(x1-x0), dy = -abs(y1-y0);
    int sx = x0 < x1 ? 1 : -1, sy = y0 < y1 ? 1 : -1, error = dx + dy;
    for (;;) {
        gfx_pixel(x0, y0, rgb);
        if (x0 == x1 && y0 == y1) break;
        int twice = 2 * error;
        if (twice >= dy) { error += dy; x0 += sx; }
        if (twice <= dx) { error += dx; y0 += sy; }
    }
}
void gfx_circle(int cx, int cy, int radius, unsigned rgb, bool filled)
{
    int x = radius, y = 0, error = 1-radius;
    while (x >= y) {
        if (filled) {
            gfx_rect(cx-x, cy+y, 2*x+1, 1, rgb);
            gfx_rect(cx-x, cy-y, 2*x+1, 1, rgb);
            gfx_rect(cx-y, cy+x, 2*y+1, 1, rgb);
            gfx_rect(cx-y, cy-x, 2*y+1, 1, rgb);
        } else {
            const int points[8][2] = {{x,y},{y,x},{-x,y},{-y,x},
                                      {x,-y},{y,-x},{-x,-y},{-y,-x}};
            for (int i=0; i<8; ++i) gfx_pixel(cx+points[i][0], cy+points[i][1], rgb);
        }
        ++y;
        if (error < 0) error += 2*y+1;
        else { --x; error += 2*(y-x)+1; }
    }
}

// Original 5x7 glyphs. Preserve case for network names and keyboard input.
static const char alphabet[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ+-.:/"
    "abcdefghijklmnopqrstuvwxyz!\"#$%&'()*,;<=>?@[\\]^_`{|}~";
static const uint8_t glyphs[][7] = {
 {14,17,19,21,25,17,14},{4,12,4,4,4,4,14},{14,17,1,2,4,8,31},
 {30,1,1,14,1,1,30},{2,6,10,18,31,2,2},{31,16,16,30,1,1,30},
 {14,16,16,30,17,17,14},{31,1,2,4,8,8,8},{14,17,17,14,17,17,14},
 {14,17,17,15,1,1,14},{14,17,17,31,17,17,17},{30,17,17,30,17,17,30},
 {14,17,16,16,16,17,14},{30,17,17,17,17,17,30},{31,16,16,30,16,16,31},
 {31,16,16,30,16,16,16},{14,17,16,23,17,17,14},{17,17,17,31,17,17,17},
 {14,4,4,4,4,4,14},{7,2,2,2,2,18,12},{17,18,20,24,20,18,17},
 {16,16,16,16,16,16,31},{17,27,21,21,17,17,17},{17,25,21,19,17,17,17},
 {14,17,17,17,17,17,14},{30,17,17,30,16,16,16},{14,17,17,17,21,18,13},
 {30,17,17,30,20,18,17},{15,16,16,14,1,1,30},{31,4,4,4,4,4,4},
 {17,17,17,17,17,17,14},{17,17,17,17,17,10,4},{17,17,17,21,21,27,17},
 {17,17,10,4,10,17,17},{17,17,10,4,4,4,4},{31,1,2,4,8,16,31},
 {0,4,4,31,4,4,0},{0,0,0,31,0,0,0},{0,0,0,0,0,6,6},
 {0,6,6,0,6,6,0},{1,2,2,4,8,8,16},
 {0,0,14,1,15,17,15},{16,16,30,17,17,17,30},{0,0,14,17,16,17,14},
 {1,1,15,17,17,17,15},{0,0,14,17,31,16,14},{6,8,8,30,8,8,8},
 {0,0,15,17,15,1,14},{16,16,30,17,17,17,17},{4,0,12,4,4,4,14},
 {2,0,6,2,2,18,12},{16,16,18,20,24,20,18},{12,4,4,4,4,4,14},
 {0,0,26,21,21,21,21},{0,0,30,17,17,17,17},{0,0,14,17,17,17,14},
 {0,0,30,17,30,16,16},{0,0,15,17,15,1,1},{0,0,22,25,16,16,16},
 {0,0,15,16,14,1,30},{8,8,30,8,8,9,6},{0,0,17,17,17,19,13},
 {0,0,17,17,17,10,4},{0,0,17,17,21,21,10},{0,0,17,10,4,10,17},
 {0,0,17,17,15,1,14},{0,0,31,2,4,8,31},
 {4,4,4,4,4,0,4},{10,10,10,0,0,0,0},{10,31,10,10,31,10,0},
 {4,15,20,14,5,30,4},{25,26,2,4,8,11,19},{12,18,20,8,21,18,13},
 {4,4,8,0,0,0,0},{2,4,8,8,8,4,2},{8,4,2,2,2,4,8},
 {0,21,14,31,14,21,0},{0,0,0,0,0,4,8},{0,4,0,0,4,4,8},
 {2,4,8,16,8,4,2},{0,0,31,0,31,0,0},{8,4,2,1,2,4,8},
 {14,17,1,2,4,0,4},{14,17,23,21,23,16,14},{14,8,8,8,8,8,14},
 {16,8,8,4,2,2,1},{14,2,2,2,2,2,14},{4,10,17,0,0,0,0},
 {0,0,0,0,0,0,31},{8,4,0,0,0,0,0},{2,4,4,8,4,4,2},
 {4,4,4,4,4,4,4},{8,4,4,2,4,4,8},{0,0,9,22,0,0,0}
};
_Static_assert(sizeof(glyphs)/sizeof(glyphs[0])==sizeof(alphabet)-1,"glyph count");
void gfx_text(int x, int y, int scale, unsigned rgb, const char *text)
{
    for (; *text; ++text, x += 6*scale) {
        int letter = (unsigned char)*text, index = -1;
        for (int i=0; alphabet[i]; ++i) if (alphabet[i] == letter) { index=i; break; }
        if (index < 0) continue;
        for (int row=0; row<7; ++row)
            for (int col=0; col<5; ++col)
                if (glyphs[index][row] & (1 << (4-col)))
                    gfx_rect(x+col*scale, y+row*scale, scale, scale, rgb);
    }
}
void gfx_pattern(bool grid)
{
    gfx_clear(0x080C14);
    if (!grid) {
        const unsigned colors[] = {0xFF0000,0x00FF00,0x0000FF,0xFFFFFF,0x000000,0x808080};
        for (int i=0; i<6; ++i) gfx_rect(i*60, 0, 60, 360, colors[i]);
        return;
    }
    for (int i=0; i<360; i+=30) {
        gfx_line(i,0,i,359,0x24354B);
        gfx_line(0,i,359,i,0x24354B);
    }
    gfx_circle(180,180,176,0xFFFFFF,false);
    gfx_line(15,180,345,180,0xFFFFFF);
    gfx_line(180,15,180,345,0xFFFFFF);
    gfx_line(345,180,333,172,0xFFFFFF);
    gfx_line(345,180,333,188,0xFFFFFF);
    gfx_line(180,345,172,333,0xFFFFFF);
    gfx_line(180,345,188,333,0xFFFFFF);
    gfx_circle(180,180,5,0xFFFFFF,true);
    gfx_rect(142,24,76,26,0xB00000);
    gfx_text(153,28,2,0xFFFFFF,"TOP");
    gfx_rect(280,158,72,19,0x006000);
    gfx_text(287,164,1,0xFFFFFF,"RIGHT +X");
    gfx_rect(126,303,108,26,0x0000C0);
    gfx_text(143,307,2,0xFFFFFF,"BOTTOM");
    gfx_rect(8,158,65,19,0xB09000);
    gfx_text(20,164,1,0xFFFFFF,"LEFT");
    gfx_text(187,185,1,0xFFFFFF,"180:180");
    gfx_text(190,277,2,0xFFFFFF,"+Y");
}
