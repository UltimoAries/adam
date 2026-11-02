// SPD2010 register protocol documented in the pinned Waveshare 1.46 example.
// Native IDF 5 I2C implementation, with bounded packet sizes and drain loops.
#include "touch.h"
#include <stdio.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "esp_timer.h"
#include "esp_rom_sys.h"

static i2c_master_dev_handle_t device;
static QueueHandle_t events;
static bool ready;
static int last_x,last_y;
static unsigned presses,releases,errors,dropped;
static esp_err_t last_error;
static unsigned last_status,last_length,last_hdp;

static esp_err_t read16(uint16_t reg,void *data,size_t size)
{
    uint8_t address[]={reg>>8,reg&255};
    return i2c_master_transmit_receive(device,address,2,data,size,30);
}
static esp_err_t command(uint16_t reg,uint8_t value)
{
    uint8_t data[]={reg>>8,reg&255,value,0};
    esp_err_t err=i2c_master_transmit(device,data,sizeof(data),30);
    esp_rom_delay_us(200);
    return err;
}
static esp_err_t sample(bool *down,int *x,int *y,bool *updated)
{
    uint8_t status[4];
    esp_err_t err=read16(0x2000,status,sizeof(status));
    if (err!=ESP_OK) return err;
    unsigned length=status[2]|(status[3]<<8);
    last_status=status[0]|(status[1]<<8); last_length=length;
    *updated=false;
    if (status[1]&0x40) {
        if ((err=command(0x0200,1))!=ESP_OK) return err;
        return command(0x0400,1);
    }
    if (status[1]&0x20) {
        if ((err=command(0x5000,0))!=ESP_OK || (err=command(0x4600,0))!=ESP_OK) return err;
        return command(0x0200,1);
    }
    // The point flag can remain set between reports while the CPU is running.
    // An empty report is an interrupt acknowledgement, not a malformed packet.
    if ((status[1]&8) && !length) return command(0x0200,1);
    if (status[0]&3) {
        uint8_t data[64];
        if (length<4 || length>sizeof(data)) return ESP_ERR_INVALID_SIZE;
        if ((err=read16(0x0003,data,length))!=ESP_OK) return err;
        *down=(status[0]&1) && length>=10 && data[4]<=10 && data[8]>0;
        if (*down) {
            unsigned raw_x=((data[7]&0xF0)<<4)|data[5];
            unsigned raw_y=((data[7]&15)<<8)|data[6];
            if (raw_x>=412 || raw_y>=412) return ESP_ERR_INVALID_RESPONSE;
            *x=raw_x*360/412; *y=raw_y*360/412;
        }
        *updated=true;
        for (unsigned packet=0;packet<4;++packet) {
            uint8_t hdp[8];
            if ((err=read16(0xFC02,hdp,sizeof(hdp)))!=ESP_OK) return err;
            last_hdp=hdp[5];
            if (hdp[5]==0x82) return command(0x0200,1);
            unsigned remain=hdp[2]|(hdp[3]<<8);
            if (hdp[5]!=0 || !remain || remain>sizeof(data)) return ESP_ERR_INVALID_RESPONSE;
            if ((err=read16(0x0003,data,remain))!=ESP_OK) return err;
        }
        return ESP_ERR_TIMEOUT;
    }
    if ((status[1]&8) && (!length || (status[0]&8))) return command(0x0200,1);
    return ESP_OK;
}
static void poll(void *unused)
{
    bool held=false;
    int64_t last_good=esp_timer_get_time();
    for (;;) {
        bool down=held,updated=false;
        int x=last_x,y=last_y;
        esp_err_t err=sample(&down,&x,&y,&updated);
        if (err!=ESP_OK) {
            ++errors;
            last_error=err;
            if (held && esp_timer_get_time()-last_good>250000) { down=false;updated=true; }
        } else last_good=esp_timer_get_time();
        if (updated && (down!=held || (down&&(x!=last_x||y!=last_y)))) {
            touch_event_t event={.x=down?x:last_x,.y=down?y:last_y,
                .down=down,.pressed=down&&!held,.released=!down&&held};
            if (xQueueSend(events,&event,(event.pressed||event.released)?pdMS_TO_TICKS(30):0)!=pdTRUE) ++dropped;
            if (event.pressed) ++presses;
            if (event.released) ++releases;
            if (down) { last_x=x;last_y=y; }
            held=down;
        }
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
bool touch_init(i2c_master_bus_handle_t bus,i2c_master_dev_handle_t expander)
{
    uint8_t address=1,out,config;
    if (i2c_master_transmit_receive(expander,&address,1,&out,1,30)!=ESP_OK) return false;
    address=3;
    if (i2c_master_transmit_receive(expander,&address,1,&config,1,30)!=ESP_OK) return false;
    uint8_t low[]={1,out&~1},direction[]={3,config&~1},high[]={1,out|1};
    if (i2c_master_transmit(expander,low,2,30)!=ESP_OK || i2c_master_transmit(expander,direction,2,30)!=ESP_OK) return false;
    vTaskDelay(pdMS_TO_TICKS(50));
    if (i2c_master_transmit(expander,high,2,30)!=ESP_OK) return false;
    vTaskDelay(pdMS_TO_TICKS(100));
    const i2c_device_config_t cfg={.dev_addr_length=I2C_ADDR_BIT_LEN_7,.device_address=0x53,.scl_speed_hz=400000};
    if (i2c_master_bus_add_device(bus,&cfg,&device)!=ESP_OK) return false;
    uint8_t version[18];
    if (read16(0x2600,version,sizeof(version))!=ESP_OK) return false;
    events=xQueueCreate(24,sizeof(touch_event_t));
    if (!events) return false;
    ready=xTaskCreate(poll,"touch",3072,NULL,4,NULL)==pdPASS;
    return ready;
}
bool touch_read(touch_event_t *event)
{ return ready && xQueueReceive(events,event,0)==pdTRUE; }
void touch_status(void)
{
    printf("OK touch ready=%d controller=SPD2010 logical=%d,%d presses=%u releases=%u errors=%u dropped=%u last_error=%s status=%04X length=%u hdp=%02X\n",
           ready,last_x,last_y,presses,releases,errors,dropped,esp_err_to_name(last_error),last_status,last_length,last_hdp);
}
