#include "touch.h"
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "driver/gpio.h"
#include "esp_timer.h"

static i2c_master_dev_handle_t device;
static QueueHandle_t events;
static bool ready;
static uint8_t chip_id;
static volatile int last_x,last_y;
static volatile unsigned presses,releases,errors,dropped;
static esp_err_t read_reg(i2c_master_dev_handle_t dev,uint8_t reg,void *data,size_t len)
{ return i2c_master_transmit_receive(dev,&reg,1,data,len,30); }
static esp_err_t write_reg(i2c_master_dev_handle_t dev,uint8_t reg,uint8_t value)
{ uint8_t data[]={reg,value}; return i2c_master_transmit(dev,data,2,30); }
static void poll(void *unused)
{
    bool held=false;
    for (;;) {
        uint8_t data[5];
        if (read_reg(device,2,data,5)==ESP_OK) {
            bool down=(data[0]&0x0f)>0;
            int x=((data[1]&15)<<8)|data[2],y=((data[3]&15)<<8)|data[4];
            if (down && (x>=360 || y>=360)) { ++errors; vTaskDelay(pdMS_TO_TICKS(10)); continue; }
            if (down!=held || (down && (x!=last_x || y!=last_y))) {
                touch_event_t e={.x=down?x:last_x,.y=down?y:last_y,
                    .down=down,.pressed=down&&!held,.released=!down&&held};
                // Keep edge transitions; dropping motion samples is harmless for buttons.
                if (e.pressed || e.released) {
                    if (xQueueSend(events,&e,pdMS_TO_TICKS(30))!=pdTRUE) ++dropped;
                } else if (xQueueSend(events,&e,0)!=pdTRUE) ++dropped;
                if (e.pressed) ++presses;
                if (e.released) ++releases;
                if (down) { last_x=x; last_y=y; }
                held=down;
            }
        } else ++errors;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
bool touch_init(i2c_master_bus_handle_t bus,i2c_master_dev_handle_t ex)
{
    uint8_t out,config;
    if (read_reg(ex,1,&out,1)!=ESP_OK || read_reg(ex,3,&config,1)!=ESP_OK) return false;
    // P0 is touch reset; preserve P1 (LCD reset) and all other expander pins.
    if (write_reg(ex,1,out&~1)!=ESP_OK || write_reg(ex,3,config&~1)!=ESP_OK) return false;
    vTaskDelay(pdMS_TO_TICKS(10));
    if (write_reg(ex,1,out|1)!=ESP_OK) return false;
    vTaskDelay(pdMS_TO_TICKS(50));
    const gpio_config_t irq={.pin_bit_mask=1ULL<<4,.mode=GPIO_MODE_INPUT,.pull_up_en=1};
    ESP_ERROR_CHECK(gpio_config(&irq));
    const i2c_device_config_t cfg={.dev_addr_length=I2C_ADDR_BIT_LEN_7,.device_address=0x15,.scl_speed_hz=400000};
    if (i2c_master_bus_add_device(bus,&cfg,&device)!=ESP_OK) return false;
    if (read_reg(device,0xA7,&chip_id,1)!=ESP_OK || write_reg(device,0xFE,1)!=ESP_OK) return false;
    events=xQueueCreate(24,sizeof(touch_event_t));
    if (!events) return false;
    ready=xTaskCreate(poll,"touch",3072,NULL,4,NULL)==pdPASS;
    return ready;
}
bool touch_read(touch_event_t *event)
{ return ready && xQueueReceive(events,event,0)==pdTRUE; }
void touch_status(void)
{
    printf("OK touch ready=%d id=%02X raw=%d,%d presses=%u releases=%u errors=%u dropped=%u\n",
           ready,chip_id,last_x,last_y,presses,releases,errors,dropped);
}
