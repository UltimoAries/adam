#include "ear.h"
#include "graphics.h"
#include <math.h>

static float bob;
static void curve(float x0,float y0,float x1,float y1,float x2,float y2,float x3,float y3,int radius,unsigned color)
{
    for (int i=0;i<=42;++i) {
        float t=i/42.0f,u=1-t;
        float x=u*u*u*x0+3*u*u*t*x1+3*u*t*t*x2+t*t*t*x3;
        float y=u*u*u*y0+3*u*u*t*y1+3*u*t*t*y2+t*t*t*y3+bob;
        gfx_circle((int)x,(int)y,radius,color,true);
    }
}
static void outer(int radius,unsigned color)
{
    curve(165,231,139,227,126,202,129,169,radius,color);
    curve(129,169,128,133,140,111,170,108,radius,color);
    curve(170,108,205,102,226,120,225,149,radius,color);
    curve(225,149,225,177,204,184,199,205,radius,color);
    curve(199,205,198,229,183,245,165,231,radius,color);
}
void ear_render(int64_t now,unsigned input_level)
{
    float time=now/1000000.0f;
    bob=1.5f*sinf(time*2.8f);
    float level=fminf(1,sqrtf(input_level/1600.0f));
    gfx_clear(0x030810);
    gfx_circle(177,177,78,0x0B1C27,true);
    outer(10,0x493E39);outer(6,0xE9B48F);outer(3,0xFBD5B2);
    curve(149,150,149,126,186,117,204,139,4,0xD39779);
    curve(204,139,219,158,192,167,181,180,4,0xD39779);
    curve(181,180,169,194,183,202,173,218,4,0xD39779);
    curve(153,164,150,148,166,139,178,146,3,0xFBD5B2);
    curve(178,146,190,151,181,163,172,168,3,0xFBD5B2);
    curve(151,179,165,174,166,190,155,198,5,0xE9B48F);
    gfx_circle(170,187+(int)bob,7,0x142B35,true);
    // Sound waves breathe gently and grow brighter with the actual mic level.
    for (int wave=0;wave<3;++wave) {
        float phase=.5f+.5f*sinf(time*4-wave*.7f);
        float r=18+wave*13+level*3+phase*2;
        float brightness=.30f+.50f*level+.15f*phase;
        unsigned color=((unsigned)(62*brightness)<<16)|((unsigned)(217*brightness)<<8)|(unsigned)(218*brightness);
        for (int i=-28;i<=28;++i) {
            float angle=i*.025f;
            int x=222+(int)(r*cosf(angle)),y=173+(int)(r*sinf(angle));
            gfx_circle(x,y,1,color,true);
        }
    }
}
