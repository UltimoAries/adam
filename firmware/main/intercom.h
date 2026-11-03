#pragma once
#include <stdbool.h>
#include <stdint.h>

void intercom_init(void);
bool intercom_start(const char *url,const char *unit,const char *token,const char *call,const char *peer);
bool intercom_active(void);
bool intercom_connected(void);
void intercom_end(void);
void intercom_feed(const int16_t *stereo,unsigned frames);
void intercom_peer(char *out,unsigned size);
void intercom_status(void);
