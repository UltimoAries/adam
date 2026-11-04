#pragma once
#include "sdkconfig.h"
#ifndef CONFIG_ADAM_BOARD_146
#define CONFIG_ADAM_BOARD_146 0
#endif

// Keep the established 360x360 UI coordinate system on both boards.
#if CONFIG_ADAM_BOARD_146
#define ADAM_BOARD_NAME "Waveshare 1.46 SPD2010"
#define ADAM_FIRMWARE_ID "v33-146"
#define ADAM_PANEL_SIZE 412
#else
#define ADAM_BOARD_NAME "Waveshare 1.85C V2"
#define ADAM_FIRMWARE_ID "v33-185"
#define ADAM_PANEL_SIZE 360
#endif
