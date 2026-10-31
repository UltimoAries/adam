#pragma once
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"

bool mic_init(i2c_master_bus_handle_t bus,i2s_chan_handle_t rx);
bool mic_ready(void);
bool mic_record(unsigned ms);
bool mic_set_gain(unsigned db);
unsigned mic_gain_db(void);
bool mic_recording(void);
void mic_stop(void);
bool mic_clear(void);
void mic_status(void);
void mic_diagnose(void);
unsigned mic_remaining_ms(void);
unsigned mic_duration_ms(void);
unsigned mic_level(unsigned channel);
const int16_t *mic_clip(size_t *bytes);
