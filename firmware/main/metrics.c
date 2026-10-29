#include "metrics.h"
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_flash.h"
#include "esp_ota_ops.h"
#include "esp_image_format.h"
#include "esp_partition.h"
#include "esp_timer.h"

static TaskStatus_t tasks[32];
enum { TRACKED=9 };
static const char *const names[]={"IDLE0","IDLE1","main","audio","wifi","touch","mic","headend","wake"};
static configRUN_TIME_COUNTER_TYPE previous[TRACKED],previous_clock;
static float usage[TRACKED];
static unsigned task_count,stack_min[TRACKED];
static bool baseline,valid;
static int64_t next_sample,sampled_at;
static uint32_t flash_bytes,image_bytes,app_bytes,unallocated_bytes;

void metrics_init(void)
{
    esp_flash_get_size(NULL,&flash_bytes);
    const esp_partition_t *app=esp_ota_get_running_partition();
    if (app) {
        app_bytes=app->size;
        esp_partition_pos_t position={.offset=app->address,.size=app->size};
        esp_image_metadata_t metadata;
        if (esp_image_get_metadata(&position,&metadata)==ESP_OK) image_bytes=metadata.image_len;
    }
    uint32_t last_end=0x10000;
    esp_partition_iterator_t it=esp_partition_find(ESP_PARTITION_TYPE_ANY,ESP_PARTITION_SUBTYPE_ANY,NULL);
    while (it) {
        const esp_partition_t *p=esp_partition_get(it);
        if (p->address+p->size>last_end) last_end=p->address+p->size;
        it=esp_partition_next(it);
    }
    unallocated_bytes=flash_bytes>last_end?flash_bytes-last_end:0;
}
void metrics_tick(int64_t now)
{
    if (now<next_sample) return;
    next_sample=now+2000000;
    configRUN_TIME_COUNTER_TYPE clock;
    unsigned count=uxTaskGetSystemState(tasks,sizeof(tasks)/sizeof(tasks[0]),&clock);
    if (!count) { valid=false; baseline=false; return; }
    configRUN_TIME_COUNTER_TYPE current[TRACKED]={0}; bool found[TRACKED]={0};
    for (unsigned i=0;i<count;++i) for (unsigned j=0;j<TRACKED;++j)
        if (!strcmp(tasks[i].pcTaskName,names[j])) {
            current[j]=tasks[i].ulRunTimeCounter;
            stack_min[j]=tasks[i].usStackHighWaterMark; found[j]=true;
        }
    if (baseline && clock>previous_clock && found[0] && found[1]) {
        for (unsigned j=0;j<TRACKED;++j) {
            usage[j]=found[j] && current[j]>=previous[j]?
                100.0*(current[j]-previous[j])/(clock-previous_clock):0;
            if (usage[j]>100) usage[j]=100;
        }
        valid=true; sampled_at=now;
    }
    memcpy(previous,current,sizeof(previous)); previous_clock=clock; baseline=true; task_count=count;
}
void metrics_status(void)
{
    unsigned internal=MALLOC_CAP_INTERNAL|MALLOC_CAP_8BIT;
    printf("OK memory internal_total=%u internal_free=%u internal_min=%u internal_largest=%u "
           "dma_free=%u dma_largest=%u psram_total=%u psram_free=%u psram_largest=%u main_stack_min=%u audio_stack_min=%u mic_stack_min=%u headend_stack_min=%u wake_stack_min=%u\n",
           (unsigned)heap_caps_get_total_size(internal),(unsigned)heap_caps_get_free_size(internal),
           (unsigned)heap_caps_get_minimum_free_size(internal),(unsigned)heap_caps_get_largest_free_block(internal),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_DMA|MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_total_size(MALLOC_CAP_SPIRAM),(unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_SPIRAM),stack_min[2],stack_min[3],stack_min[6],stack_min[7],stack_min[8]);
    printf("OK cpu valid=%d core0_busy=%.1f core1_busy=%.1f combined_busy=%.1f "
           "main_one_core=%.1f audio_one_core=%.1f wifi_one_core=%.1f touch_one_core=%.1f mic_one_core=%.1f headend_one_core=%.1f wake_one_core=%.1f tasks=%u sample_age_ms=%lu\n",
           valid,100-usage[0],100-usage[1],100-(usage[0]+usage[1])/2,
           usage[2],usage[3],usage[4],usage[5],usage[6],usage[7],usage[8],task_count,
           (unsigned long)((esp_timer_get_time()-sampled_at)/1000));
    printf("OK storage flash_total=%lu app_image=%lu app_partition=%lu app_free=%lu unallocated=%lu filesystem=0\n",
           (unsigned long)flash_bytes,(unsigned long)image_bytes,(unsigned long)app_bytes,
           (unsigned long)(app_bytes>image_bytes?app_bytes-image_bytes:0),(unsigned long)unallocated_bytes);
}
