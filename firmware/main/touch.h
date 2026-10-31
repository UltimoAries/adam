#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "driver/i2c_master.h"
typedef struct { int x,y; bool down,pressed,released; } touch_event_t;
bool touch_init(i2c_master_bus_handle_t bus,i2c_master_dev_handle_t expander);
bool touch_read(touch_event_t *event);
void touch_status(void);
