// LAN protocol v1: authenticated control polling, ordered 20-ms PCM upload,
// and acknowledged mono PCM paging. No network work runs in the mic task.
#include "headend.h"
#include "audio.h"
#include "mic.h"
#include "preferences.h"
#include "wake.h"
#include "preroll.h"
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

typedef struct { char url[101],unit[17],token[65]; } config_t;
typedef enum { UNPAIRED,OFFLINE,READY,STARTING,LISTENING,PROCESSING,PAGING,ERROR } state_t;
static config_t configuration;
static portMUX_TYPE guard=portMUX_INITIALIZER_UNLOCKED;
static voice_block_t *storage;
static voice_buffer_t buffer;
static TaskHandle_t worker;
static bool configured,requested,cancelled,paging,uploading;
static unsigned sent,failures,completed,pages_done,stack_min,aborted,input_gaps;
static int64_t capture_started;
static int64_t last_contact;
static state_t state;
static char wake_model[97];

static void set_state(state_t value)
{ portENTER_CRITICAL(&guard); state=value; portEXIT_CRITICAL(&guard); }
static bool wifi_ready(void) { return !strcmp(network_state(),"CONNECTED"); }
static bool safe_id(const char *value,unsigned length,bool exact)
{
    size_t size=strlen(value);
    if (!size || size>length || (exact && size!=length)) return false;
    for (const char *p=value;*p;++p)
        if (!isalnum((unsigned char)*p) && *p!='_' && *p!='-' && *p!='.') return false;
    return true;
}
static bool safe_url(const char *url)
{
    unsigned a,b,c,d; int used=0;
    if (sscanf(url,"http://%u.%u.%u.%u:8765%n",&a,&b,&c,&d,&used)!=4 || used!=(int)strlen(url)) return false;
    if (a>255 || b>255 || c>255 || d>255) return false;
    return a==10 || (a==172 && b>=16 && b<=31) || (a==192 && b==168);
}
static esp_http_client_handle_t client(const config_t *cfg,const char *path,esp_http_client_method_t method)
{
    char url[300],auth[80];
    snprintf(url,sizeof(url),"%s/api/units/%s/%s",cfg->url,cfg->unit,path);
    const esp_http_client_config_t options={.url=url,.timeout_ms=3000,.buffer_size=1024,.buffer_size_tx=1024};
    esp_http_client_handle_t handle=esp_http_client_init(&options);
    if (!handle) return NULL;
    snprintf(auth,sizeof(auth),"Bearer %s",cfg->token);
    esp_http_client_set_header(handle,"Authorization",auth);
    esp_http_client_set_method(handle,method);
    return handle;
}
static bool write_all(esp_http_client_handle_t handle,const void *data,unsigned length)
{
    unsigned sent_bytes=0;
    while (sent_bytes<length) {
        int result=esp_http_client_write(handle,(const char *)data+sent_bytes,length-sent_bytes);
        if (result<=0) return false;
        sent_bytes+=result;
    }
    return true;
}
static bool read_json(esp_http_client_handle_t handle,char *response,unsigned size)
{
    if (esp_http_client_fetch_headers(handle)<0) return false;
    int status=esp_http_client_get_status_code(handle);
    if (status<200 || status>=300) return false;
    int count=esp_http_client_read_response(handle,response,size-1);
    if (count<0 || count>=(int)size-1) return false;
    response[count]=0;
    portENTER_CRITICAL(&guard); last_contact=esp_timer_get_time(); portEXIT_CRITICAL(&guard);
    return true;
}
static bool request(const config_t *cfg,const char *path,const char *body,char *response,unsigned size)
{
    esp_http_client_handle_t handle=client(cfg,path,body?HTTP_METHOD_POST:HTTP_METHOD_GET);
    if (!handle) return false;
    if (body) esp_http_client_set_header(handle,"Content-Type","application/json");
    unsigned length=body?strlen(body):0;
    bool ok=esp_http_client_open(handle,length)==ESP_OK;
    if (ok && length) ok=write_all(handle,body,length);
    if (ok) ok=read_json(handle,response,size);
    esp_http_client_cleanup(handle);
    return ok;
}
bool headend_listening(void)
{ portENTER_CRITICAL(&guard); bool value=buffer.capturing; portEXIT_CRITICAL(&guard); return value; }
bool headend_busy(void)
{ portENTER_CRITICAL(&guard); bool value=requested || uploading || buffer.session || paging; portEXIT_CRITICAL(&guard); return value; }
const char *headend_state(void)
{
    static const char *names[]={"UNPAIRED","OFFLINE","READY","STARTING","LISTENING","PROCESSING","PAGING","ERROR"};
    portENTER_CRITICAL(&guard); state_t value=state; portEXIT_CRITICAL(&guard);
    return names[value];
}
bool headend_start(const char *model)
{
    if (!storage || !worker || !wifi_ready() || mic_recording() || audio_is_playing() || audio_clip_busy()) return false;
    if (model && !safe_id(model,96,false)) return false;
    portENTER_CRITICAL(&guard);
    bool ok=configured && !requested && !uploading && !buffer.session && !paging && last_contact>0 && esp_timer_get_time()-last_contact<10000000;
    if (ok) ok=model?voice_buffer_begin_auto(&buffer):voice_buffer_begin(&buffer);
    if (ok) {
        snprintf(wake_model,sizeof(wake_model),"%s",model?model:"");
        requested=true; cancelled=false; sent=0;capture_started=esp_timer_get_time();state=LISTENING;
    }
    portEXIT_CRITICAL(&guard);
    if (ok) xTaskNotifyGive(worker);
    return ok;
}
void headend_stop(void)
{
    portENTER_CRITICAL(&guard);
    bool stop_audio=paging || uploading || requested;
    cancelled=true;requested=false;voice_buffer_end(&buffer);
    if (!uploading && !paging) state=configured?READY:UNPAIRED;
    portEXIT_CRITICAL(&guard);
    if (stop_audio) audio_stop();
}
void headend_feed(const int16_t *stereo,unsigned frames)
{
    if (!storage || frames!=VOICE_BLOCK_SAMPLES) return;
    voice_block_t block;
    for (unsigned i=0;i<VOICE_BLOCK_SAMPLES;++i) block.pcm[i]=stereo[i*2];
    bool cache=!audio_is_playing() && !audio_clip_busy() && !mic_recording();
    portENTER_CRITICAL(&guard);
    voice_buffer_feed(&buffer,block.pcm,cache);
    portEXIT_CRITICAL(&guard);
}
void headend_audio_gap(void)
{
    if (!storage) return;
    portENTER_CRITICAL(&guard);
    ++input_gaps;voice_buffer_gap(&buffer);
    portEXIT_CRITICAL(&guard);
}
static bool upload(const config_t *cfg,const char *model)
{
    // Capture already began at the trigger, with its history bank frozen.
    // Keep every microphone block during the cue and connection setup. The
    // fixed live spool cannot overrun when networking is temporarily delayed.
    portENTER_CRITICAL(&guard);
    bool stopped=cancelled,automatic=buffer.automatic;unsigned prefix=buffer.prefix,limit=buffer.limit;int64_t start=capture_started;
    portEXIT_CRITICAL(&guard);
    if (stopped) return false;
    unsigned cue=audio_play_listen_cue();
    if (!cue) return false;
    while (audio_clip_busy()) {
        if (esp_timer_get_time()-start>12000000) { audio_stop();return false; }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
    if (!audio_message_delivered(cue)) return false;
    vTaskDelay(pdMS_TO_TICKS(120));
    portENTER_CRITICAL(&guard); stopped=cancelled || buffer.failed; portEXIT_CRITICAL(&guard);
    if (stopped) return false;
    esp_http_client_handle_t handle=client(cfg,"audio",HTTP_METHOD_POST);
    if (!handle) return false;
    char session[33];
    snprintf(session,sizeof(session),"%08lx%08lx%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random(),
             (unsigned long)esp_random(),(unsigned long)esp_random());
    esp_http_client_set_header(handle,"Content-Type","application/octet-stream");
    esp_http_client_set_header(handle,"X-Session-ID",session);
    unsigned packets=(prefix+limit)/2;
    char frame_header[16],prefix_header[16];
    snprintf(frame_header,sizeof(frame_header),"%u",packets);
    snprintf(prefix_header,sizeof(prefix_header),"%u",prefix*10);
    esp_http_client_set_header(handle,"X-Audio-Frames",frame_header);
    esp_http_client_set_header(handle,"X-Audio-Preroll-Ms",prefix_header);
    if (automatic) esp_http_client_set_header(handle,"X-Audio-Mode","silence-v1");
    esp_http_client_set_header(handle,"X-Trigger",model[0]?"wake":"touch");
    if (model[0]) esp_http_client_set_header(handle,"X-Wake-Model",model);
    bool ok=esp_http_client_open(handle,automatic?-1:(int)(packets*644))==ESP_OK;
    if (ok && (mic_recording() || audio_is_playing())) ok=false;
    unsigned sequence=0;
    for (;sequence<packets && ok;++sequence) {
        uint8_t packet[644];bool available=false;
        bool finished=false;
        while (ok && !available) {
            portENTER_CRITICAL(&guard);
            bool stop=cancelled || buffer.failed,active=buffer.capturing;
            available=voice_buffer_packet(&buffer,sequence,packet+4);
            portEXIT_CRITICAL(&guard);
            if (!stop && !active && !available && automatic) { finished=true;break; }
            if (stop || (!available && !active) || audio_is_playing() || esp_timer_get_time()-start>18000000) ok=false;
            else if (!available) vTaskDelay(pdMS_TO_TICKS(10));
        }
        if (!ok || finished) break;
        packet[0]=sequence; packet[1]=sequence>>8; packet[2]=sequence>>16; packet[3]=sequence>>24;
        if (automatic) ok=write_all(handle,"284\r\n",5);
        if (ok) ok=write_all(handle,packet,sizeof(packet));
        if (ok && automatic) ok=write_all(handle,"\r\n",2);
        if (ok) { portENTER_CRITICAL(&guard); sent+=640; portEXIT_CRITICAL(&guard); }
    }
    portENTER_CRITICAL(&guard); bool invalid=cancelled || buffer.failed; portEXIT_CRITICAL(&guard);
    ok=ok && !invalid;
    if (ok && automatic) {
        // Explicit end record distinguishes a completed utterance from a
        // truncated connection. Follow with the HTTP chunk terminator.
        uint8_t end[644]={255,255,255,255};
        end[4]=sequence;end[5]=sequence>>8;end[6]=sequence>>16;end[7]=sequence>>24;
        portENTER_CRITICAL(&guard);end[8]=buffer.reason;portEXIT_CRITICAL(&guard);
        ok=write_all(handle,"284\r\n",5) && write_all(handle,end,sizeof(end)) && write_all(handle,"\r\n0\r\n\r\n",7);
    }
    char response[512];
    if (ok) ok=read_json(handle,response,sizeof(response));
    esp_http_client_cleanup(handle);
    return ok;
}
static bool ack(const config_t *cfg,const char *id,const char *status)
{
    char path[100],body[64],response[256];
    snprintf(path,sizeof(path),"pages/%s/ack",id);
    snprintf(body,sizeof(body),"{\"status\":\"%s\"}",status);
    return request(cfg,path,body,response,sizeof(response));
}
static bool play_page(const config_t *cfg,const char *id,unsigned length)
{
    if (!safe_id(id,32,true) || !length || length>480000 || length%2) return false;
    if (mic_recording() || audio_is_playing() || audio_clip_busy()) return false;
    char path[100]; snprintf(path,sizeof(path),"pages/%s/audio",id);
    int16_t *pcm=heap_caps_malloc(length,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!pcm) return false;
    esp_http_client_handle_t handle=client(cfg,path,HTTP_METHOD_GET);
    bool ok=handle && esp_http_client_open(handle,0)==ESP_OK;
    if (ok) ok=esp_http_client_fetch_headers(handle)==length && esp_http_client_get_status_code(handle)==200;
    unsigned received=0; int64_t start=esp_timer_get_time();
    while (ok && received<length) {
        int count=esp_http_client_read(handle,(char *)pcm+received,length-received>4096?4096:length-received);
        if (count<=0 || esp_timer_get_time()-start>12000000) ok=false;
        else received+=count;
    }
    if (handle) esp_http_client_cleanup(handle);
    portENTER_CRITICAL(&guard); bool stopped=cancelled; portEXIT_CRITICAL(&guard);
    ok=ok && !stopped;
    unsigned playback=0;
    if (ok) ok=ack(cfg,id,"playing");
    portENTER_CRITICAL(&guard); stopped=cancelled; portEXIT_CRITICAL(&guard);
    if (ok && !stopped) { playback=audio_play_message(pcm,length/2); ok=playback!=0; }
    else ok=false;
    if (ok) {
        while (audio_clip_busy()) vTaskDelay(pdMS_TO_TICKS(25));
        ok=audio_message_delivered(playback);
    }
    free(pcm);
    ack(cfg,id,ok?"delivered":"failed");
    return ok;
}
static void task(void *unused)
{
    for (;;) {
        config_t cfg; bool paired,start; char model[97];
        portENTER_CRITICAL(&guard);
        cfg=configuration; paired=configured; start=requested; requested=false; uploading=start;
        snprintf(model,sizeof(model),"%s",wake_model);
        portEXIT_CRITICAL(&guard);
        if (start) {
            bool ok=paired && wifi_ready() && upload(&cfg,model);
            portENTER_CRITICAL(&guard);
            bool stopped=cancelled && !buffer.failed;
            uploading=false;voice_buffer_end(&buffer);
            if (ok) ++completed; else if (stopped) ++aborted; else ++failures;
            portEXIT_CRITICAL(&guard);
            set_state(ok?PROCESSING:stopped?READY:ERROR);
        } else if (!paired || !wifi_ready()) set_state(paired?OFFLINE:UNPAIRED);
        else {
            char response[1536];
            char path[180];
            snprintf(path,sizeof(path),"poll?firmware=v13&wake_model=%s&busy=%d",wake_model_id(),
                     headend_busy() || mic_recording() || audio_is_playing() || audio_clip_busy());
            bool ok=request(&cfg,path,NULL,response,sizeof(response));
            if (!ok) set_state(OFFLINE);
            else {
                cJSON *root=cJSON_Parse(response);
                const cJSON *type=cJSON_GetObjectItemCaseSensitive(root,"type");
                if (cJSON_IsString(type) && !strcmp(type->valuestring,"listen")) headend_start(NULL);
                else if (cJSON_IsString(type) && !strcmp(type->valuestring,"page")) {
                    const cJSON *id=cJSON_GetObjectItemCaseSensitive(root,"id"),*bytes=cJSON_GetObjectItemCaseSensitive(root,"bytes");
                    if (cJSON_IsString(id) && cJSON_IsNumber(bytes) && bytes->valuedouble>0 && bytes->valuedouble<=480000) {
                        portENTER_CRITICAL(&guard);
                        bool reserve=!requested && !uploading && !buffer.session;
                        if (reserve) { paging=true;cancelled=false;state=PAGING; }
                        portEXIT_CRITICAL(&guard);
                        // A wake can arrive while the poll is in flight. Its
                        // capture wins; the server's offer lease will retry.
                        if (reserve) {
                            bool played=play_page(&cfg,id->valuestring,(unsigned)bytes->valuedouble);
                            portENTER_CRITICAL(&guard); paging=false; if (played) ++pages_done; else ++failures; portEXIT_CRITICAL(&guard);
                            set_state(played?READY:ERROR);
                        }
                    }
                } else if (!headend_busy()) set_state(READY);
                cJSON_Delete(root);
            }
        }
        portENTER_CRITICAL(&guard); stack_min=uxTaskGetStackHighWaterMark(NULL); portEXIT_CRITICAL(&guard);
        // Faster delivery pickup following a question; normal idle polling
        // stays at 1.5 s to avoid multiplying background fleet traffic.
        ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(capture_started && esp_timer_get_time()-capture_started<30000000?250:1500));
    }
}
void headend_init(void)
{
    nvs_handle_t handle;
    if (nvs_open("headend",NVS_READONLY,&handle)==ESP_OK) {
        size_t url=sizeof(configuration.url),unit=sizeof(configuration.unit),token=sizeof(configuration.token);
        configured=nvs_get_str(handle,"url",configuration.url,&url)==ESP_OK &&
            nvs_get_str(handle,"unit",configuration.unit,&unit)==ESP_OK && nvs_get_str(handle,"token",configuration.token,&token)==ESP_OK;
        nvs_close(handle);
        configured=configured && safe_url(configuration.url) && safe_id(configuration.unit,16,true) && safe_id(configuration.token,64,false);
    }
    state=configured?OFFLINE:UNPAIRED;
    storage=heap_caps_malloc(VOICE_STORAGE_BLOCKS*sizeof(*storage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!storage) { state=ERROR;return; }
    voice_buffer_init(&buffer,storage);
    if (xTaskCreatePinnedToCore(task,"headend",8192,NULL,4,&worker,1)!=pdPASS) {
        free(storage);storage=NULL;state=ERROR;
    }
}
bool headend_configure(const char *url,const char *unit_id,const char *token)
{
    if (!safe_url(url) || !safe_id(unit_id,16,true) || !safe_id(token,64,false) || headend_busy()) return false;
    nvs_handle_t handle;
    if (nvs_open("headend",NVS_READWRITE,&handle)!=ESP_OK) return false;
    esp_err_t err=nvs_set_str(handle,"url",url);
    if (err==ESP_OK) err=nvs_set_str(handle,"unit",unit_id);
    if (err==ESP_OK) err=nvs_set_str(handle,"token",token);
    if (err==ESP_OK) err=nvs_commit(handle);
    nvs_close(handle);
    if (err!=ESP_OK) return false;
    portENTER_CRITICAL(&guard);
    snprintf(configuration.url,sizeof(configuration.url),"%s",url);
    snprintf(configuration.unit,sizeof(configuration.unit),"%s",unit_id);
    snprintf(configuration.token,sizeof(configuration.token),"%s",token);
    configured=true; state=OFFLINE;
    portEXIT_CRITICAL(&guard);
    if (worker) xTaskNotifyGive(worker);
    return true;
}
void headend_status(void)
{
    portENTER_CRITICAL(&guard);
    unsigned bytes=sent,errors=failures,done=completed,pages=pages_done,stack=stack_min,cancel_count=aborted;
    bool paired=configured,active=buffer.capturing,bad=buffer.failed;
    unsigned pre=buffer.prefix*10,live=buffer.recorded*10,history=buffer.have*10,gaps=input_gaps;
    unsigned reason=buffer.reason,threshold=buffer.threshold;bool automatic=buffer.automatic;
    portEXIT_CRITICAL(&guard);
    printf("OK headend configured=%d state=%s listening=%d sent_bytes=%u errors=%u completed=%u cancelled=%u pages_delivered=%u queue_failed=%d stack_min=%u wake_model=%s preroll_ms=%u captured_ms=%u history_ms=%u input_gaps=%u buffer_bytes=%u\n",
           paired,headend_state(),active,bytes,errors,done,cancel_count,pages,bad,stack,wake_model_id(),
           pre,live,history,gaps,storage?(unsigned)(VOICE_STORAGE_BLOCKS*sizeof(*storage)):0);
    printf("OK endpoint automatic=%d reason=%u threshold_energy=%u silence_ms=1200 max_live_ms=13000\n",automatic,reason,threshold);
}
bool headend_selftest(void)
{
    return !headend_busy() && !mic_recording() && !audio_is_playing() &&
        !audio_clip_busy() && voice_buffer_selftest();
}
