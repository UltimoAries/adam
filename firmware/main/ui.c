#include "ui.h"
#include "graphics.h"
#include "space.h"
#include "esp_timer.h"
#include "preferences.h"
#include "audio.h"
#include "mic.h"
#include "headend.h"
#include "wake.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>

enum { BG=0x030810, CARD=0x102535, EDGE=0x214458, WHITE=0xE9F4FA,
       MUTED=0x83A3B6, CYAN=0x40D9DC, GOLD=0xF4BC62 };
typedef enum { HOME, SETTINGS, WIFI, KEYBOARD, FORGET, NETWORKS, AUDIO, MICROPHONE, VOICE } page_t;
static page_t page;
static space_state_t home_scene;
static char draft_ssid[33],draft_password[65],edit[65],notice[40];
static bool editing_password;
static unsigned keyboard_mode,tap_count;
static unsigned network_page;
static int highlight;
static int64_t highlight_until,last_press,controls_until;
static const char *const lower_rows[]={"qwertyuiop","asdfghjkl","zxcvbnm"};
static const char *const symbol_rows[]={"1234567890","!@#$%^&*()","-_+=/\\:;.,"};
static const char *const extra_rows[]={"?\"'`~|<>","[]{}",""};

static void centered(int cx,int y,int scale,unsigned color,const char *text)
{ gfx_text(cx-((int)strlen(text)*6-1)*scale/2,y,scale,color,text); }
static bool inside(int x,int y,int bx,int by,int w,int h)
{ return x>=bx && x<bx+w && y>=by && y<by+h; }
static void rounded(int x,int y,int w,int h,int r,unsigned color)
{
    gfx_rect(x+r,y,w-2*r,h,color); gfx_rect(x,y+r,w,h-2*r,color);
    gfx_circle(x+r,y+r,r,color,true); gfx_circle(x+w-r-1,y+r,r,color,true);
    gfx_circle(x+r,y+h-r-1,r,color,true); gfx_circle(x+w-r-1,y+h-r-1,r,color,true);
}
static void button(int x,int y,int w,int h,const char *text,unsigned color)
{
    rounded(x,y,w,h,8,EDGE); rounded(x+1,y+1,w-2,h-2,7,CARD);
    centered(x+w/2,y+(h-14)/2,2,color,text);
}
static void arrow(bool up)
{
    int cy=up?35:325;
    gfx_circle(180,cy,23,CARD,true);
    int dy=up?-5:5;
    for (int i=-1;i<=1;++i) {
        gfx_line(170,cy-dy+i,180,cy+dy+i,WHITE);
        gfx_line(180,cy+dy+i,190,cy-dy+i,WHITE);
    }
}
static void rails(int64_t now)
{
    uint16_t *fb=gfx_buffer();
    for (int y=85;y<=275;++y) {
        int dy=y-180;
        for (int x=6;x<78;++x) {
            int dx=x-180,r2=dx*dx+dy*dy;
            if (r2>174*174 || r2<140*140) continue;
            int segment=y<174?1:y>186?2:0;
            if (!segment) continue;
            bool border=r2>172*172 || r2<142*142 || y==85 || y==275;
            unsigned left=border?0x716040:CARD,right=border?0x27545C:CARD;
            if (now<highlight_until && highlight==segment) left=0x68502D;
            if (now<highlight_until && highlight==segment+2) right=0x15545B;
            fb[y*360+x]=gfx_color(left); fb[y*360+359-x]=gfx_color(right);
        }
    }
    centered(33,119,3,GOLD,"+"); centered(326,119,3,CYAN,"+");
    centered(33,224,3,GOLD,"-"); centered(326,224,3,CYAN,"-");
    char value[8];
    snprintf(value,sizeof(value),"%d",preference_volume()); centered(30,174,2,GOLD,value);
    snprintf(value,sizeof(value),"%d",preference_brightness()); centered(329,174,2,CYAN,value);
    centered(80,278,1,GOLD,"VOLUME"); centered(277,278,1,CYAN,"LIGHT");
}
static void field(int y,const char *label,const char *value)
{
    gfx_text(72,y-15,1,MUTED,label);
    rounded(66,y,228,38,7,EDGE); rounded(67,y+1,226,36,6,CARD);
    char shown[19]; size_t len=strlen(value),take=len>18?18:len;
    memcpy(shown,value+len-take,take); shown[take]=0;
    gfx_text(76,y+12,2,len?WHITE:MUTED,len?shown:"Tap to enter");
}
static void keyboard_row(unsigned row,char *dest)
{
    const char *src=keyboard_mode<2?lower_rows[row]:keyboard_mode==2?symbol_rows[row]:extra_rows[row];
    snprintf(dest,11,"%s",src);
    if (keyboard_mode==1) for (char *p=dest;*p;++p) *p=toupper((unsigned char)*p);
}
void ui_init(void) { page=HOME; }
void ui_page(bool settings)
{
    page=settings?SETTINGS:HOME; notice[0]=0;
    memset(edit,0,sizeof(edit)); memset(draft_password,0,sizeof(draft_password));
    controls_until=0;
}
const char *ui_name(void)
{
    static const char *const names[]={"home","settings","wifi","keyboard","forget","networks","audio","microphone","voice"};
    return names[page];
}
void ui_status(void)
{
    char ip[20]; network_ip(ip,sizeof(ip));
    printf("OK ui page=%s volume=%d brightness=%d wifi=%s ip=%s taps=%u keyboard=%u field=%s scan=%s networks=%u list_page=%u event_stack_min=%u controls=%d scene=%s\n",
           ui_name(),preference_volume(),preference_brightness(),network_state(),ip[0]?ip:"none",
           tap_count,keyboard_mode,editing_password?"password":"ssid",network_scan_state(),network_scan_count(),network_page,network_event_stack_min(),page!=HOME || esp_timer_get_time()<controls_until,
           page==HOME?(home_scene==SPACE_LISTENING?"listening":home_scene==SPACE_PLAYING?"playing":"idle"):"menu");
}
void ui_render(int64_t now)
{
    if (page==HOME) {
        // Keep the listening session green through its short acknowledgement cue.
        space_state_t activity=(headend_listening() || mic_recording())?SPACE_LISTENING:
            (audio_is_playing() || audio_clip_busy())?SPACE_PLAYING:SPACE_IDLE;
        home_scene=activity;
        space_render(now,activity,now<controls_until);
        if (now<controls_until) { rails(now);arrow(true);arrow(false); }
        return;
    } else gfx_clear(BG);
    if (page==SETTINGS || page==AUDIO || page==MICROPHONE || page==VOICE) {
        rails(now); arrow(true); arrow(false);
        char title[24];
        snprintf(title,sizeof(title),"%s",page==VOICE?"VOICE":page==AUDIO?"AUDIO":page==MICROPHONE?"MICROPHONE":"SETTINGS");
        centered(180,77,2,mic_recording()?GOLD:WHITE,title);
        gfx_circle(173,291,3,EDGE,true);
        gfx_circle(187,291,3,page==SETTINGS?CYAN:EDGE,true);
        if (page==AUDIO) {
            centered(180,104,1,MUTED,!audio_is_ready()?"Speaker unavailable":audio_is_playing()?"Playing":"Speaker ready");
            button(76,126,100,42,"CHIME",CYAN);
            button(184,126,100,42,"TONE",WHITE);
            button(76,179,208,42,"MIC TEST",WHITE);
            button(92,233,176,40,"STOP",GOLD);
        } else if (page==MICROPHONE) {
            centered(180,104,1,MUTED,!mic_ready()?"Microphone unavailable":mic_duration_ms()?"Clip ready in memory":"Ready to record");
            for (unsigned ch=0;ch<2;++ch) {
                int y=128+ch*25;
                gfx_text(77,y,1,MUTED,ch?"MIC2":"MIC1");
                float db=20*log10f((mic_level(ch)+1)/32768.0f);
                int width=(int)((db+60)*146/60); if (width<0) width=0; if (width>146) width=146;
                gfx_rect(112,y,166,9,EDGE); gfx_rect(113,y+1,width,7,width>140?GOLD:CYAN);
            }
            char gain[24]; snprintf(gain,sizeof(gain),"INPUT GAIN %u DB",mic_gain_db());
            centered(180,171,1,MUTED,gain);
            button(76,185,208,42,"RECORD 8S",CYAN);
            button(76,239,100,32,"REPLAY",WHITE); button(184,239,100,32,"CLEAR",GOLD);
        } else if (page==VOICE) {
            centered(180,111,2,CYAN,headend_state());
            button(76,149,208,42,"TALK 8S",CYAN);
            button(76,205,100,38,"STOP",GOLD);
            button(184,205,100,38,wake_enabled()?"WAKE ON":"WAKE OFF",CYAN);
            centered(180,251,1,MUTED,wake_label());
            centered(180,265,1,MUTED,"Local wake test");
        } else {
            rounded(76,113,208,60,10,EDGE); rounded(77,114,206,58,9,CARD);
            centered(180,125,2,CYAN,"Wi-Fi"); centered(180,153,1,MUTED,network_state());
            button(76,191,100,40,"HOME",WHITE); button(184,191,100,40,"VOICE",CYAN);
            button(92,241,176,32,"AUDIO",CYAN);
        }
    } else if (page==WIFI) {
        arrow(true); centered(180,72,2,WHITE,"Wi-Fi");
        field(108,"NETWORK NAME",draft_ssid);
        if (!draft_ssid[0]) { gfx_rect(75,119,205,16,CARD); gfx_text(76,120,2,MUTED,"Choose network"); }
        char scan_label[16];
        if (!strcmp(network_scan_state(),"ready")) snprintf(scan_label,sizeof(scan_label),"%u FOUND",network_scan_count());
        else snprintf(scan_label,sizeof(scan_label),"%s",!strcmp(network_scan_state(),"scanning")?"SCAN...":"SCAN");
        button(66,153,110,30,scan_label,CYAN); button(184,153,110,30,"TYPE",WHITE);
        field(201,"PASSWORD",draft_password);
        button(66,252,228,36,"CONNECT",CYAN);
        centered(180,301,1,MUTED,"FORGET NETWORK");
        centered(180,319,1,notice[0]?GOLD:MUTED,notice[0]?notice:network_state());
        char ip[20]; network_ip(ip,sizeof(ip)); centered(180,335,1,CYAN,ip);
    } else if (page==NETWORKS) {
        arrow(true); centered(180,73,2,WHITE,"NETWORKS");
        unsigned count=network_scan_count(),pages=(count+2)/3;
        if (!pages) pages=1;
        if (network_page>=pages) network_page=pages-1;
        for (unsigned row=0;row<3;++row) {
            const network_ap_t *ap=network_scan_result(network_page*3+row);
            if (!ap) continue;
            int y=104+row*50;
            rounded(66,y,228,44,7,EDGE); rounded(67,y+1,226,42,6,CARD);
            char name[19]; size_t len=strlen(ap->ssid);
            if (len<=18) snprintf(name,sizeof(name),"%.18s",ap->ssid);
            else { memcpy(name,ap->ssid,13); memcpy(name+13,"..",2); memcpy(name+15,ap->ssid+len-3,3); name[18]=0; }
            for (char *c=name;*c;++c) if ((unsigned char)*c<32 || (unsigned char)*c>126) *c='?';
            gfx_text(74,y+7,2,ap->supported?WHITE:MUTED,name);
            char detail[36]; snprintf(detail,sizeof(detail),"%s  %d dBm",ap->security,ap->rssi);
            gfx_text(75,y+29,1,MUTED,detail);
            int bars=ap->rssi>=-55?4:ap->rssi>=-67?3:ap->rssi>=-78?2:1;
            for (int b=0;b<4;++b) gfx_rect(264+b*5,y+37-b*2,3,3+b*2,b<bars?CYAN:EDGE);
        }
        if (!count) {
            const char *scan=network_scan_state();
            centered(180,154,2,CYAN,!strcmp(scan,"scanning")?"Scanning...":
                     !strcmp(scan,"waiting")?"Please wait":!strcmp(scan,"failed")?"Scan failed":"None found");
            centered(180,183,1,MUTED,"2.4 GHz networks only");
        }
        button(70,257,48,30,"<",network_page?WHITE:MUTED);
        button(242,257,48,30,">",network_page+1<pages?WHITE:MUTED);
        char position[20]; snprintf(position,sizeof(position),"%u / %u",network_page+1,pages);
        centered(180,267,1,MUTED,position);
        button(78,296,96,31,"RESCAN",CYAN); button(186,296,96,31,"MANUAL",WHITE);
    } else if (page==FORGET) {
        arrow(true); centered(180,122,2,WHITE,"Forget Wi-Fi?");
        centered(180,153,1,MUTED,"Removes saved credentials");
        button(80,185,200,40,"KEEP",CYAN); button(80,236,200,40,"FORGET",GOLD);
    } else {
        arrow(true); centered(180,65,2,WHITE,editing_password?"PASSWORD":"NETWORK NAME");
        field(102,"",edit);
        char count[12]; snprintf(count,sizeof(count),"%u/%d",(unsigned)strlen(edit),editing_password?63:32);
        gfx_text(250,88,1,MUTED,count);
        for (int row=0;row<3;++row) {
            char keys[11]; keyboard_row(row,keys); int len=strlen(keys),left=180-len*29/2;
            for (int i=0;i<len;++i) {
                char key[]={keys[i],0}; button(left+i*29,149+row*37,27,33,key,WHITE);
            }
        }
        const char *modes[]={"ABC","123","#+=","abc"};
        button(54,264,57,34,modes[keyboard_mode],CYAN); button(119,264,57,34,"SPC",WHITE);
        button(184,264,57,34,"DEL",GOLD); button(249,264,57,34,"OK",CYAN);
        centered(180,314,1,MUTED,"Top arrow cancels editing");
    }
}
static void open_wifi(void)
{
    snprintf(draft_ssid,sizeof(draft_ssid),"%s",network_ssid());
    snprintf(draft_password,sizeof(draft_password),"%s",network_password());
    notice[0]=0; page=WIFI;
    network_scan();
}
static void begin_edit(bool secret)
{
    editing_password=secret; keyboard_mode=0;
    snprintf(edit,sizeof(edit),"%s",secret?draft_password:draft_ssid); page=KEYBOARD;
}
static void append(char c)
{
    size_t n=strlen(edit);
    if (n<(editing_password?63:32)) { edit[n]=c; edit[n+1]=0; }
}
void ui_touch(const touch_event_t *event,int64_t now)
{
    // One action per press; holding or sliding cannot cross into another control.
    if (!event->pressed || (last_press && now-last_press<90000)) return;
    last_press=now; ++tap_count;
    int x=event->x,y=event->y;
    if (page==HOME) {
        if (space_timer_touch(x,y,now)) return;
        bool visible=now<controls_until;
        controls_until=now+8000000;
        if (!visible) {
            if (inside(x,y,76,100,208,190)) {
                if (headend_busy()) headend_stop();
                else if (mic_recording()) mic_stop();
                else if (audio_is_playing() || audio_clip_busy()) audio_stop();
            }
            return; // First touch reveals controls without changing a level or page.
        }
    }
    if (page==KEYBOARD) {
        if (inside(x,y,142,5,76,60)) { memset(edit,0,sizeof(edit)); page=WIFI; return; }
        if (y>=264 && y<300) {
            if (x>=54 && x<111) keyboard_mode=(keyboard_mode+1)%4;
            else if (x>=119 && x<176) append(' ');
            else if (x>=184 && x<241) { size_t n=strlen(edit); if (n) edit[n-1]=0; }
            else if (x>=249 && x<306) {
                if (editing_password) snprintf(draft_password,sizeof(draft_password),"%s",edit);
                else {
                    if (strcmp(draft_ssid,edit)) memset(draft_password,0,sizeof(draft_password));
                    memcpy(draft_ssid,edit,32); draft_ssid[32]=0;
                }
                memset(edit,0,sizeof(edit)); page=WIFI; notice[0]=0;
            }
            return;
        }
        for (int row=0;row<3;++row) {
            char keys[11]; keyboard_row(row,keys); int len=strlen(keys),left=180-len*29/2;
            if (inside(x,y,left,149+row*37,len*29,35)) {
                append(keys[(x-left)/29]); return;
            }
        }
    } else if (page==WIFI) {
        if (inside(x,y,142,5,76,60)) { ui_page(true); return; }
        if (inside(x,y,66,106,228,42) || inside(x,y,66,151,110,34)) {
            page=NETWORKS; network_page=0;
            if (!strcmp(network_scan_state(),"idle") || !strcmp(network_scan_state(),"failed")) network_scan();
        } else if (inside(x,y,184,151,110,34)) begin_edit(false);
        else if (inside(x,y,66,199,228,42)) begin_edit(true);
        else if (inside(x,y,66,250,228,40)) {
            if (!draft_ssid[0]) snprintf(notice,sizeof(notice),"Enter a network name");
            else if (draft_password[0] && strlen(draft_password)<8)
                snprintf(notice,sizeof(notice),"Password needs 8+ characters");
            else if (network_connect(draft_ssid,draft_password)) notice[0]=0;
            else snprintf(notice,sizeof(notice),"Could not start Wi-Fi");
        } else if (inside(x,y,110,294,140,20)) page=FORGET;
    } else if (page==NETWORKS) {
        if (inside(x,y,142,5,76,60)) { page=WIFI; return; }
        if (inside(x,y,78,296,96,34)) { network_page=0; network_scan(); return; }
        if (inside(x,y,186,296,96,34)) { begin_edit(false); return; }
        if (inside(x,y,70,257,48,32)) { if (network_page) --network_page; return; }
        if (inside(x,y,242,257,48,32)) { if ((network_page+1)*3<network_scan_count()) ++network_page; return; }
        for (unsigned row=0;row<3;++row) if (inside(x,y,66,104+row*50,228,44)) {
            const network_ap_t *ap=network_scan_result(network_page*3+row);
            if (!ap) return;
            if (!ap->supported) { snprintf(notice,sizeof(notice),"Security type not supported"); page=WIFI; return; }
            if (strcmp(draft_ssid,ap->ssid) || ap->open) memset(draft_password,0,sizeof(draft_password));
            snprintf(draft_ssid,sizeof(draft_ssid),"%s",ap->ssid);
            notice[0]=0; page=WIFI; return;
        }
    } else if (page==FORGET) {
        if (inside(x,y,142,5,76,60) || inside(x,y,80,185,200,40)) page=WIFI;
        else if (inside(x,y,80,236,200,40)) { network_forget(); open_wifi(); }
    } else {
        if (inside(x,y,140,5,80,62) || inside(x,y,140,297,80,62)) {
            if (page==MICROPHONE && y<100) { page=AUDIO; return; }
            ui_page((page==AUDIO || page==VOICE)?y<100:page==HOME); return;
        }
        if ((x<76 || x>=284) && y>=80 && y<=275 && (y<174 || y>186)) {
            int delta=y<174?10:-10;
            int vol=preference_volume(),light=preference_brightness();
            bool left=x<76;
            preference_levels(vol+(left?delta:0),light+(left?0:delta));
            highlight=(left?1:3)+(delta<0); highlight_until=now+220000;
        } else if (page==HOME && inside(x,y,76,100,208,155)) {
            if (headend_busy()) headend_stop(); else if (mic_recording()) mic_stop(); else if (audio_is_playing() || audio_clip_busy()) audio_stop(); else controls_until=0;
        }
        else if (page==AUDIO) {
            if (inside(x,y,76,126,100,42)) audio_play_chime();
            else if (inside(x,y,184,126,100,42)) audio_play_tone(440,10000);
            else if (inside(x,y,76,179,208,42)) page=MICROPHONE;
            else if (inside(x,y,92,233,176,40)) { audio_stop(); mic_stop(); }
        }
        else if (page==MICROPHONE) {
            if (inside(x,y,76,185,208,42)) { audio_stop(); if (mic_record(8000)) ui_page(false); }
            else if (inside(x,y,76,239,100,32)) { if (audio_play_clip(0)) ui_page(false); }
            else if (inside(x,y,184,239,100,32)) mic_clear();
        }
        else if (page==VOICE) {
            if (inside(x,y,76,149,208,42)) { if (headend_start(NULL)) ui_page(false); }
            else if (inside(x,y,76,205,100,38)) headend_stop();
            else if (inside(x,y,184,205,100,38)) wake_enable(!wake_enabled());
        }
        else if (page==SETTINGS) {
            if (inside(x,y,76,110,208,66)) open_wifi();
            else if (inside(x,y,76,188,100,45)) ui_page(false);
            else if (inside(x,y,184,188,100,45)) page=VOICE;
            else if (inside(x,y,92,239,176,36)) page=AUDIO;
        }
    }
}
