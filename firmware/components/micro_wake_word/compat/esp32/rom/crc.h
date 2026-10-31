#pragma once
// Preserve pinned ESPHome sources while selecting the target-neutral IDF API.
#include "esp_rom_crc.h"
#define crc16_le esp_rom_crc16_le
#define crc16_be esp_rom_crc16_be
