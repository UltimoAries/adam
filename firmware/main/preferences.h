#pragma once
#include <stdbool.h>
#include <stdint.h>
void preferences_init(void);
int preference_volume(void);
int preference_brightness(void);
void preference_levels(int volume,int brightness);
void preferences_tick(int64_t now);
const char *network_ssid(void);
const char *network_password(void);
const char *network_state(void);
void network_ip(char *dest,unsigned size);
bool network_connect(const char *ssid,const char *password);
void network_forget(void);
typedef struct {
    char ssid[33];
    int rssi;
    bool supported,open;
    char security[12];
} network_ap_t;
void network_scan(void);
const char *network_scan_state(void);
unsigned network_scan_count(void);
unsigned network_event_stack_min(void);
const network_ap_t *network_scan_result(unsigned index);
