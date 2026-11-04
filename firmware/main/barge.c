// Local echo-cancelled wake detection during an explicitly interruptible reply.
// No audio leaves this worker. A wake stops playback; normal upload starts later.
#include "barge.h"
#include "audio.h"
#include "headend.h"
#include "mic.h"
#include "wake.h"
#include "board.h"
#include "esp_aec.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>

typedef struct { int16_t mic[160],ref[160]; } block_t;
static QueueHandle_t input;
static TaskHandle_t worker;
static portMUX_TYPE guard=portMUX_INITIALIZER_UNLOCKED;
static bool running,stop,interrupted,feeding;
static int64_t ready_after;
static unsigned drops,max_us,interruptions;
enum { DIAG_FRAMES=16000*15 };
static int16_t *diagnostic;
static bool diagnostic_armed;
static unsigned diagnostic_frames;
static uint64_t energies[3];
static unsigned measured,clean_peak;

bool barge_record(void)
{
    if (barge_active()) return false;
    if (!diagnostic) diagnostic=heap_caps_malloc(DIAG_FRAMES*3*sizeof(int16_t),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!diagnostic) return false;
    portENTER_CRITICAL(&guard);
    bool ok=!running;
    if (ok) { diagnostic_frames=0;diagnostic_armed=true; }
    portEXIT_CRITICAL(&guard);return ok;
}
const int16_t *barge_clip(size_t *bytes)
{
    portENTER_CRITICAL(&guard);
    *bytes=!running && !diagnostic_armed?diagnostic_frames*3*sizeof(int16_t):0;
    portEXIT_CRITICAL(&guard);return *bytes?diagnostic:NULL;
}

bool barge_active(void)
{ portENTER_CRITICAL(&guard);bool v=running;portEXIT_CRITICAL(&guard);return v; }
bool barge_ready(void)
{ portENTER_CRITICAL(&guard);bool v=running && feeding && !stop && esp_timer_get_time()>=ready_after;portEXIT_CRITICAL(&guard);return v; }
static bool stopping(void)
{ portENTER_CRITICAL(&guard);bool v=stop;portEXIT_CRITICAL(&guard);return v; }
void barge_cancel(void)
{ portENTER_CRITICAL(&guard);stop=true;interrupted=false;portEXIT_CRITICAL(&guard); }
bool barge_interrupt(void)
{
    if (!barge_ready()) return false;
    portENTER_CRITICAL(&guard);interrupted=true;stop=true;++interruptions;portEXIT_CRITICAL(&guard);
    audio_stop();return true;
}
void barge_feed(const int16_t *stereo,unsigned frames)
{
    portENTER_CRITICAL(&guard);bool active=feeding && !stop;portEXIT_CRITICAL(&guard);
    if (!active || frames!=160) return;
    block_t block;
    for (unsigned i=0;i<160;++i) block.mic[i]=stereo[i*2];
    audio_stream_reference(block.ref);
    if (xQueueSend(input,&block,0)!=pdTRUE) {
        portENTER_CRITICAL(&guard);++drops;stop=true;portEXIT_CRITICAL(&guard);
    }
}
static void task(void *unused)
{
    for (;;) {
        ulTaskNotifyTake(pdTRUE,portMAX_DELAY);
        portENTER_CRITICAL(&guard);
        bool record=diagnostic_armed;diagnostic_armed=false;
        energies[0]=energies[1]=energies[2]=0;measured=clean_peak=0;
        portEXIT_CRITICAL(&guard);
        unsigned gain=mic_gain_db();
        // Reply playback is much louder than the intercom mix. Leave ADC
        // headroom, then restore sensitivity only after cancellation.
        unsigned reply_gain=CONFIG_ADAM_BOARD_146?gain:(gain>6?6:gain);
        bool changed=mic_set_gain(reply_gain);
        float restore=powf(10.0f,(gain-reply_gain)/20.0f);
        aec_config_t cfg={.mic_num=1,.ref_num=1,.out_num=1,.filter_length=4,.sample_rate=16000,
            // Speech-recognition mode keeps the linear echo filter without
            // FD's nonlinear suppression, which can erase a quiet wake word.
            .caps=MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT,.mode=AEC_MODE_SR_LOW_COST,.nlp_level=AEC_NLP_LEVEL_NORMAL};
        aec_handle_t *aec=changed?aec_create_from_config(&cfg):NULL;
        int size=aec?aec_get_chunksize(aec):0;
        int16_t *mic=NULL,*ref=NULL,*out=NULL;
        if (size>0 && size<=1024) {
            mic=heap_caps_aligned_alloc(16,size*2,MALLOC_CAP_8BIT);
            ref=heap_caps_aligned_alloc(16,size*2,MALLOC_CAP_8BIT);
            out=heap_caps_aligned_alloc(16,size*2,MALLOC_CAP_8BIT);
        }
        xQueueReset(input);
        portENTER_CRITICAL(&guard);
        if (!mic || !ref || !out) stop=true;
        feeding=!stop;ready_after=esp_timer_get_time()+800000;
        portEXIT_CRITICAL(&guard);
        block_t block;unsigned have=0,assembled=0,recorded=0;
        int16_t mono[160],stereo[320];
        while (!stopping()) {
            if (xQueueReceive(input,&block,pdMS_TO_TICKS(100))!=pdTRUE) continue;
            for (unsigned i=0;i<160 && !stopping();++i) {
                mic[have]=block.mic[i];ref[have]=block.ref[i];
                if (++have!=(unsigned)size) continue;
                uint64_t energy[3]={0};unsigned peak=0;
                // Measure and preserve inputs before the DSP call: its API
                // takes mutable buffers, so diagnostics must not assume that
                // their contents survive processing unchanged.
                unsigned start=recorded;
                for (int j=0;j<size;++j) {
                    energy[0]+=(int64_t)mic[j]*mic[j];energy[1]+=(int64_t)ref[j]*ref[j];
                    if (record && recorded<DIAG_FRAMES) {
                        diagnostic[recorded*3]=mic[j];diagnostic[recorded*3+1]=ref[j];++recorded;
                    }
                }
                int64_t before=esp_timer_get_time();aec_process(aec,mic,ref,out);
                unsigned elapsed=esp_timer_get_time()-before;
                for (int j=0;j<size;++j) {
                    energy[2]+=(int64_t)out[j]*out[j];
                    unsigned absolute=out[j]<0?-(int)out[j]:out[j];if (absolute>peak) peak=absolute;
                    if (record && start+(unsigned)j<recorded) diagnostic[(start+j)*3+2]=out[j];
                }
                portENTER_CRITICAL(&guard);
                if (elapsed>max_us) max_us=elapsed;
                for (unsigned c=0;c<3;++c) energies[c]+=energy[c];
                measured+=size;if (peak>clean_peak) clean_peak=peak;
                if (record) diagnostic_frames=recorded;
                portEXIT_CRITICAL(&guard);
                have=0;
                for (int j=0;j<size;++j) {
                    // Restore wake sensitivity after AEC while keeping ADC input
                    // linear. Saturation here cannot feed back into AEC.
                    int value=(int)(out[j]*restore);
                    mono[assembled]=(int16_t)(value>32767?32767:value< -32768?-32768:value);
                    stereo[assembled*2]=stereo[assembled*2+1]=mono[assembled];
                    if (++assembled==160) {
                        if (barge_ready()) { headend_barge_feed(mono);wake_feed(stereo,160); }
                        assembled=0;
                    }
                }
            }
        }
        portENTER_CRITICAL(&guard);feeding=false;portEXIT_CRITICAL(&guard);
        if (aec) aec_destroy(aec);
        free(mic);free(ref);free(out);
        if (changed) mic_set_gain(gain);
        xQueueReset(input);
        portENTER_CRITICAL(&guard);running=false;portEXIT_CRITICAL(&guard);
    }
}
void barge_init(void)
{
    input=xQueueCreate(12,sizeof(block_t));
    if (input && xTaskCreatePinnedToCore(task,"reply-aec",8192,NULL,4,&worker,0)!=pdPASS) {
        vQueueDelete(input);input=NULL;
    }
}
bool barge_begin(void)
{
    if (!input || !worker || !wake_enabled()) return false;
    portENTER_CRITICAL(&guard);
    if (running) { portEXIT_CRITICAL(&guard);return false; }
    running=true;stop=interrupted=feeding=false;
    portEXIT_CRITICAL(&guard);xTaskNotifyGive(worker);return true;
}
bool barge_end(void)
{
    portENTER_CRITICAL(&guard);stop=true;portEXIT_CRITICAL(&guard);
    // AEC processes bounded blocks. Do not restore capture until its worker
    // has restored the original microphone gain and released its reference.
    for (unsigned i=0;i<200 && barge_active();++i) vTaskDelay(pdMS_TO_TICKS(5));
    portENTER_CRITICAL(&guard);bool value=interrupted && !running;portEXIT_CRITICAL(&guard);return value;
}
void barge_status(void)
{
    portENTER_CRITICAL(&guard);unsigned lost=drops,us=max_us,count=interruptions;bool active=running;portEXIT_CRITICAL(&guard);
    printf("OK reply_interrupt active=%d count=%u drops=%u aec_max_us=%u mode=sr_linear\n",active,count,lost,us);
    portENTER_CRITICAL(&guard);
    uint64_t energy[3]={energies[0],energies[1],energies[2]};unsigned samples=measured,peak=clean_peak,frames=diagnostic_frames;bool armed=diagnostic_armed;
    portEXIT_CRITICAL(&guard);
    printf("OK reply_levels mic_rms=%u reference_rms=%u clean_rms=%u clean_peak=%u samples=%u diagnostic_armed=%d diagnostic_frames=%u\n",
        samples?(unsigned)sqrt(energy[0]/(double)samples):0,samples?(unsigned)sqrt(energy[1]/(double)samples):0,
        samples?(unsigned)sqrt(energy[2]/(double)samples):0,peak,samples,armed,frames);
}
