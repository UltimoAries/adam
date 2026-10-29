#include "eye.h"
#include "graphics.h"
#include <assert.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"

enum { IRIS_R = 50, IRIS_SIZE = IRIS_R*2+1 };
static uint16_t *base, *iris;
static int aperture[360];
static bool automatic = true;
static float gaze_x, gaze_y;
static int target_x, target_y, pupil = 20, lid_percent;
static int64_t last_frame, next_gaze, next_blink, blink_start = -1000000;
static unsigned gaze_step;

void eye_init(void)
{
    base = heap_caps_malloc(360*360*2, MALLOC_CAP_SPIRAM);
    iris = heap_caps_malloc(IRIS_SIZE*IRIS_SIZE*2, MALLOC_CAP_SPIRAM);
    assert(base && iris);
    for (int x=0; x<360; ++x) {
        float nx = (x-179.5f)/118.0f;
        aperture[x] = fabsf(nx) >= 1 ? 0 : (int)(70 * powf(1-nx*nx, 0.65f));
        for (int y=0; y<360; ++y) {
            float ny = (y-180)/74.0f;
            unsigned color = 0x030810;
            if (abs(y-180) < aperture[x]) {
                float shade = 1.0f - 0.30f*nx*nx - 0.18f*ny*ny - 0.10f*(ny+1);
                int edge = aperture[x] - abs(y-180);
                if (edge < 6) shade *= 0.55f + 0.45f*edge/6;
                unsigned r=228*shade, g=242*shade, b=248*shade;
                color=(r<<16)|(g<<8)|b;
            }
            base[y*360+x]=gfx_color(color);
        }
    }
    // Precompute the expensive polar iris texture once, not on every frame.
    for (int y=-IRIS_R; y<=IRIS_R; ++y) for (int x=-IRIS_R; x<=IRIS_R; ++x) {
        float radius=sqrtf(x*x+y*y), angle=atan2f(y,x);
        float fibers=0.5f+0.25f*sinf(angle*43+radius*0.18f)+
                          0.15f*sinf(angle*79-radius*0.11f)+
                          0.10f*sinf(angle*121+radius*0.3f);
        float shade=fmaxf(0.08f,fminf(1.0f,(IRIS_R-radius)/9));
        float glow=expf(-powf((radius-24)/6,2));
        unsigned r=(12+30*fibers+65*glow)*shade;
        unsigned g=(75+100*fibers+35*glow)*shade;
        unsigned b=(125+110*fibers)*shade;
        iris[(y+IRIS_R)*IRIS_SIZE+x+IRIS_R]=gfx_color((r<<16)|(g<<8)|b);
    }
}
void eye_auto(bool enabled) { automatic=enabled; next_gaze=0; }
void eye_look(int x,int y) { automatic=false; target_x=x; target_y=y; }
void eye_blink(int64_t now) { blink_start=now; }
void eye_pupil(int radius) { pupil=radius; }
bool eye_is_auto(void) { return automatic; }
int eye_x(void) { return (int)gaze_x; }
int eye_y(void) { return (int)gaze_y; }
int eye_lid_percent(void) { return lid_percent; }
void eye_render(int64_t now)
{
    static const int targets[][2]={{0,0},{-65,-12},{45,-25},{75,18},{0,0},{-30,45},{20,-50},{-70,15}};
    if (automatic && now >= next_gaze) {
        target_x=targets[gaze_step%8][0]; target_y=targets[gaze_step%8][1];
        next_gaze=now+1100000+(gaze_step%3)*270000;
        ++gaze_step;
    }
    if (next_blink == 0) next_blink=now+2800000;
    if (automatic && now >= next_blink) {
        blink_start=now;
        next_blink=now+3300000+(gaze_step%3)*370000;
    }
    float dt=last_frame ? fminf((now-last_frame)/1000000.0f,0.1f) : 0.033f;
    last_frame=now;
    float ease=1-expf(-dt*10);
    gaze_x += (target_x-gaze_x)*ease; gaze_y += (target_y-gaze_y)*ease;
    float phase=(now-blink_start)/260000.0f;
    float closed=phase>=0 && phase<1 ? sinf(phase*3.14159265f) : 0;
    closed=closed*closed;
    lid_percent=(int)(closed*100);
    int cx=180+(int)(gaze_x*0.35f), cy=180+(int)(gaze_y*0.23f);
    uint16_t *frame=gfx_buffer();
    memcpy(frame,base,360*360*2);
    uint16_t pupil_color=gfx_color(0x010309), highlight=gfx_color(0xF8FFFF);
    for (int y=-IRIS_R; y<=IRIS_R; ++y) for (int x=-IRIS_R; x<=IRIS_R; ++x) {
        int px=cx+x,py=cy+y,d2=x*x+y*y;
        if (d2>IRIS_R*IRIS_R || abs(py-180)>=aperture[px]) continue;
        uint16_t c=d2<=pupil*pupil ? pupil_color : iris[(y+IRIS_R)*IRIS_SIZE+x+IRIS_R];
        if ((x+15)*(x+15)+(y+18)*(y+18)<6*6 ||
            (x-11)*(x-11)+(y-13)*(y-13)<3*3) c=highlight;
        frame[py*360+px]=c;
    }
    uint16_t lid=gfx_color(0x030810), rim=gfx_color(0x153348);
    for (int x=62; x<298; ++x) {
        int open=(int)(aperture[x]*(1-closed));
        for (int y=180-aperture[x]; y<=180+aperture[x]; ++y)
            if (abs(y-180)>=open) frame[y*360+x]=lid;
        frame[(180-open)*360+x]=rim;
        frame[(180+open)*360+x]=rim;
    }
}
