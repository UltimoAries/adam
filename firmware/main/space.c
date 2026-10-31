#include "space.h"
#include "audio.h"
#include "graphics.h"
#include "headend.h"
#include "esp_heap_caps.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <time.h>
#include <ctype.h>

enum { TIMER_RING_WINDOW_US = 60000000LL, MAX_PREVIEW_TIMERS = 4 };

static uint16_t *ripple;
static uint8_t wave[256];
static unsigned sky_rows[360];
static int16_t cloud_left[49],cloud_right[49];
static int16_t ring_inner[360],ring_outer[360];
static int preview_minute;
static int64_t preview_until;
static int preview_weather;
static int64_t weather_preview_until;
static headend_timer_t preview_timers[MAX_PREVIEW_TIMERS];
static unsigned preview_timer_count,preview_seq,timer_offset;
static char confirm_id[17];
static int64_t confirm_until;
static const unsigned ink=0xFFFFFF,outline=0x000000;
static const unsigned CREAM=0xFFF2D4;

static void label(int x,int y,int scale,unsigned color,const char *value)
{
    // Solid white lettering with a complete two-pixel black edge, including
    // diagonals. Draw the fill last so the outline never thins the glyphs.
    for (int dy=-2;dy<=2;++dy) for (int dx=-2;dx<=2;++dx)
        if ((dx || dy) && dx*dx+dy*dy<=4) gfx_text(x+dx,y+dy,scale,outline,value);
    gfx_text(x,y,scale,color,value);
}

static void text(int x,int y,int scale,unsigned color,const char *value)
{ label(x-((int)strlen(value)*6-1)*scale/2,y,scale,color,value); }

static void rounded_box(int x,int y,int w,int h,int r,unsigned color)
{
    gfx_rect(x+r,y,w-2*r,h,color); gfx_rect(x,y+r,w,h-2*r,color);
    gfx_circle(x+r,y+r,r,color,true); gfx_circle(x+w-r-1,y+r,r,color,true);
    gfx_circle(x+r,y+h-r-1,r,color,true); gfx_circle(x+w-r-1,y+h-r-1,r,color,true);
}

static unsigned blend(unsigned a,unsigned b,int amount)
{
    unsigned rgb=0;
    for (int shift=0;shift<=16;shift+=8) {
        int from=(a>>shift)&255,to=(b>>shift)&255;
        rgb|=(unsigned)(from+(to-from)*amount/255)<<shift;
    }
    return rgb;
}

static uint16_t sky_color(unsigned rgb,int threshold)
{
    // Ordered RGB565 dithering avoids broad horizontal bands in the sky.
    int r=((rgb>>16)&255)+(threshold>>1);
    int g=((rgb>>8)&255)+(threshold>>2),b=(rgb&255)+(threshold>>1);
    return gfx_color(((r>255?255:r)<<16)|((g>255?255:g)<<8)|(b>255?255:b));
}

void space_init(void)
{
    if (ripple) return;
    ripple=heap_caps_malloc(360*360*sizeof(*ripple),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    for (int n=0;n<256;++n) wave[n]=(uint8_t)(127.5f+127.5f*sinf(n*6.2831853f/256));
    // Cache the edge spans once; glow rendering skips the entire screen center.
    for (int y=0;y<360;++y) {
        int dy=y-180,squared=dy*dy;
        ring_outer[y]=squared<=179*179?(int)sqrtf(179*179-squared):-1;
        ring_inner[y]=squared<155*155?(int)sqrtf(155*155-squared):-1;
    }
    // Precompute one rounded cloud silhouette; rendering only stretches spans.
    static const int lobes[][4]={{-39,5,27,12},{-17,-3,25,20},{12,-3,28,24},{39,5,28,14},{0,10,51,14}};
    for (int row=0;row<49;++row) {
        int y=row-24,left=100,right=-100;
        for (unsigned n=0;n<sizeof(lobes)/sizeof(lobes[0]);++n) {
            int dy=y-lobes[n][1],ry=lobes[n][3];
            if (dy*dy>ry*ry) continue;
            int half=(int)(lobes[n][2]*sqrtf(1.0f-(float)(dy*dy)/(ry*ry)));
            if (lobes[n][0]-half<left) left=lobes[n][0]-half;
            if (lobes[n][0]+half>right) right=lobes[n][0]+half;
        }
        cloud_left[row]=left;cloud_right[row]=right;
    }
    if (!ripple) return;
    for (int y=0;y<360;++y) for (int x=0;x<360;++x) {
        int a=(int)(sqrtf((x-78)*(x-78)+(y-268)*(y-268))*2.1f);
        int b=(int)(sqrtf((x-280)*(x-280)+(y-70)*(y-70))*1.4f);
        ripple[y*360+x]=(a&255)|((b&255)<<8);
    }
}

void space_preview(int minute,int64_t now)
{
    preview_minute=minute;
    preview_until=minute>=0?now+60000000:0;
}

void space_weather_preview(int code,int64_t now)
{
    preview_weather=code;
    weather_preview_until=code>=0?now+60000000:0;
}

void space_timer_preview(int seconds,const char *name,int64_t now)
{
    if (seconds<0) {
        preview_timer_count=0;
        confirm_id[0]=0;
        confirm_until=0;
        timer_offset=0;
        return;
    }
    if (preview_timer_count==MAX_PREVIEW_TIMERS) {
        memmove(&preview_timers[0],&preview_timers[1],(MAX_PREVIEW_TIMERS-1)*sizeof(preview_timers[0]));
        --preview_timer_count;
    }
    headend_timer_t *dst=&preview_timers[preview_timer_count++];
    snprintf(dst->id,sizeof(dst->id),"prev%u",++preview_seq);
    const char *raw=(name && name[0])?name:"TIMER";
    size_t i=0;
    for (;raw[i] && i<sizeof(dst->label)-1;++i) {
        unsigned char ch=(unsigned char)raw[i];
        dst->label[i]=(ch>=32 && ch<=126)?(char)toupper(ch):' ';
    }
    dst->label[i]=0;
    dst->duration_s=seconds>0?seconds:1;
    dst->deadline_us=now+(int64_t)seconds*1000000LL;
}

static unsigned collect_timers(const headend_panel_t *p,int64_t now,
                               headend_timer_t *out,bool *is_preview,unsigned max_out)
{
    unsigned pw=0;
    for (unsigned i=0;i<preview_timer_count;++i) {
        if (now-preview_timers[i].deadline_us<TIMER_RING_WINDOW_US)
            preview_timers[pw++]=preview_timers[i];
    }
    preview_timer_count=pw;

    unsigned count=0;
    for (unsigned i=0;i<p->timer_count && count<max_out;++i) {
        if (now-p->timers[i].deadline_us<TIMER_RING_WINDOW_US) {
            out[count]=p->timers[i];
            if (is_preview) is_preview[count]=false;
            ++count;
        }
    }
    for (unsigned i=0;i<preview_timer_count && count<max_out;++i) {
        out[count]=preview_timers[i];
        if (is_preview) is_preview[count]=true;
        ++count;
    }
    for (unsigned i=1;i<count;++i) {
        headend_timer_t cur=out[i];
        bool cur_prev=is_preview?is_preview[i]:false;
        unsigned j=i;
        while (j>0 && out[j-1].deadline_us>cur.deadline_us) {
            out[j]=out[j-1];
            if (is_preview) is_preview[j]=is_preview[j-1];
            --j;
        }
        out[j]=cur;
        if (is_preview) is_preview[j]=cur_prev;
    }
    return count;
}

bool space_timer_ringing(int64_t now)
{
    for (unsigned i=0;i<preview_timer_count;++i) {
        int64_t elapsed=now-preview_timers[i].deadline_us;
        if (elapsed>=0 && elapsed<TIMER_RING_WINDOW_US) return true;
    }
    headend_panel_t p;headend_panel(&p);
    for (unsigned i=0;i<p.timer_count;++i) {
        int64_t elapsed=now-p.timers[i].deadline_us;
        if (elapsed>=0 && elapsed<TIMER_RING_WINDOW_US) return true;
    }
    return false;
}

static void timer_bar_rect(unsigned vis,unsigned row,int *bx,int *by,int *bw,int *bh)
{
    *bx=82; *bw=196;
    if (vis==1) { *by=230; *bh=38; }
    else { *by=216+(int)row*40; *bh=36; }
}

bool space_timer_touch(int x,int y,int64_t now)
{
    if (space_timer_ringing(now)) {
        unsigned pw=0;
        for (unsigned i=0;i<preview_timer_count;++i) {
            int64_t elapsed=now-preview_timers[i].deadline_us;
            if (elapsed<0 || elapsed>=TIMER_RING_WINDOW_US)
                preview_timers[pw++]=preview_timers[i];
        }
        preview_timer_count=pw;
        headend_dismiss_ringing(now);
        audio_stop();
        confirm_id[0]=0;
        confirm_until=0;
        return true;
    }
    headend_panel_t p;headend_panel(&p);
    headend_timer_t list[HEADEND_MAX_TIMERS+MAX_PREVIEW_TIMERS];
    bool prev[HEADEND_MAX_TIMERS+MAX_PREVIEW_TIMERS];
    unsigned count=collect_timers(&p,now,list,prev,HEADEND_MAX_TIMERS+MAX_PREVIEW_TIMERS);
    if (!count) {
        confirm_id[0]=0;
        timer_offset=0;
        return false;
    }
    if (confirm_id[0] && now>=confirm_until) confirm_id[0]=0;
    unsigned vis=count<2?count:2;
    if (count<=2) timer_offset=0;
    else timer_offset%=count;
    for (unsigned row=0;row<vis;++row) {
        unsigned idx=(timer_offset+row)%count;
        int bx,by,bw,bh;
        timer_bar_rect(vis,row,&bx,&by,&bw,&bh);
        if (x>=bx+bw-46 && x<bx+bw+4 && y>=by-2 && y<by+bh+2) {
            if (confirm_id[0] && !strcmp(confirm_id,list[idx].id) && now<confirm_until) {
                if (prev[idx]) {
                    unsigned pw=0;
                    for (unsigned i=0;i<preview_timer_count;++i)
                        if (strcmp(preview_timers[i].id,list[idx].id)) preview_timers[pw++]=preview_timers[i];
                    preview_timer_count=pw;
                } else {
                    headend_dismiss_timer(list[idx].id);
                }
                confirm_id[0]=0;
                confirm_until=0;
            } else {
                snprintf(confirm_id,sizeof(confirm_id),"%s",list[idx].id);
                confirm_until=now+3000000LL;
            }
            return true;
        }
        if (x>=bx-2 && x<bx+bw-46 && y>=by-2 && y<by+bh+2) {
            bool was_armed=confirm_id[0]!=0;
            confirm_id[0]=0;
            if (count>2) {
                timer_offset=(timer_offset+1)%count;
                return true;
            }
            if (was_armed) return true;
        }
    }
    return false;
}

static void draw_timer_bars(const headend_timer_t *list,unsigned count,int64_t now)
{
    if (confirm_id[0] && now>=confirm_until) confirm_id[0]=0;
    unsigned vis=count<2?count:2;
    if (count<=2) timer_offset=0;
    else timer_offset%=count;
    bool pulse=((now/350000LL)&1)!=0;
    for (unsigned row=0;row<vis;++row) {
        unsigned idx=(timer_offset+row)%count;
        const headend_timer_t *t=&list[idx];
        bool ringing=now>=t->deadline_us;
        bool armed=!ringing && confirm_id[0] && !strcmp(confirm_id,t->id) && now<confirm_until;
        int bx,by,bw,bh;
        timer_bar_rect(vis,row,&bx,&by,&bw,&bh);
        unsigned border=ringing?(pulse?0xFFE38E:0xF4BC62):armed?0xF4BC62:0x284D63;
        unsigned fill_col=ringing?(pulse?0x3A260D:0x261909):0x0D1F2D;
        rounded_box(bx,by,bw,bh,7,border);
        rounded_box(bx+1,by+1,bw-2,bh-2,6,fill_col);

        int px=bx+8,py=by+bh-4,pw=bw-16;
        gfx_rect(px,py,pw,2,0x19364A);
        if (ringing) gfx_rect(px,py,pw,2,0xF4BC62);
        else {
            int64_t total_us=(int64_t)(t->duration_s>0?t->duration_s:1)*1000000LL;
            int64_t rem_us=t->deadline_us-now;
            if (rem_us<0) rem_us=0;
            if (rem_us>total_us) rem_us=total_us;
            int fill_w=(int)(rem_us*pw/total_us);
            if (fill_w>0) gfx_rect(px,py,fill_w,2,armed?0xF4BC62:0x40D9DC);
        }

        char hdr[40];
        if (count>2) snprintf(hdr,sizeof(hdr),"%u/%u %.14s",idx+1,count,t->label);
        else snprintf(hdr,sizeof(hdr),"%.18s",t->label);
        gfx_text(bx+8,by+4,1,ringing?0xFFE38E:0x9ED8E8,hdr);

        char rem_txt[16];
        if (ringing) snprintf(rem_txt,sizeof(rem_txt),"00:00 DONE");
        else {
            int rem_s=(int)((t->deadline_us-now+999999LL)/1000000LL);
            if (rem_s>=3600) snprintf(rem_txt,sizeof(rem_txt),"%d:%02d:%02d",rem_s/3600,(rem_s%3600)/60,rem_s%60);
            else snprintf(rem_txt,sizeof(rem_txt),"%02d:%02d",rem_s/60,rem_s%60);
        }
        gfx_text(bx+8,by+14,2,ringing?(pulse?0xFFFFFF:0xFFE38E):0xFFFFFF,rem_txt);

        int btn_x=bx+bw-42,btn_y=by+4,btn_w=38,btn_h=bh-10;
        unsigned btn_edge=(ringing || armed)?0xF4BC62:0x284D63;
        unsigned btn_fill=ringing?0x573912:armed?0x3D2A10:0x152E42;
        rounded_box(btn_x,btn_y,btn_w,btn_h,5,btn_edge);
        rounded_box(btn_x+1,btn_y+1,btn_w-2,btn_h-2,4,btn_fill);
        if (ringing) {
            gfx_text(btn_x+(btn_w-23)/2,btn_y+(btn_h-7)/2,1,0xFFE38E,"STOP");
        } else if (armed) {
            gfx_text(btn_x+(btn_w-17)/2,btn_y+(btn_h-7)/2,1,0xFFE38E,"OK?");
        } else {
            gfx_text(btn_x+(btn_w-10)/2,btn_y+(btn_h-14)/2,2,0xB8D4E3,"X");
        }
    }
}

static void sky_colors(int seconds,unsigned *top,unsigned *horizon,int *day,int *stars)
{
    // A decorative local-time cycle, subsequently tinted by current weather.
    // Interpolate by seconds so colors never jump at an hour boundary.
    static const struct { int minute; unsigned top,horizon; int day,stars; } keys[]={
        {   0,0x02040A,0x09111D,  0,255},
        { 300,0x030711,0x121D2D,  0,255},
        { 360,0x15284A,0xA65E3D, 50,160},
        { 420,0x1E5B99,0xF6BF7D,150,  0},
        { 540,0x1469B2,0x8BCFEC,240,  0},
        { 720,0x0F66BA,0x9AD8F4,255,  0},
        { 960,0x1E62A9,0xBCD3D9,210,  0},
        {1080,0x1C3055,0xE5834B,100, 40},
        {1140,0x060D1D,0x2A2A39,  8,200},
        {1200,0x02040A,0x09111D,  0,255},
        {1440,0x02040A,0x09111D,  0,255}
    };
    unsigned i=0;
    while (i+2<sizeof(keys)/sizeof(keys[0]) && seconds>=keys[i+1].minute*60) ++i;
    int elapsed=seconds-keys[i].minute*60,span=(keys[i+1].minute-keys[i].minute)*60;
    int amount=elapsed*255/span;
    *top=blend(keys[i].top,keys[i+1].top,amount);
    *horizon=blend(keys[i].horizon,keys[i+1].horizon,amount);
    *day=keys[i].day+(keys[i+1].day-keys[i].day)*elapsed/span;
    *stars=keys[i].stars+(keys[i+1].stars-keys[i].stars)*elapsed/span;
}

static void cloud(int x,int y,int scale,int day,int cover)
{
    int height=48*scale/100;
    for (int row=0;row<=height;++row) {
        int cy=y+(row-height/2),index=row*48/height;
        if (cy<0 || cy>=360 || cloud_left[index]>cloud_right[index]) continue;
        int left=x+cloud_left[index]*scale/100,right=x+cloud_right[index]*scale/100;
        int edge=row<height-row?row:height-row;
        int alpha=(10+day*165/255)*(edge<4?edge:4)/4;
        unsigned shade=blend(0xF9FCFF,0xB8D3E5,index*170/48);
        shade=blend(shade,0x879EAF,cover*130/255);
        shade=blend(0x58677D,shade,day);
        // Defined soft edges and a bright upper rim give the clouds a puffy,
        // illustrated shape instead of dissolving into a gray wash.
        alpha=alpha*3/4+35;
        gfx_rect(left,cy,right-left+1,1,blend(sky_rows[cy],shade,alpha));
        // Feather the silhouette edges without adding a full-screen blend pass.
        for (int n=0;n<3;++n) {
            unsigned color=blend(sky_rows[cy],shade,alpha*(n+1)/4);
            gfx_pixel(left+n,cy,color);gfx_pixel(right-n,cy,color);
        }
        if (edge>3) {
            unsigned edge_color=blend(sky_rows[cy],0x29435C,day*90/255+30);
            gfx_pixel(left,cy,edge_color);gfx_pixel(right,cy,edge_color);
            if (index<18) gfx_rect(left+3,cy,right-left-5,1,blend(sky_rows[cy],0xF8FCFF,alpha));
        }
    }
}

static void sun_disc(int x,int y,int radius,unsigned color,int alpha)
{
    for (int dy=-radius;dy<=radius;++dy) {
        int cy=y+dy;
        if (cy<0 || cy>=360) continue;
        int half=(int)sqrtf((float)(radius*radius-dy*dy));
        gfx_rect(x-half,cy,2*half+1,1,blend(sky_rows[cy],color,alpha));
    }
}

static void smile(int x,int y,unsigned color)
{
    gfx_circle(x-5,y-3,1,color,true);gfx_circle(x+5,y-3,1,color,true);
    gfx_line(x-4,y+3,x-2,y+5,color);gfx_line(x-2,y+5,x+2,y+5,color);
    gfx_line(x+2,y+5,x+4,y+3,color);
}

static void moon(int x,int y)
{
    gfx_circle(x,y,18,0x34425E,true);gfx_circle(x,y,16,0xFFF2BE,true);
    // Restore the real row gradient through the crescent cutout.
    for (int dy=-16;dy<=16;++dy) {
        int cy=y-5+dy;
        if (cy<0 || cy>=360) continue;
        int half=(int)sqrtf(256-dy*dy);
        gfx_rect(x+8-half,cy,half*2+1,1,sky_rows[cy]);
    }
    gfx_circle(x-8,y+1,1,0x554B42,true);
    gfx_line(x-7,y+7,x-4,y+9,0x554B42);
}

static void stars(int64_t now,int visibility,bool twinkle)
{
    static const uint16_t points[][2]={{91,36},{230,30},{69,73},{286,71},{46,111},{314,130},{28,177},
        {326,192},{51,242},{310,258},{97,298},{263,307},{129,58},{217,86},{287,213},{74,204},{237,332}};
    for (unsigned n=0;n<sizeof(points)/sizeof(points[0]);++n) {
        int x=points[n][0],y=points[n][1];
        unsigned color=n%3?0x8794B5:CREAM;
        if (twinkle) {
            int brightness=visibility*(150+wave[((unsigned)(now/40000)+n*37)&255]*105/255)/255;
            color=blend(sky_rows[y],color,brightness);
        }
        gfx_pixel(x,y,color);
        if (!(n%4)) { gfx_line(x-2,y,x+2,y,color);gfx_line(x,y-2,x,y+2,color); }
    }
}

static int cloud_cover(int code)
{
    if (code<0 || code==0) return 0;
    if (code==1) return 35;
    if (code==2) return 125;
    return 255;
}

static void precipitation(int64_t now,int code,int day)
{
    bool snow=(code>=71 && code<=77) || code==85 || code==86;
    bool drizzle=code>=51 && code<=57;
    bool rain=(code>=61 && code<=67) || (code>=80 && code<=82) || code>=95;
    if (!snow && !drizzle && !rain) return;
    bool heavy=code==65 || code==67 || code==82 || code>=95;
    int count=snow?20:drizzle?12:heavy?48:30;
    int speed=snow?30:drizzle?55:heavy?230:150;
    int length=snow?0:drizzle?3:heavy?14:9;
    unsigned phase=(unsigned)(now/1000);
    uint32_t seed=0x51A7;
    for (int i=0;i<count;++i) {
        seed=seed*1664525u+1013904223u;
        int y=(int)(((uint64_t)phase*speed/1000+(seed>>9))%420)-30;
        int x=(int)((seed+(unsigned)(y+30)/3)%400)-20;
        if (snow) x+=(int)wave[((phase/60)+i*19)&255]/18-7;
        if (y<0 || y>=360) continue;
        unsigned shade=snow?blend(0xA1B5C9,0xF8FCFF,day):blend(0x4CB8FF,0x268FFF,day);
        unsigned color=blend(sky_rows[y],shade,snow?205:drizzle?215:235);
        if (snow) gfx_circle(x,y,i%3?1:2,color,true);
        else {
            gfx_line(x,y,x-length/3,y+length,color);
            // Bright blue tips remain visible against gray storm clouds.
            int tip=drizzle?0:heavy?3:2;
            gfx_line(x-(length-tip)/3,y+length-tip,x-length/3,y+length,blend(sky_rows[y],0x9BDEFF,235));
        }
    }
}

static void sky(int64_t now,int seconds,int code)
{
    unsigned top,horizon;
    int day,starlight;
    sky_colors(seconds,&top,&horizon,&day,&starlight);
    int cover=cloud_cover(code);
    bool fog=code==45 || code==48;
    unsigned wet_top=blend(0x0C1523,0x68899D,day),wet_horizon=blend(0x1E2A3B,0xBDCED6,day);
    if (fog) { wet_top=blend(0x1C2736,0x9BB0BE,day);wet_horizon=blend(0x2A3544,0xD1DCE1,day); }
    top=blend(top,wet_top,cover);horizon=blend(horizon,wet_horizon,cover);
    starlight=starlight*(255-cover)/255;
    uint16_t *frame=gfx_buffer();
    static const uint8_t dither[4][4]={{0,8,2,10},{12,4,14,6},{3,11,1,9},{15,7,13,5}};
    for (int y=0;y<360;++y) {
        sky_rows[y]=blend(top,horizon,y*255/359);
        uint16_t colors[4];
        for (int n=0;n<4;++n) colors[n]=sky_color(sky_rows[y],dither[y&3][n]);
        for (int x=0;x<360;x+=4) {
            uint16_t *p=frame+y*360+x;
            p[0]=colors[0];p[1]=colors[1];p[2]=colors[2];p[3]=colors[3];
        }
    }
    if (starlight) stars(now,starlight,true);
    // Keep the golden sun clear of the information column, in the upper right.
    if (cover<255 && seconds>6*3600 && seconds<19*3600) {
        int x=282,y=78+(wave[((unsigned)(now/55000))&255]-128)/64;
        int edge=seconds-6*3600<19*3600-seconds?seconds-6*3600:19*3600-seconds;
        int alpha=edge<1800?edge*255/1800:255;
        alpha=alpha*(255-cover)/255;
        unsigned color=blend(0xFFAD32,0xFFDA35,day);
        for (int radius=34;radius>18;--radius) {
            int glow=(35-radius)*(35-radius)*85/(16*16);
            sun_disc(x,y,radius,color,alpha*glow/255);
        }
        // A few short rays and a face bring back the friendly illustrated sun.
        for (int n=0;n<8;++n) {
            float a=n*3.14159265f/4;
            int sx=x+(int)(23*cosf(a)),sy=y+(int)(23*sinf(a));
            int ex=x+(int)(27*cosf(a)),ey=y+(int)(27*sinf(a));
            gfx_line(sx,sy,ex,ey,blend(sky_rows[y],0xFFD143,alpha));
        }
        sun_disc(x,y,19,0xB77A16,alpha);
        sun_disc(x,y,17,color,alpha);
        if (alpha>190) {
            smile(x,y,0x594018);
            gfx_circle(x-10,y+2,2,0xFFB34C,true);gfx_circle(x+10,y+2,2,0xFFB34C,true);
        }
    }
    else if (cover<125 && day<30) moon(282,78);
    unsigned phase=(unsigned)(now/300000);
    cloud(54+(int)wave[phase&255]*32/255,70,65+cover/5,day,cover);
    cloud(330-(int)wave[(phase+90)&255]*28/255,218,65+cover/5,day,cover);
    cloud(122+(int)wave[(phase+160)&255]*50/255,327,95+cover/6,day,cover);
    if (cover>125) {
        cloud(220+(int)wave[(phase+40)&255]*28/255,31,140,day,cover);
        cloud(45+(int)wave[(phase+120)&255]*24/255,170,fog?220:125,day,cover);
    }
    precipitation(now,code,day);
}

static void background(int64_t now,space_state_t activity,int local_seconds,int code)
{
    sky(now,local_seconds,code);
    if (activity==SPACE_IDLE || !ripple) return;
    // Keep the live sky, clouds and precipitation beneath the same moving
    // ripple pattern. Only the mist changes color; lettering is drawn afterward.
    unsigned tint=activity==SPACE_LISTENING?0x0CBE2C:0x0E5AF5;
    unsigned color=((tint>>8)&0xF800)|((tint>>5)&0x07E0)|((tint>>3)&31);
    const unsigned lanes=0x07E0F81F;
    unsigned mist=(color|(color<<16))&lanes;
    uint8_t opacity[256];
    for (unsigned n=0;n<256;++n) opacity[n]=4+n*15/255;
    unsigned phase=(unsigned)(now/35000);
    uint16_t *frame=gfx_buffer();
    for (int n=0;n<360*360;++n) {
        unsigned d=ripple[n];
        unsigned shade=(wave[((d&255)+phase)&255]+wave[((d>>8)-phase/2)&255])/2;
        // The framebuffer stores RGB565 in LCD byte order. Blend its existing
        // pixels directly, keeping at least 40% of the sky even at ripple crests.
        // Spaced channel lanes allow one multiply for all three components;
        // five-bit opacity keeps their products from overlapping.
        unsigned pixel=(uint16_t)((frame[n]>>8)|(frame[n]<<8));
        unsigned base=(pixel|(pixel<<16))&lanes;
        unsigned mixed=(base+(((mist-base)*opacity[shade])>>5))&lanes;
        uint16_t result=(mixed>>16)|mixed;
        frame[n]=(result>>8)|(result<<8);
    }
}

static const char *condition(int code)
{
    if (code==0) return "CLEAR SKIES";
    if (code==1) return "MOSTLY CLEAR";
    if (code==2) return "PARTLY CLOUDY";
    if (code==3) return "OVERCAST";
    if (code==45 || code==48) return "FOGGY";
    if (code>=51 && code<=57) return "DRIZZLE";
    if (code>=61 && code<=67) return "RAIN";
    if (code>=71 && code<=77) return "SNOW";
    if (code>=80 && code<=82) return "RAIN SHOWERS";
    if (code==85 || code==86) return "SNOW SHOWERS";
    if (code>=95) return "THUNDERSTORMS";
    return "WEATHER";
}

static void device_name(const char *name)
{
    size_t length=strlen(name);
    if (length<=19) text(180,44,length<=12?3:2,ink,name);
    else {
        // Keep long paired-unit names readable instead of shrinking to 7px.
        char first[25],second[25];int split=12;
        for (int n=8;n<=17;++n) if (name[n]==' ') split=n;
        snprintf(first,sizeof(first),"%.*s",split,name);
        snprintf(second,sizeof(second),"%s",name+split+(name[split]==' '));
        text(180,35,2,ink,first);text(180,54,2,ink,second);
    }
}

static void weather_icon(int x,int y,int code,bool night)
{
    // Small sticker-like weather symbols; each stays inside the weather row.
    const unsigned edge=0x122339;
    if (code<=2) {
        if (night) {
            gfx_circle(x-5,y-5,12,edge,true);gfx_circle(x-5,y-5,10,0xFFF0AC,true);
            gfx_circle(x-1,y-9,8,edge,true);
        } else {
            for (int n=0;n<8;++n) {
                float a=n*3.14159265f/4;
                gfx_line(x-5+(int)(15*cosf(a)),y-5+(int)(15*sinf(a)),
                         x-5+(int)(18*cosf(a)),y-5+(int)(18*sinf(a)),0xFFD348);
            }
            gfx_circle(x-5,y-5,13,edge,true);gfx_circle(x-5,y-5,11,0xFFDA35,true);
            if (!code) smile(x-5,y-5,0x604010);
        }
        if (!code) return;
    }
    // Outline the whole cloud before filling its lobes, avoiding dark seams.
    gfx_circle(x-10,y-1,10,edge,true);gfx_circle(x,y-7,13,edge,true);
    gfx_circle(x+12,y,9,edge,true);gfx_rect(x-12,y,25,11,edge);
    gfx_circle(x-10,y-1,8,0xDFEFFA,true);gfx_circle(x,y-7,11,0xF5FAFF,true);
    gfx_circle(x+12,y,7,0xDFEFFA,true);gfx_rect(x-12,y,25,9,0xDFEFFA);
    smile(x,y-2,edge);
    if (code==45 || code==48) {
        gfx_line(x-17,y+14,x+14,y+14,0xD5DFEA);gfx_line(x-10,y+18,x+19,y+18,0xD5DFEA);
    } else if (code>=51) {
        bool snow=(code>=71 && code<=77) || code==85 || code==86;
        for (int n=0;n<3;++n) {
            int dx=x-12+n*12;
            if (snow) {
                gfx_line(dx-3,y+15,dx+3,y+15,edge);gfx_line(dx,y+12,dx,y+18,edge);
                gfx_pixel(dx,y+15,0xFFFFFF);
            } else {
                gfx_line(dx+1,y+12,dx-1,y+18,edge);
                gfx_line(dx+2,y+12,dx,y+18,0x67CEFF);
                gfx_circle(dx,y+18,1,0x67CEFF,true);
            }
        }
        if (code>=95) {
            gfx_line(x+4,y+9,x,y+15,0xFFDC43);gfx_line(x,y+15,x+5,y+15,0xFFDC43);
            gfx_line(x+5,y+15,x,y+21,0xFFDC43);
        }
    }
}

static void llm_ring(headend_llm_t provider)
{
    unsigned color=provider==HEADEND_LLM_GPU?0x67F7A0:0xFFE77A;
    uint16_t wire=gfx_color(color),target=(wire>>8)|(wire<<8);
    int tr=(target>>11)&31,tg=(target>>5)&63,tb=target&31;
    uint16_t *frame=gfx_buffer();
    for (int y=1;y<360;++y) {
        int outer=ring_outer[y],inner=ring_inner[y],dy=y-180;
        if (outer<0) continue;
        int spans[4]={180-outer,180+outer,0,-1};
        if (inner>=0) { spans[1]=179-inner;spans[2]=181+inner;spans[3]=180+outer; }
        for (int span=0;span<2;++span) for (int x=spans[span*2];x<=spans[span*2+1];++x) {
            int dx=x-180,distance=dx*dx+dy*dy,alpha=255;
            if (distance<169*169) {
                alpha=(distance-155*155)*255/(169*169-155*155);
                alpha=alpha*alpha/255;
            } else if (distance>176*176) alpha=(179*179-distance)*255/(179*179-176*176);
            // Keep the bright core and full ring size; soften only the halo.
            if (distance<169*169 || distance>176*176) alpha=alpha*3/4;
            if (alpha<=0) continue;
            if (alpha>=255) { frame[y*360+x]=wire;continue; }
            uint16_t pixel=frame[y*360+x],background=(pixel>>8)|(pixel<<8);
            int r=(background>>11)&31,g=(background>>5)&63,b=background&31;
            r+=(tr-r)*alpha/255;g+=(tg-g)*alpha/255;b+=(tb-b)*alpha/255;
            uint16_t mixed=(r<<11)|(g<<5)|b;
            frame[y*360+x]=(mixed>>8)|(mixed<<8);
        }
    }
}

void space_render(int64_t now,space_state_t activity,bool controls)
{
    headend_panel_t p;headend_panel(&p);
    char clock[8]="--:--",date[12]="--- --",weekday[12]="SYNCING",period[4]="";
    int local_seconds=0;
    int64_t epoch=p.unix_time+(p.clock_ready?(now-p.synced_us)/1000000:0);
    if (p.clock_ready) {
        time_t local=(time_t)(epoch+p.utc_offset);struct tm tm;gmtime_r(&local,&tm);
        snprintf(clock,sizeof(clock),"%d:%02d",tm.tm_hour%12?tm.tm_hour%12:12,tm.tm_min);
        snprintf(period,sizeof(period),"%s",tm.tm_hour>=12?"PM":"AM");
        strftime(date,sizeof(date),"%b %d",&tm);
        for (char *s=date;*s;++s) *s=toupper((unsigned char)*s);
        strftime(weekday,sizeof(weekday),"%A",&tm);
        for (char *s=weekday;*s;++s) *s=toupper((unsigned char)*s);
        local_seconds=tm.tm_hour*3600+tm.tm_min*60+tm.tm_sec;
    }
    if (now<preview_until) local_seconds=preview_minute*60;
    bool fresh=p.weather_ready && epoch-p.observed_at<=7200 && now-p.synced_us<1800000000LL;
    int code=fresh?p.weather_code:-1;
    if (now<weather_preview_until) code=preview_weather;
    background(now,activity,local_seconds,code);
    // Persistent route indicator, including idle. Hide stale/unknown status;
    // the separate green/blue activity mist keeps its existing meaning.
    if (p.synced_us && now-p.synced_us<20000000 && p.llm_provider!=HEADEND_LLM_UNKNOWN) {
        llm_ring(p.llm_provider);
    }
    if (p.device_name[0] && !controls) device_name(p.device_name);
    text(180,88,3,ink,weekday);
    text(180,126,3,ink,date);
    int clock_width=((int)strlen(clock)*6-1)*5,period_width=period[0]?32:0;
    int start=180-(clock_width+period_width)/2;
    label(start,166,5,ink,clock);
    if (period[0]) label(start+clock_width+9,187,2,ink,period);
    headend_timer_t active_timers[HEADEND_MAX_TIMERS+MAX_PREVIEW_TIMERS];
    unsigned active_count=collect_timers(&p,now,active_timers,NULL,HEADEND_MAX_TIMERS+MAX_PREVIEW_TIMERS);
    if (active_count>0) {
        draw_timer_bars(active_timers,active_count,now);
    } else if (fresh) {
        const char *description=condition(p.weather_code);
        int scale=strlen(description)<=13?3:2;
        int width=((int)strlen(description)*6-1)*scale;
        int left=180-(width+52)/2;
        weather_icon(left+20,237,p.weather_code,local_seconds<6*3600 || local_seconds>=19*3600);
        label(left+52,228,scale,ink,description);
        char temperature[12];snprintf(temperature,sizeof(temperature),"%d",p.temperature);
        width=((int)strlen(temperature)*6-1)*4;int tstart=180-(width+36)/2;
        label(tstart,272,4,ink,temperature);
        gfx_circle(tstart+width+7,277,5,outline,true);
        gfx_circle(tstart+width+7,277,3,ink,false);
        label(tstart+width+18,279,3,ink,p.units=='C'?"C":"F");
    } else {
        text(180,228,2,ink,!strcmp(p.weather_state,"disabled")?"WEATHER OFF":
            !strcmp(p.weather_state,"loading")?"CHECKING WEATHER":"WEATHER UNAVAILABLE");
        text(180,272,4,ink,"--");
    }
    if (!controls) {
        if (p.synced_us && now-p.synced_us>20000000) text(180,320,1,ink,"HEAD-END OFFLINE");
    }
}
