#include "preferences.h"
#include "audio.h"
#include <string.h>
#include <stdio.h>
#include <assert.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

static nvs_handle_t storage;
static bool storage_ok,network_initialized,network_enabled;
static int volume=50,brightness=50,retries;
static int64_t save_after,connect_after,connect_deadline;
static char ssid[33],password[65],address[20];
static enum { IDLE,STARTING,JOINING,CONNECTED,FAILED } state;
typedef struct {
    bool connected,scan_done;
    uint32_t scan_status;
    esp_ip4_addr_t ip;
    unsigned stack_min;
} network_event_t;
static QueueHandle_t network_events;
static unsigned event_stack_min;
static network_ap_t networks[32];
static unsigned network_count;
static bool scan_requested,scan_running;
static int64_t scan_deadline;
static enum { SCAN_IDLE,SCAN_WAITING,SCAN_ACTIVE,SCAN_READY,SCAN_FAILED } scan_state;

static void event(void *arg,esp_event_base_t base,int32_t id,void *data)
{
    network_event_t message={0};
    if (base==IP_EVENT && id==IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *got=data;
        message.connected=true;
        message.ip=got->ip_info.ip;
    } else if (base==WIFI_EVENT && id==WIFI_EVENT_SCAN_DONE) {
        message.scan_done=true;
        message.scan_status=((wifi_event_sta_scan_done_t *)data)->status;
    } else if (base!=WIFI_EVENT || id!=WIFI_EVENT_STA_DISCONNECTED) return;
    // Keep sys_evt callbacks small: copy raw data and format it in the main task.
    message.stack_min=uxTaskGetStackHighWaterMark(NULL);
    xQueueSend(network_events,&message,0);
}
static bool network_init(void)
{
    if (network_initialized) return true;
    network_events=xQueueCreate(8,sizeof(network_event_t));
    if (!network_events) return false;
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    assert(esp_netif_create_default_wifi_sta());
    wifi_init_config_t cfg=WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_STA_DISCONNECTED,event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,WIFI_EVENT_SCAN_DONE,event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,event,NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_start());
    network_initialized=true;
    return true;
}
void preferences_init(void)
{
    esp_err_t err=nvs_flash_init();
    if (err!=ESP_OK) { printf("Storage unavailable: %s\n",esp_err_to_name(err)); return; }
    storage_ok=nvs_open("eye_ui",NVS_READWRITE,&storage)==ESP_OK;
    if (!storage_ok) return;
    uint8_t value;
    if (nvs_get_u8(storage,"volume",&value)==ESP_OK && value<=100) volume=value;
    if (nvs_get_u8(storage,"brightness",&value)==ESP_OK && value>=10 && value<=100) brightness=value;
    size_t size=sizeof(ssid); nvs_get_str(storage,"ssid",ssid,&size);
    size=sizeof(password); nvs_get_str(storage,"password",password,&size);
    if (ssid[0]) network_connect(ssid,password);
}
int preference_volume(void) { return volume; }
int preference_brightness(void) { return brightness; }
void preference_levels(int v,int b)
{
    volume=v<0?0:v>100?100:v;
    brightness=b<10?10:b>100?100:b;
    audio_set_volume(volume);
    save_after=esp_timer_get_time()+1000000;
}
const char *network_ssid(void) { return ssid; }
const char *network_password(void) { return password; }
const char *network_state(void)
{
    switch(state) {
    case STARTING: case JOINING:return "CONNECTING";
    case CONNECTED:return "CONNECTED";
    case FAILED:return "JOIN FAILED";
    default:return ssid[0]?"SAVED":"NOT CONFIGURED";
    }
}
void network_ip(char *dest,unsigned size)
{ snprintf(dest,size,"%s",address); }
bool network_connect(const char *name,const char *pass)
{
    size_t sn=strlen(name),pn=strlen(pass);
    if (!sn || sn>32 || pn>63 || (pn>0 && pn<8)) return false;
    if (!network_init()) return false;
    // Inputs may point into the stored buffers during automatic reconnect.
    memmove(ssid,name,sn+1); memmove(password,pass,pn+1);
    network_enabled=false; state=STARTING; address[0]=0;
    esp_wifi_disconnect();
    if (storage_ok) {
        nvs_set_str(storage,"ssid",ssid); nvs_set_str(storage,"password",password); nvs_commit(storage);
    }
    retries=0;
    connect_after=esp_timer_get_time()+300000;
    return true;
}
void network_forget(void)
{
    network_enabled=false; connect_after=0; state=IDLE;
    if (network_initialized) esp_wifi_disconnect();
    memset(ssid,0,sizeof(ssid)); memset(password,0,sizeof(password));
    address[0]=0;
    if (storage_ok) { nvs_erase_key(storage,"ssid"); nvs_erase_key(storage,"password"); nvs_commit(storage); }
}
void network_scan(void)
{
    if (scan_running || scan_requested) return;
    network_count=0;
    if (!network_init()) { scan_state=SCAN_FAILED; return; }
    scan_requested=true; scan_state=SCAN_WAITING;
}
const char *network_scan_state(void)
{
    static const char *const names[]={"idle","waiting","scanning","ready","failed"};
    return names[scan_state];
}
unsigned network_scan_count(void) { return network_count; }
unsigned network_event_stack_min(void) { return event_stack_min; }
const network_ap_t *network_scan_result(unsigned i)
{ return i<network_count?&networks[i]:NULL; }
static void collect_scan(uint32_t status)
{
    if (!scan_running) { esp_wifi_clear_ap_list(); return; }
    scan_running=false; network_count=0;
    // Driver results are strongest-first; fetching releases the driver's AP list.
    static wifi_ap_record_t records[32];
    uint16_t count=32;
    if (status || esp_wifi_scan_get_ap_records(&count,records)!=ESP_OK) {
        esp_wifi_clear_ap_list(); scan_state=SCAN_FAILED; return;
    }
    for (unsigned i=0;i<count;++i) {
        char name[33]; memcpy(name,records[i].ssid,32); name[32]=0;
        if (!name[0]) continue; // Hidden SSIDs remain available through manual entry.
        bool duplicate=false;
        for (unsigned j=0;j<network_count;++j)
            if (!strcmp(name,networks[j].ssid)) { duplicate=true; break; }
        if (duplicate) continue;
        network_ap_t *ap=&networks[network_count++];
        snprintf(ap->ssid,sizeof(ap->ssid),"%s",name); ap->rssi=records[i].rssi;
        ap->supported=true; ap->open=false;
        const char *security;
        switch (records[i].authmode) {
        case WIFI_AUTH_OPEN: ap->open=true; security="OPEN"; break;
        case WIFI_AUTH_WPA2_PSK: security="WPA2"; break;
        case WIFI_AUTH_WPA_WPA2_PSK: security="WPA/WPA2"; break;
        case WIFI_AUTH_WPA3_PSK: security="WPA3"; break;
        case WIFI_AUTH_WPA2_WPA3_PSK: security="WPA2/WPA3"; break;
        default: security="UNSUPPORTED"; ap->supported=false; break;
        }
        snprintf(ap->security,sizeof(ap->security),"%s",security);
    }
    scan_state=SCAN_READY;
}
void preferences_tick(int64_t now)
{
    network_event_t message;
    while (network_events && xQueueReceive(network_events,&message,0)==pdTRUE) {
        if (!event_stack_min || message.stack_min<event_stack_min) event_stack_min=message.stack_min;
        if (message.scan_done) { collect_scan(message.scan_status); continue; }
        if (!network_enabled || state==STARTING) continue;
        if (message.connected) {
            snprintf(address,sizeof(address),IPSTR,IP2STR(&message.ip)); state=CONNECTED; retries=0;
        } else {
            address[0]=0;
            if (state==CONNECTED) connect_deadline=now+25000000;
            if (++retries<=3) { state=JOINING; esp_wifi_connect(); }
            else { state=FAILED; network_enabled=false; }
        }
    }
    if (save_after && now>=save_after) {
        if (storage_ok) {
            nvs_set_u8(storage,"volume",volume); nvs_set_u8(storage,"brightness",brightness); nvs_commit(storage);
        }
        save_after=0;
    }
    if (connect_after && now>=connect_after && !scan_running) {
        connect_after=0;
        wifi_config_t cfg={0};
        memcpy(cfg.sta.ssid,ssid,strlen(ssid)); memcpy(cfg.sta.password,password,strlen(password));
        cfg.sta.threshold.authmode=password[0]?WIFI_AUTH_WPA2_PSK:WIFI_AUTH_OPEN;
        cfg.sta.sae_pwe_h2e=WPA3_SAE_PWE_BOTH;
        network_enabled=true; state=JOINING;
        connect_deadline=now+25000000;
        if (esp_wifi_set_config(WIFI_IF_STA,&cfg)!=ESP_OK || esp_wifi_connect()!=ESP_OK) state=FAILED;
        memset(&cfg,0,sizeof(cfg));
    }
    if (state==JOINING && now>=connect_deadline) { network_enabled=false; state=FAILED; esp_wifi_disconnect(); }
    if (scan_running && now>=scan_deadline) {
        esp_wifi_scan_stop(); esp_wifi_clear_ap_list(); scan_running=false; scan_state=SCAN_FAILED;
    }
    // Scans do not block rendering or interrupt an active connection attempt.
    if (scan_requested && state!=STARTING && state!=JOINING && !scan_running) {
        scan_requested=false;
        if (esp_wifi_scan_start(NULL,false)==ESP_OK) {
            scan_running=true; scan_state=SCAN_ACTIVE; scan_deadline=now+12000000;
        } else scan_state=SCAN_FAILED;
    }
}
