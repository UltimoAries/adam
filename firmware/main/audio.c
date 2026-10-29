// ES8311 register setup adapted from the Espressif Apache-2.0 driver preserved
// in vendor/Arduino/examples/03_audio_out_no_tf/es8311.cpp (2015-2022 Espressif).
#include "audio.h"
#include "mic.h"
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "driver/gpio.h"
#include "driver/i2s_std.h"
#include "esp_rom_gpio.h"
#include "soc/i2s_periph.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"

enum { RATE=16000, FRAMES=160, DMA_COUNT=6, AMP=15, TABLE_SIZE=1024 };
typedef struct {
    unsigned hz,ms,id,clip_frames,channel,pcm_channels;
    bool chime,message;
    const int16_t *pcm;
    float pcm_gain;
} request_t;
static i2c_master_dev_handle_t codec;
static i2s_chan_handle_t tx,rx;
static QueueHandle_t requests;
static TaskHandle_t worker;
static portMUX_TYPE lock=portMUX_INITIALIZER_UNLOCKED;
static bool ready,playing,feeding;
static bool current_clip,pending_clip;
static int volume;
static unsigned generation,played,blocks,underruns,write_errors,worker_stack;
static unsigned delivered_message;
static esp_err_t last_error;
static uint8_t chip_hi,chip_lo;
static int16_t sine_table[TABLE_SIZE];
static int32_t samples[FRAMES*2];
extern const uint8_t listen_cue_start[] asm("_binary_listen_cue_pcm_start");
extern const uint8_t listen_cue_end[] asm("_binary_listen_cue_pcm_end");

static esp_err_t reg_write(uint8_t reg,uint8_t value)
{ uint8_t data[]={reg,value}; return i2c_master_transmit(codec,data,2,100); }
static esp_err_t reg_read(uint8_t reg,uint8_t *value)
{ return i2c_master_transmit_receive(codec,&reg,1,value,1,100); }
static esp_err_t codec_setup(void)
{
    esp_err_t err;
    // 16 kHz, 256*Fs MCLK (4.096 MHz), slave Philips I2S, 32-bit slots.
    // These coefficients are also the vendor 4096000 / 16000 table entry.
    const uint8_t setup[][2]={
        {0x00,0x00},{0x00,0x80},{0x01,0x3F},{0x02,0x00},
        {0x03,0x10},{0x04,0x10},{0x05,0x00},{0x06,0x03},
        {0x07,0x00},{0x08,0xFF},{0x09,0x10},{0x0A,0x10},
        {0x0D,0x01},{0x0E,0x02},{0x12,0x00},{0x13,0x10},
        {0x1C,0x6A},{0x37,0x08},{0x32,0xBF},{0x31,0x60},
    };
    if ((err=reg_write(0x00,0x1F))!=ESP_OK) return err;
    vTaskDelay(pdMS_TO_TICKS(20));
    for (unsigned i=0;i<sizeof(setup)/sizeof(setup[0]);++i)
        if ((err=reg_write(setup[i][0],setup[i][1]))!=ESP_OK) return err;
    // No positive DAC gain. User volume scales PCM samples, including true zero.
    uint8_t format,gain;
    if ((err=reg_read(0x09,&format))!=ESP_OK || (err=reg_read(0x32,&gain))!=ESP_OK) return err;
    return format==0x10 && gain==0xBF?ESP_OK:ESP_ERR_INVALID_RESPONSE;
}
static bool queue_overflow(i2s_chan_handle_t channel,i2s_event_data_t *event,void *context)
{
    portENTER_CRITICAL_ISR(&lock);
    if (feeding) ++underruns;
    portEXIT_CRITICAL_ISR(&lock);
    return false;
}
static bool write_block(void)
{
    size_t written=0;
    esp_err_t err=i2s_channel_write(tx,samples,sizeof(samples),&written,100);
    portENTER_CRITICAL(&lock);
    if (err!=ESP_OK || written!=sizeof(samples)) {
        ++write_errors; last_error=err==ESP_OK?ESP_ERR_INVALID_SIZE:err;
    } else ++blocks;
    portEXIT_CRITICAL(&lock);
    return err==ESP_OK && written==sizeof(samples);
}
static void audio_task(void *unused)
{
    request_t request;
    for (;;) {
        // Keep the common clock running while idle so capture is uninterrupted.
        if (xQueueReceive(requests,&request,0)!=pdPASS) {
            memset(samples,0,sizeof(samples));
            if (!write_block()) vTaskDelay(pdMS_TO_TICKS(10));
            continue;
        }
        portENTER_CRITICAL(&lock);
        bool current=request.id==generation;
        if (current) { current_clip=request.pcm!=NULL; pending_clip=false; }
        portEXIT_CRITICAL(&lock);
        if (!current) continue;
        if (!request.ms) continue;
        memset(samples,0,sizeof(samples));
        esp_err_t err=ESP_OK;
        bool ok=true;
        // Give the codec clocks and silence before enabling the amplifier.
        for (int i=0;i<DMA_COUNT && ok;++i) ok=write_block();
        if (ok) { err=reg_write(0x31,0x00); ok=err==ESP_OK; }
        portENTER_CRITICAL(&lock); playing=ok; feeding=ok; portEXIT_CRITICAL(&lock);
        uint32_t phase=0;
        float gain=0;
        unsigned total=RATE*request.ms/1000;
        bool amp_on=false,interrupted=false;
        for (unsigned offset=0;offset<total && ok;offset+=FRAMES) {
            portENTER_CRITICAL(&lock);
            int level=volume; bool stop=request.id!=generation;
            portEXIT_CRITICAL(&lock);
            bool enable=level>0;
            if (enable!=amp_on) { gpio_set_level(AMP,enable); amp_on=enable; }
            unsigned hz=request.chime?(unsigned[]){523,659,784}[(offset/(RATE*4/10))%3]:request.hz;
            uint32_t step=((uint64_t)hz<<32)/RATE;
            float target=level*level/10000.0f;
            for (unsigned i=0;i<FRAMES;++i) {
                unsigned pos=offset+i,note_pos=request.chime?pos%(RATE*4/10):pos;
                unsigned note_len=request.chime?RATE*3/10:total;
                float envelope=1;
                if (note_pos>=note_len || pos>=total) envelope=0;
                else if (note_pos<FRAMES) envelope=note_pos/(float)FRAMES;
                else if (note_len-note_pos<FRAMES) envelope=(note_len-note_pos)/(float)FRAMES;
                if (stop) envelope*=1-i/(float)FRAMES;
                gain+=(target-gain)*0.005f;
                float source=request.pcm?(pos<request.clip_frames?
                    request.pcm[pos*request.pcm_channels+request.channel]*request.pcm_gain:0):sine_table[phase>>22];
                int16_t value=(int16_t)(source*gain*envelope);
                phase+=step;
                samples[i*2]=samples[i*2+1]=(int32_t)value*65536;
            }
            ok=write_block();
            if (stop) { interrupted=true; break; }
        }
        // Drain through zeroes before muting; shared capture clocks keep running.
        portENTER_CRITICAL(&lock); feeding=false; portEXIT_CRITICAL(&lock);
        memset(samples,0,sizeof(samples));
        for (int i=0;i<DMA_COUNT+1 && ok;++i) ok=write_block();
        gpio_set_level(AMP,0);
        esp_err_t mute_err=reg_write(0x31,0x60);
        unsigned stack=uxTaskGetStackHighWaterMark(NULL);
        portENTER_CRITICAL(&lock);
        playing=false; current_clip=false; worker_stack=stack;
        if (ok && err==ESP_OK && mute_err==ESP_OK) ++played;
        if (request.message && !interrupted && ok && err==ESP_OK && mute_err==ESP_OK)
            delivered_message=request.id;
        if (err!=ESP_OK) last_error=err;
        if (mute_err!=ESP_OK) last_error=mute_err;
        portEXIT_CRITICAL(&lock);
    }
}
bool audio_init(i2c_master_bus_handle_t bus,int initial_volume)
{
    volume=initial_volume;
    gpio_set_level(AMP,0);
    const i2c_device_config_t cfg={.dev_addr_length=I2C_ADDR_BIT_LEN_7,.device_address=0x18,.scl_speed_hz=400000};
    esp_err_t err=i2c_master_bus_add_device(bus,&cfg,&codec);
    if (err!=ESP_OK) goto fail;
    if ((err=reg_read(0xFD,&chip_hi))!=ESP_OK || (err=reg_read(0xFE,&chip_lo))!=ESP_OK) goto fail;
    if (chip_hi!=0x83 || chip_lo!=0x11) { err=ESP_ERR_INVALID_RESPONSE; goto fail; }
    if ((err=codec_setup())!=ESP_OK) goto fail;
    i2s_chan_config_t channel=I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0,I2S_ROLE_MASTER);
    channel.dma_desc_num=DMA_COUNT; channel.dma_frame_num=FRAMES;
    channel.auto_clear_after_cb=true;
    if ((err=i2s_new_channel(&channel,&tx,&rx))!=ESP_OK) goto fail;
    const i2s_std_config_t standard={
        .clk_cfg=I2S_STD_CLK_DEFAULT_CONFIG(RATE),
        .slot_cfg=I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,I2S_SLOT_MODE_STEREO),
        .gpio_cfg={.mclk=2,.bclk=48,.ws=38,.dout=47,.din=39},
    };
    if ((err=i2s_channel_init_std_mode(tx,&standard))!=ESP_OK) goto fail;
    if ((err=i2s_channel_init_std_mode(rx,&standard))!=ESP_OK) goto fail;
    const i2s_event_callbacks_t callbacks={.on_send_q_ovf=queue_overflow};
    if ((err=i2s_channel_register_event_callback(tx,&callbacks,NULL))!=ESP_OK) goto fail;
    requests=xQueueCreate(1,sizeof(request_t));
    if (!requests) { err=ESP_ERR_NO_MEM; goto fail; }
    for (int i=0;i<TABLE_SIZE;++i) sine_table[i]=(int16_t)(4096*sinf(i*6.283185307f/TABLE_SIZE));
    if ((err=i2s_channel_enable(tx))!=ESP_OK) goto fail;
    if (xTaskCreatePinnedToCore(audio_task,"audio",4096,NULL,5,&worker,1)!=pdPASS) {
        i2s_channel_disable(tx);
        err=ESP_ERR_NO_MEM; goto fail;
    }
    ready=true;
    printf("Microphone initialization: %s\n",mic_init(bus,rx)?"ready":"failed");
    return true;
fail:
    last_error=err;
    if (requests) { vQueueDelete(requests); requests=NULL; }
    if (tx) { i2s_del_channel(tx); tx=NULL; }
    if (rx) { i2s_del_channel(rx); rx=NULL; }
    if (codec) { i2c_master_bus_rm_device(codec); codec=NULL; }
    return false;
}
bool audio_diagnostic_loopback(bool enable)
{
    if (!ready || mic_recording() || audio_clip_busy()) return false;
    int pin=enable?47:39;
    if (gpio_input_enable(pin)!=ESP_OK) return false;
    esp_rom_gpio_connect_in_signal(pin,i2s_periph_signal[0].data_in_sig,false);
    return true;
}
void audio_set_volume(int value)
{
    portENTER_CRITICAL(&lock); volume=value<0?0:value>100?100:value; portEXIT_CRITICAL(&lock);
}
static bool submit(unsigned hz,unsigned ms,bool chime)
{
    if (!ready) return false;
    portENTER_CRITICAL(&lock);
    request_t request={.hz=hz,.ms=ms,.id=++generation,.chime=chime};
    pending_clip=false;
    portEXIT_CRITICAL(&lock);
    return xQueueOverwrite(requests,&request)==pdPASS;
}
bool audio_play_chime(void) { return submit(523,1200,true); }
bool audio_play_tone(unsigned hz,unsigned ms)
{ return hz>=100 && hz<=4000 && ms>=100 && ms<=30000 && submit(hz,ms,false); }
bool audio_play_clip(unsigned channel)
{
    if (!ready || channel>1) return false;
    size_t bytes=0; const int16_t *pcm=mic_clip(&bytes);
    if (!pcm) return false;
    unsigned count=bytes/4,peak=0;
    for (unsigned i=0;i<count;++i) {
        int sample=pcm[i*2+channel]; unsigned absolute=sample<0?-sample:sample;
        if (absolute>peak) peak=absolute;
    }
    float scale=peak?8192.0f/peak:1; if (scale>32) scale=32;
    portENTER_CRITICAL(&lock);
    request_t request={.ms=(count*1000+RATE-1)/RATE,.id=++generation,
        .clip_frames=count,.channel=channel,.pcm=pcm,.pcm_gain=scale,.pcm_channels=2};
    pending_clip=true;
    portEXIT_CRITICAL(&lock);
    return xQueueOverwrite(requests,&request)==pdPASS;
}
unsigned audio_play_message(const int16_t *pcm,unsigned count)
{
    if (!ready || !pcm || !count || count>RATE*15 || mic_recording() || audio_is_playing() || audio_clip_busy()) return 0;
    portENTER_CRITICAL(&lock);
    request_t request={.ms=(count*1000+RATE-1)/RATE,.id=++generation,.clip_frames=count,
        .pcm=pcm,.pcm_gain=1,.pcm_channels=1,.message=true};
    pending_clip=true;
    portEXIT_CRITICAL(&lock);
    return xQueueOverwrite(requests,&request)==pdPASS?request.id:0;
}
bool audio_message_delivered(unsigned id)
{ portENTER_CRITICAL(&lock); bool value=id && delivered_message==id; portEXIT_CRITICAL(&lock); return value; }
unsigned audio_play_listen_cue(void)
{ return audio_play_message((const int16_t *)listen_cue_start,(listen_cue_end-listen_cue_start)/2); }
bool audio_clip_busy(void)
{ portENTER_CRITICAL(&lock); bool busy=current_clip||pending_clip; portEXIT_CRITICAL(&lock); return busy; }
void audio_stop(void) { submit(0,0,false); }
bool audio_is_ready(void) { return ready; }
bool audio_is_playing(void)
{ portENTER_CRITICAL(&lock); bool value=playing; portEXIT_CRITICAL(&lock); return value; }
void audio_status(void)
{
    portENTER_CRITICAL(&lock);
    bool active=playing; int level=volume;
    unsigned count=played,frames=blocks,errors=write_errors,missed=underruns,stack=worker_stack;
    esp_err_t err=last_error;
    portEXIT_CRITICAL(&lock);
    printf("OK audio ready=%d playing=%d volume=%d codec=%02X%02X rate=%d bits=32 slots=2 dma_bytes=%d "
           "completed=%u blocks=%u write_errors=%u queue_overflows=%u stack_min=%u last_error=%s\n",
           ready,active,level,chip_hi,chip_lo,RATE,DMA_COUNT*FRAMES*8,
           count,frames,errors,missed,stack,esp_err_to_name(err));
}
