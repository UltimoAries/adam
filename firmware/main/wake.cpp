// Local microWakeWord inference reuses the verified ES7210 capture worker.
#include "wake.h"
#include "wake_model_config.h"
extern "C" {
#include "headend.h"
#include "audio.h"
#include "mic.h"
}
#include <atomic>
#include <algorithm>
#include <cstring>
#include <new>
#include <stdio.h>
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "esphome/components/microphone/microphone.h"
#include "esphome/components/micro_wake_word/micro_wake_word.h"

extern const uint8_t model_data[] asm("_binary_wake_model_tflite_start");
static QueueHandle_t audio_queue;
static std::atomic<bool> enabled{true},feeding{false},initialized{false},resync{false};
static std::atomic<unsigned> detections{0},started_sessions{0},overflows{0},stack_min{0},loop_max_us{0};
struct Samples { int16_t pcm[160]; };
class Detector final : public esphome::micro_wake_word::MicroWakeWord {};
class SharedMicrophone : public esphome::microphone::Microphone {
 public:
  void start() override {
    xQueueReset(audio_queue);this->state_=esphome::microphone::STATE_RUNNING;feeding=true;
  }
  void stop() override { feeding=false;this->state_=esphome::microphone::STATE_STOPPED; }
  size_t read(int16_t *destination,size_t length) override {
    Samples samples;
    if (length<sizeof(samples.pcm) || xQueueReceive(audio_queue,&samples,pdMS_TO_TICKS(100))!=pdTRUE) return 0;
    memcpy(destination,samples.pcm,sizeof(samples.pcm));return sizeof(samples.pcm);
  }
};
static void task(void *)
{
    SharedMicrophone microphone;
    auto *detector=new(std::nothrow) Detector;
    if (!detector) { vTaskDelete(nullptr); return; }
    detector->set_microphone(&microphone);detector->set_features_step_size(WAKE_STEP_MS);
    // The upstream arena number is a minimum; allow newer TFLM allocator overhead.
    detector->add_wake_word_model(model_data,WAKE_THRESHOLD,WAKE_WINDOW,WAKE_PHRASE,std::max<unsigned>(WAKE_ARENA,65536));
    detector->add_detection_callback([](std::string) {
        ++detections;if (headend_start(WAKE_MODEL_ID)) ++started_sessions;
    });
    detector->setup();
    if (detector->is_failed()) { delete detector; vTaskDelete(nullptr); return; }
    int64_t cooldown=0;
    for (;;) {
        int64_t now=esp_timer_get_time();
        bool busy=headend_busy() || audio_is_playing() || audio_clip_busy() || mic_recording();
        if (busy) cooldown=now+1000000;
        bool paused=!enabled || busy || now<cooldown;
        if (resync.exchange(false)) { paused=true; cooldown=now+1000000; }
        if (paused && detector->is_running()) detector->stop();
        else if (!paused && !detector->is_running()) {
            detector->start();
            if (detector->status_has_error()) { initialized=false;enabled=false; }
            else initialized=detector->is_running();
        }
        int64_t start=esp_timer_get_time();detector->loop();
        unsigned elapsed=(unsigned)(esp_timer_get_time()-start);
        if (elapsed>loop_max_us) loop_max_us=elapsed;
        stack_min=uxTaskGetStackHighWaterMark(nullptr);vTaskDelay(pdMS_TO_TICKS(1));
    }
}
void wake_init(void)
{
    audio_queue=xQueueCreate(16,sizeof(Samples));
    if (audio_queue && xTaskCreatePinnedToCore(task,"wake",8192,nullptr,3,nullptr,1)!=pdPASS) {
        vQueueDelete(audio_queue);audio_queue=nullptr;
    }
}
void wake_feed(const int16_t *stereo,unsigned frames)
{
    if (!audio_queue || !feeding || frames!=160) return;
    Samples samples;for (unsigned i=0;i<160;++i) samples.pcm[i]=stereo[i*2];
    if (xQueueSend(audio_queue,&samples,0)!=pdTRUE) { ++overflows;resync=true;xQueueReset(audio_queue); }
}
void wake_enable(bool value) { enabled=value; }
bool wake_enabled(void) { return enabled; }
const char *wake_model_id(void) { return initialized?(enabled?WAKE_MODEL_ID:"disabled"):"unavailable"; }
const char *wake_label(void) { return initialized?(enabled?WAKE_PHRASE:"Wake disabled"):"Wake unavailable"; }
void wake_status(void)
{
    printf("OK wake ready=%d enabled=%d model=%s development=%d feeding=%d detections=%u sessions=%u queue_overflows=%u stack_min=%u loop_max_us=%u\n",
        (int)initialized.load(),(int)enabled.load(),wake_model_id(),WAKE_DEVELOPMENT,(int)feeding.load(),detections.load(),started_sessions.load(),overflows.load(),stack_min.load(),loop_max_us.load());
}
