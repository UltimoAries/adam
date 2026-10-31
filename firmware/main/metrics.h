#pragma once
#include <stdint.h>
void metrics_init(void);
void metrics_tick(int64_t now);
void metrics_status(void);
