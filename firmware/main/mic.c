// ES7210 setup adapted from the pinned Espressif Apache-2.0 driver:
// vendor/Arduino/examples/08_esp_sr/es7210.cpp (2022 Espressif Systems).
// Active analog/clock/pair-power sequence checked against the Apache-2.0
// native Espressif driver preserved in vendor/espressif/es7210-native.c.
#include "mic.h"
#include "audio.h"
#include "headend.h"
#include "wake.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

enum { RATE=16000,FRAMES=160,MAX_MS=15000,CHANNELS=2 };
static i2c_master_dev_handle_t adc;
static i2s_chan_handle_t input;
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static int16_t *clip;
static int16_t samples[FRAMES*CHANNELS];
static int32_t wire_samples[FRAMES*CHANNELS];
static bool ready,recording;
static unsigned frames,target,read_errors,overflows,blocks,stack_min;
static unsigned session_errors,session_overflows,peak[2],clipped[2],live[2];
static uint64_t energy[2];
static esp_err_t last_error;
static unsigned gain_db=36,clip_gain_db=36;
static unsigned diagnostic_mask[2],diagnostic_zeros[2];
static uint32_t diagnostic_words[8];

static esp_err_t write_reg(uint8_t reg,uint8_t value)
{ uint8_t data[]={reg,value}; return i2c_master_transmit(adc,data,2,100); }
static esp_err_t read_reg(uint8_t reg,uint8_t *value)
{ return i2c_master_transmit_receive(adc,&reg,1,value,1,100); }
static esp_err_t setup_adc(void)
{
    // Native vendor board format: 16 kHz, 256*Fs MCLK, stereo 32-bit slots.
    // SDOUT1 carries ADC1/ADC2. The separate ADC3 AEC reference is not selected.
    // Use 36 dB analog gain (vendor voice setting), with unity digital gain.
    const uint8_t setup[][2]={
        {0x00,0xFF},{0x00,0x41},{0x01,0x3F},{0x09,0x30},{0x0A,0x30},
        {0x23,0x2A},{0x22,0x0A},{0x21,0x2A},{0x20,0x0A},
        // Clear only MS_MODE (bit 0); preserve LRCK_RATE_MODE=1 (two channels).
        // Zeroing this whole register caused 14 zero frames in every 16 on V2.
        {0x08,0x10},{0x11,0x80},{0x12,0x00},{0x40,0x43},
        {0x41,0x70},{0x42,0x70},{0x43,0x1D},{0x44,0x1D},
        {0x45,0x08},{0x46,0x08},{0x47,0x08},{0x48,0x08},
        {0x49,0x08},{0x4A,0x08},{0x07,0x20},{0x02,0xC1},
        {0x04,0x01},{0x05,0x00},{0x06,0x00},{0x4B,0x00},
        {0x4C,0xFF},{0x01,0x34},{0x1B,0xBF},{0x1C,0xBF},
        {0x1D,0xBF},{0x1E,0xBF},{0x40,0x43},{0x00,0x71},{0x00,0x41},
    };
    for (unsigned i=0;i<sizeof(setup)/sizeof(setup[0]);++i) {
        esp_err_t err=write_reg(setup[i][0],setup[i][1]);
        if (err!=ESP_OK) return err;
    }
    uint8_t format,clock,analog,pair_power,mode;
    esp_err_t err=read_reg(0x11,&format);
    if (err!=ESP_OK || (err=read_reg(0x02,&clock))!=ESP_OK) return err;
    if ((err=read_reg(0x40,&analog))!=ESP_OK || (err=read_reg(0x4B,&pair_power))!=ESP_OK) return err;
    if ((err=read_reg(0x08,&mode))!=ESP_OK) return err;
    return format==0x80 && clock==0xC1 && analog==0x43 && pair_power==0 && mode==0x10?
        ESP_OK:ESP_ERR_INVALID_RESPONSE;
}
static bool overflow(i2s_chan_handle_t channel,i2s_event_data_t *event,void *context)
{
    portENTER_CRITICAL_ISR(&lock);
    if (ready) ++overflows;
    if (recording) ++session_overflows;
    portEXIT_CRITICAL_ISR(&lock);
    return false;
}
static void capture_task(void *unused)
{
    unsigned seen_overflows=0;
    for (;;) {
        size_t bytes=0;
        esp_err_t err=i2s_channel_read(input,wire_samples,sizeof(wire_samples),&bytes,100);
        unsigned count=bytes/(sizeof(int32_t)*CHANNELS);
        portENTER_CRITICAL(&lock);unsigned lost=overflows;portEXIT_CRITICAL(&lock);
        if (lost!=seen_overflows) { seen_overflows=lost;headend_audio_gap(); }
        if (err!=ESP_OK || bytes!=sizeof(wire_samples)) {
            portENTER_CRITICAL(&lock);
            ++read_errors; last_error=err==ESP_OK?ESP_ERR_INVALID_SIZE:err;
            // A damaged recording must not be presented as a successful clip.
            if (recording) { ++session_errors; recording=false; frames=0; }
            portEXIT_CRITICAL(&lock);
            headend_audio_gap();
            continue;
        }
        for (unsigned i=0;i<count*CHANNELS;++i) samples[i]=(int16_t)(wire_samples[i]>>16);
        headend_feed(samples,count);
        wake_feed(samples,count);
        uint64_t sum[2]={0}; unsigned maximum[2]={0},saturated[2]={0};
        unsigned phase_mask[2]={0},zeros[2]={0};
        for (unsigned i=0;i<count;++i) for (unsigned ch=0;ch<CHANNELS;++ch) {
            int sample=samples[i*2+ch]; unsigned absolute=sample<0?-sample:sample;
            sum[ch]+=(int64_t)sample*sample;
            if (absolute>maximum[ch]) maximum[ch]=absolute;
            if (absolute>=32760) ++saturated[ch];
            if (sample) phase_mask[ch]|=1U<<(i%16); else ++zeros[ch];
        }
        unsigned level[2]={(unsigned)sqrtf(sum[0]/(float)count),(unsigned)sqrtf(sum[1]/(float)count)};
        unsigned stack=uxTaskGetStackHighWaterMark(NULL);
        portENTER_CRITICAL(&lock);
        ++blocks; stack_min=stack;
        memcpy(diagnostic_mask,phase_mask,sizeof(phase_mask));
        memcpy(diagnostic_zeros,zeros,sizeof(zeros));
        memcpy(diagnostic_words,wire_samples,sizeof(diagnostic_words));
        for (unsigned ch=0;ch<CHANNELS;++ch) live[ch]=(live[ch]*3+level[ch])/4;
        if (recording) {
            if (session_overflows) { recording=false; frames=0; }
            else {
                // Durations are whole 10-ms blocks; this copy is at most 640 bytes.
                memcpy(clip+frames*2,samples,count*CHANNELS*sizeof(int16_t));
                frames+=count;
                for (unsigned ch=0;ch<CHANNELS;++ch) {
                    energy[ch]+=sum[ch]; clipped[ch]+=saturated[ch];
                    if (maximum[ch]>peak[ch]) peak[ch]=maximum[ch];
                }
                if (frames>=target) recording=false;
            }
        }
        portEXIT_CRITICAL(&lock);
    }
}
bool mic_init(i2c_master_bus_handle_t bus,i2s_chan_handle_t rx)
{
    input=rx;
    const i2c_device_config_t device={.dev_addr_length=I2C_ADDR_BIT_LEN_7,
        .device_address=0x40,.scl_speed_hz=400000};
    esp_err_t err=i2c_master_bus_add_device(bus,&device,&adc);
    if (err!=ESP_OK) goto fail;
    if ((err=setup_adc())!=ESP_OK) goto fail;
    clip=heap_caps_malloc(RATE*MAX_MS/1000*CHANNELS*sizeof(int16_t),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
    if (!clip) { err=ESP_ERR_NO_MEM; goto fail; }
    const i2s_event_callbacks_t callbacks={.on_recv_q_ovf=overflow};
    if ((err=i2s_channel_register_event_callback(rx,&callbacks,NULL))!=ESP_OK) goto fail;
    if ((err=i2s_channel_enable(rx))!=ESP_OK) goto fail;
    if (xTaskCreatePinnedToCore(capture_task,"mic",4096,NULL,6,NULL,1)!=pdPASS) {
        i2s_channel_disable(rx); err=ESP_ERR_NO_MEM; goto fail;
    }
    portENTER_CRITICAL(&lock); ready=true; portEXIT_CRITICAL(&lock);
    return true;
fail:
    last_error=err;
    if (adc) { i2c_master_bus_rm_device(adc); adc=NULL; }
    if (clip) { free(clip); clip=NULL; }
    return false;
}
bool mic_ready(void) { return ready; }
bool mic_record(unsigned ms)
{
    if (!ready || audio_clip_busy() || headend_busy() || ms<100 || ms>MAX_MS || ms%10) return false;
    portENTER_CRITICAL(&lock);
    if (recording) { portEXIT_CRITICAL(&lock); return false; }
    frames=0; target=RATE*ms/1000; session_errors=session_overflows=0;
    clip_gain_db=gain_db;
    memset(energy,0,sizeof(energy)); memset(peak,0,sizeof(peak)); memset(clipped,0,sizeof(clipped));
    recording=true;
    portEXIT_CRITICAL(&lock);
    return true;
}
bool mic_recording(void)
{ portENTER_CRITICAL(&lock); bool active=recording; portEXIT_CRITICAL(&lock); return active; }
bool mic_set_gain(unsigned db)
{
    if (!ready || mic_recording() || db>36 || db%3) return false;
    uint8_t value=0x10|(db==36?13:db/3);
    esp_err_t first=write_reg(0x43,value),second=write_reg(0x44,value);
    if (first!=ESP_OK || second!=ESP_OK) {
        portENTER_CRITICAL(&lock); last_error=first!=ESP_OK?first:second; portEXIT_CRITICAL(&lock);
        return false;
    }
    gain_db=db;
    return true;
}
unsigned mic_gain_db(void) { return gain_db; }
void mic_stop(void)
{ portENTER_CRITICAL(&lock); recording=false; portEXIT_CRITICAL(&lock); }
bool mic_clear(void)
{
    portENTER_CRITICAL(&lock);
    bool ok=!recording && !audio_clip_busy();
    if (ok) frames=target=0;
    portEXIT_CRITICAL(&lock);
    return ok;
}
unsigned mic_remaining_ms(void)
{ portENTER_CRITICAL(&lock); unsigned ms=recording?(target-frames)*1000/RATE:0; portEXIT_CRITICAL(&lock); return ms; }
unsigned mic_duration_ms(void)
{ portENTER_CRITICAL(&lock); unsigned ms=frames*1000/RATE; portEXIT_CRITICAL(&lock); return ms; }
unsigned mic_level(unsigned channel)
{ portENTER_CRITICAL(&lock); unsigned value=channel<2?live[channel]:0; portEXIT_CRITICAL(&lock); return value; }
const int16_t *mic_clip(size_t *bytes)
{
    portENTER_CRITICAL(&lock);
    *bytes=!recording?frames*CHANNELS*sizeof(int16_t):0;
    portEXIT_CRITICAL(&lock);
    // Clip mutations and USB download are both initiated by the main/UI task.
    return *bytes?clip:NULL;
}
void mic_status(void)
{
    portENTER_CRITICAL(&lock);
    unsigned count=frames,wanted=target,errors=read_errors,missed=overflows,stack=stack_min;
    unsigned p[2]={peak[0],peak[1]},c[2]={clipped[0],clipped[1]},l[2]={live[0],live[1]};
    uint64_t e[2]={energy[0],energy[1]};
    unsigned se=session_errors,so=session_overflows,total_blocks=blocks;
    bool active=recording; esp_err_t err=last_error;
    unsigned gain=gain_db,record_gain=clip_gain_db;
    portEXIT_CRITICAL(&lock);
    printf("OK mic ready=%d recording=%d rate=%d channels=2 bits=16 wire_bits=32 gain_db=%u clip_gain_db=%u frames=%u target=%u "
           "bytes=%u rms=%u,%u peak=%u,%u clipped=%u,%u live=%u,%u blocks=%u "
           "read_errors=%u queue_overflows=%u session_errors=%u session_overflows=%u stack_min=%u last_error=%s\n",
           ready,active,RATE,gain,record_gain,count,wanted,count*4,count?(unsigned)sqrt(e[0]/(double)count):0,
           count?(unsigned)sqrt(e[1]/(double)count):0,p[0],p[1],c[0],c[1],l[0],l[1],total_blocks,
           errors,missed,se,so,stack,esp_err_to_name(err));
}
void mic_diagnose(void)
{
    if (!ready) { printf("ERR microphone unavailable\n"); return; }
    unsigned masks[2],zeros[2]; uint32_t words[8];
    portENTER_CRITICAL(&lock);
    memcpy(masks,diagnostic_mask,sizeof(masks)); memcpy(zeros,diagnostic_zeros,sizeof(zeros));
    memcpy(words,diagnostic_words,sizeof(words));
    portEXIT_CRITICAL(&lock);
    printf("OK mic_diag phase_mask=%04X,%04X zeros_per_160=%u,%u raw_words=",masks[0],masks[1],zeros[0],zeros[1]);
    for (unsigned i=0;i<8;++i) printf("%s%08lX",i?",":"",(unsigned long)words[i]);
    const uint8_t registers[]={0x00,0x01,0x02,0x03,0x06,0x08,0x0B,0x11,0x12,0x40,0x43,0x44,0x4B,0x4C};
    for (unsigned i=0;i<sizeof(registers);++i) {
        uint8_t value;
        if (read_reg(registers[i],&value)==ESP_OK) printf(" reg%02X=%02X",registers[i],value);
    }
    printf("\n");
}
