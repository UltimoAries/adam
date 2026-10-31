set(VENDOR_DIR "${CMAKE_CURRENT_LIST_DIR}/../../vendor/ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/main/LCD_Driver/esp_lcd_st77916")
idf_component_register(SRCS "main.c" "graphics.c" "space.c" "touch.c" "ui.c" "preferences.c" "audio.c" "audio_clocks.c" "mic.c" "headend.c" "preroll.c" "preroll_check.c" "wake.cpp" "metrics.c" "${VENDOR_DIR}/esp_lcd_st77916.c"
    INCLUDE_DIRS "." "${VENDOR_DIR}"
    REQUIRES esp_lcd esp_driver_gpio esp_driver_spi esp_driver_i2c
             esp_driver_ledc esp_driver_usb_serial_jtag esp_psram esp_timer vfs
             esp_wifi esp_event esp_netif nvs_flash esp_driver_i2s esp_driver_pcnt app_update bootloader_support spi_flash esp_http_client json micro_wake_word
    EMBED_FILES "wake_model.tflite" "listen_cue.pcm")
