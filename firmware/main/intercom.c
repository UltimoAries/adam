// Live full-duplex PCM with a post-volume playback reference for ESP-SR AEC.
// The capture task only queues samples; DSP and networking run separately.
#include "intercom.h"
#include "audio.h"
#include "board.h"
#include "mic.h"
#include "wake.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "esp_aec.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "esp_wifi.h"
#include <math.h>
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

typedef struct { int16_t mic[160],reference[160]; } block_t;
static QueueHandle_t input;
static TaskHandle_t worker;
static portMUX_TYPE guard=portMUX_INITIALIZER_UNLOCKED;
static bool running,stop,capturing,connected,live;
static char uri[240],headers[110],peer_name[25];
static int64_t server_seen;
static unsigned sent,received,drops,aec_max_us,stack_min;
static uint64_t mic_energy,reference_energy,output_energy;
static unsigned energy_samples;
static uint64_t sent_energy;
static unsigned sent_peak;
static char inbound[768];
static unsigned inbound_count;
static int inbound_opcode;

bool intercom_active(void)
{ portENTER_CRITICAL(&guard);bool value=running;portEXIT_CRITICAL(&guard);return value; }
bool intercom_connected(void)
{ portENTER_CRITICAL(&guard);bool value=running && live && !stop;portEXIT_CRITICAL(&guard);return value; }
void intercom_end(void)
{ portENTER_CRITICAL(&guard);stop=true;capturing=false;live=false;portEXIT_CRITICAL(&guard); }
static bool stopping(void)
{ portENTER_CRITICAL(&guard);bool value=stop;portEXIT_CRITICAL(&guard);return value; }
void intercom_peer(char *out,unsigned size)
{ portENTER_CRITICAL(&guard);snprintf(out,size,"%s",peer_name);portEXIT_CRITICAL(&guard); }

static void websocket_event(void *arg,esp_event_base_t base,int32_t event,void *data)
{
    if (event==WEBSOCKET_EVENT_CONNECTED) {
        portENTER_CRITICAL(&guard);connected=true;server_seen=esp_timer_get_time();portEXIT_CRITICAL(&guard);
    } else if (event==WEBSOCKET_EVENT_DISCONNECTED || event==WEBSOCKET_EVENT_CLOSED || event==WEBSOCKET_EVENT_ERROR) {
        intercom_end();
    } else if (event==WEBSOCKET_EVENT_DATA) {
        esp_websocket_event_data_t *d=data;
        if (d->op_code!=1 && d->op_code!=2) return;
        if (d->payload_len<=0 || d->payload_len>sizeof(inbound)-1 || d->data_len<0 ||
            d->payload_offset<0 || d->payload_offset+d->data_len>d->payload_len) { intercom_end();return; }
        if (!d->payload_offset) { inbound_count=0;inbound_opcode=d->op_code; }
        if (inbound_count!=(unsigned)d->payload_offset || inbound_opcode!=d->op_code) { intercom_end();return; }
        memcpy(inbound+inbound_count,d->data_ptr,d->data_len);inbound_count+=d->data_len;
        if (inbound_count!=(unsigned)d->payload_len) return;
        portENTER_CRITICAL(&guard);server_seen=esp_timer_get_time();portEXIT_CRITICAL(&guard);
        if (inbound_opcode==2) {
            if (inbound_count!=640) { intercom_end();return; }
            int16_t pcm[320];memcpy(pcm,inbound,sizeof(pcm));
            if (intercom_connected()) {
                if (!audio_stream_write(pcm,320)) intercom_end();
                else { portENTER_CRITICAL(&guard);++received;portEXIT_CRITICAL(&guard); }
            }
        } else {
            inbound[inbound_count]=0;
            cJSON *root=cJSON_Parse(inbound),*state=cJSON_GetObjectItemCaseSensitive(root,"state");
            if (!cJSON_IsString(state)) intercom_end();
            else if (!strcmp(state->valuestring,"ended")) intercom_end();
            else {
                portENTER_CRITICAL(&guard);live=!strcmp(state->valuestring,"active");portEXIT_CRITICAL(&guard);
            }
            cJSON_Delete(root);
        }
    }
}

void intercom_feed(const int16_t *stereo,unsigned frames)
{
    portENTER_CRITICAL(&guard);bool feed=capturing && !stop;portEXIT_CRITICAL(&guard);
    if (!feed || !input || frames!=160) return;
    block_t block;
    for (unsigned i=0;i<160;++i) block.mic[i]=stereo[i*2];
    audio_stream_reference(block.reference);
    if (xQueueSend(input,&block,0)!=pdTRUE) {
        portENTER_CRITICAL(&guard);++drops;portEXIT_CRITICAL(&guard);
        intercom_end(); // A gap invalidates the acoustic reference alignment.
    }
}

static void task(void *unused)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
        wifi_ps_type_t previous_ps=WIFI_PS_NONE;
        bool restore_ps=esp_wifi_get_ps(&previous_ps)==ESP_OK;
        if (restore_ps) esp_wifi_set_ps(WIFI_PS_NONE);
        unsigned previous_gain=mic_gain_db();
        // Distant wake-word gain clips the full-size board's own loudspeaker.
        // Preserve that sensitivity outside calls; use linear input for AEC.
        if (!mic_set_gain(previous_gain>12?12:previous_gain)) intercom_end();
        // Announce before opening either microphone path to the relay.
        unsigned cue=audio_play_listen_cue();
        while (!stopping() && audio_clip_busy()) vTaskDelay(pdMS_TO_TICKS(10));
        if (!cue || !audio_message_delivered(cue)) intercom_end();
        aec_config_t aec_cfg={.mic_num=1,.ref_num=1,.out_num=1,.filter_length=4,.sample_rate=16000,
            .caps=MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT,.mode=AEC_MODE_FD_LOW_COST,.nlp_level=AEC_NLP_LEVEL_NORMAL};
        aec_handle_t *aec=stopping()?NULL:aec_create_from_config(&aec_cfg);
        int size=aec?aec_get_chunksize(aec):0;
        int16_t *mic=NULL,*reference=NULL,*output=NULL;
        if (size>0 && size<=1024) {
            mic=heap_caps_aligned_alloc(16,size*sizeof(int16_t),MALLOC_CAP_8BIT);
            reference=heap_caps_aligned_alloc(16,size*sizeof(int16_t),MALLOC_CAP_8BIT);
            output=heap_caps_aligned_alloc(16,size*sizeof(int16_t),MALLOC_CAP_8BIT);
        }
        if (!mic || !reference || !output || !audio_stream_start()) intercom_end();
        int64_t warm_deadline=esp_timer_get_time()+1000000;
        while (!stopping() && !audio_stream_ready() && esp_timer_get_time()<warm_deadline) vTaskDelay(pdMS_TO_TICKS(10));
        if (!audio_stream_ready()) intercom_end();
        esp_websocket_client_config_t cfg={.uri=uri,.headers=headers,.disable_auto_reconnect=true,
            .task_stack=6144,.task_prio=4,.buffer_size=1024,.network_timeout_ms=1000,
            .ping_interval_sec=2,.pingpong_timeout_sec=3,.task_core_id_set=true,.task_core_id=0};
        esp_websocket_client_handle_t ws=stopping()?NULL:esp_websocket_client_init(&cfg);
        if (ws) {
            esp_websocket_register_events(ws,WEBSOCKET_EVENT_ANY,websocket_event,NULL);
            if (esp_websocket_client_start(ws)!=ESP_OK) intercom_end();
        } else intercom_end();
        xQueueReset(input);
        portENTER_CRITICAL(&guard);capturing=!stop;server_seen=esp_timer_get_time();portEXIT_CRITICAL(&guard);
        unsigned have=0,packet_have=0,wake_have=0,overload_frames=0;
        bool overloaded=false;float send_gain=1,voice_gain=1;
        int16_t packet[320],wake_pcm[320];
        int64_t started=esp_timer_get_time();
        block_t block;
        while (!stopping()) {
            portENTER_CRITICAL(&guard);bool online=connected,active=live;int64_t seen=server_seen;portEXIT_CRITICAL(&guard);
            int64_t now=esp_timer_get_time();
            if (now-seen>3000000 || now-started>610000000 || xQueueReceive(input,&block,pdMS_TO_TICKS(100))!=pdTRUE) {
                intercom_end();break;
            }
            for (unsigned i=0;i<160 && !stopping();++i) {
                mic[have]=block.mic[i];reference[have]=block.reference[i];
                if (++have!=(unsigned)size) continue;
                int64_t before=esp_timer_get_time();aec_process(aec,mic,reference,output);
                unsigned elapsed=esp_timer_get_time()-before;
                uint64_t near_energy=0,far_energy=0,clean_energy=0;unsigned peak=0;
                for (int k=0;k<size;++k) {
                    near_energy+=(int64_t)mic[k]*mic[k];far_energy+=(int64_t)reference[k]*reference[k];
                    clean_energy+=(int64_t)output[k]*output[k];
                    unsigned absolute=output[k]<0?-(int)output[k]:output[k];
                    if (absolute>peak) peak=absolute;
                }
                portENTER_CRITICAL(&guard);
                if (elapsed>aec_max_us) aec_max_us=elapsed;
                mic_energy+=near_energy;reference_energy+=far_energy;output_energy+=clean_energy;energy_samples+=size;
                portEXIT_CRITICAL(&guard);
                // Restore speech sensitivity AFTER AEC, without clipping its ADC input.
                // The full-size board loses 24 dB when its analog gain is capped.
                // Let the echo filter settle before gradually restoring that level.
                float desired_voice_gain=CONFIG_ADAM_BOARD_146?1.0f:16.0f;
                if (esp_timer_get_time()-started<1500000) desired_voice_gain=1;
                voice_gain+=(desired_voice_gain-voice_gain)*.05f;
                float limit=peak*voice_gain>12000?12000.0f/peak:voice_gain;
                send_gain=limit<send_gain?limit:send_gain+(limit-send_gain)*.05f;
                // A sustained loud return while the other end is playing is
                // an overload, not an opportunity to keep amplifying a loop.
                overload_frames=((double)clean_energy/size*send_gain*send_gain>6000.0*6000 &&
                    far_energy/(unsigned)size>100ULL*100)?overload_frames+1:0;
                if (overload_frames>=8) { overloaded=true;intercom_end();break; }
                have=0;
                uint64_t frame_sent_energy=0;unsigned frame_sent_peak=0;
                for (int j=0;j<size && !stopping();++j) {
                    int16_t outgoing=active?(int16_t)(output[j]*send_gain):0;
                    packet[packet_have++]=outgoing;
                    frame_sent_energy+=(int64_t)outgoing*outgoing;
                    unsigned absolute=outgoing<0?-(int)outgoing:outgoing;
                    if (absolute>frame_sent_peak) frame_sent_peak=absolute;
                    wake_pcm[wake_have*2]=wake_pcm[wake_have*2+1]=output[j];
                    if (++wake_have==160) {
                        if (active) wake_feed(wake_pcm,160);
                        wake_have=0;
                    }
                    if (packet_have==320) {
                        if (online) {
                            if (esp_websocket_client_send_bin(ws,(const char *)packet,sizeof(packet),pdMS_TO_TICKS(100))!=sizeof(packet)) intercom_end();
                            else { portENTER_CRITICAL(&guard);++sent;portEXIT_CRITICAL(&guard); }
                        }
                        packet_have=0;
                    }
                }
                portENTER_CRITICAL(&guard);
                sent_energy+=frame_sent_energy;
                if (frame_sent_peak>sent_peak) sent_peak=frame_sent_peak;
                portEXIT_CRITICAL(&guard);
            }
        }
        intercom_end();
        audio_stream_stop();
        if (ws) {
            if (esp_websocket_client_is_connected(ws)) {
                const char *end=overloaded?"{\"type\":\"end\",\"reason\":\"audio_overload\"}":"{\"type\":\"end\"}";
                esp_websocket_client_send_text(ws,end,strlen(end),pdMS_TO_TICKS(100));
            }
            esp_websocket_client_stop(ws);esp_websocket_client_destroy(ws);
        }
        if (restore_ps) esp_wifi_set_ps(previous_ps);
        mic_set_gain(previous_gain);
        while (audio_is_playing()) vTaskDelay(pdMS_TO_TICKS(10));
        if (aec) aec_destroy(aec);
        free(mic);free(reference);free(output);
        xQueueReset(input);
        portENTER_CRITICAL(&guard);
        capturing=false;connected=false;live=false;running=false;headers[0]=0;stack_min=uxTaskGetStackHighWaterMark(NULL);
        portEXIT_CRITICAL(&guard);
    }
}
void intercom_init(void)
{
    input=xQueueCreate(12,sizeof(block_t));
    if (input && xTaskCreatePinnedToCore(task,"intercom",8192,NULL,4,&worker,0)!=pdPASS) {
        vQueueDelete(input);input=NULL;
    }
}
bool intercom_start(const char *url,const char *unit,const char *token,const char *call,const char *peer)
{
    if (!input || !worker || !url || strncmp(url,"http://",7) || !call || strlen(call)!=32 ||
        strspn(call,"0123456789abcdef")!=32 || audio_is_playing() || audio_clip_busy()) return false;
    portENTER_CRITICAL(&guard);
    if (running) { portEXIT_CRITICAL(&guard);return false; }
    snprintf(uri,sizeof(uri),"ws://%s/api/units/%s/calls/%s/stream",url+7,unit,call);
    snprintf(headers,sizeof(headers),"Authorization: Bearer %s\r\n",token);
    snprintf(peer_name,sizeof(peer_name),"%.24s",peer);
    running=true;stop=false;capturing=false;connected=false;live=false;inbound_count=0;
    sent=received=drops=aec_max_us=0;
    mic_energy=reference_energy=output_energy=sent_energy=0;energy_samples=sent_peak=0;
    portEXIT_CRITICAL(&guard);
    xTaskNotifyGive(worker);return true;
}
void intercom_status(void)
{
    portENTER_CRITICAL(&guard);
    bool active=running,online=connected;unsigned tx=sent,rx=received,missed=drops,elapsed=aec_max_us,stack=stack_min;
    uint64_t near=mic_energy,far=reference_energy,clean=output_energy;unsigned count=energy_samples;
    uint64_t transmitted=sent_energy;unsigned peak=sent_peak;
    portEXIT_CRITICAL(&guard);
    printf("OK intercom active=%d connected=%d sent=%u received=%u capture_drops=%u aec_max_us=%u stack_min=%u\n",
        active,online,tx,rx,missed,elapsed,stack);
    printf("OK intercom levels mic_rms=%.0f reference_rms=%.0f output_rms=%.0f samples=%u sent_rms=%.0f sent_peak=%u\n",
        count?sqrt((double)near/count):0.,count?sqrt((double)far/count):0.,count?sqrt((double)clean/count):0.,count,count?sqrt((double)transmitted/count):0.,peak);
}
