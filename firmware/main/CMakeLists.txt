if(CONFIG_ADAM_BOARD_146)
    set(VENDOR_DIR "${CMAKE_CURRENT_LIST_DIR}/../../vendor/waveshare-146")
    set(PANEL_SOURCE "${VENDOR_DIR}/esp_lcd_spd2010.c")
    set(TOUCH_SOURCE "touch_spd2010.c")
else()
    set(VENDOR_DIR "${CMAKE_CURRENT_LIST_DIR}/../../vendor/ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/main/LCD_Driver/esp_lcd_st77916")
    set(PANEL_SOURCE "${VENDOR_DIR}/esp_lcd_st77916.c")
    set(TOUCH_SOURCE "touch.c")
endif()
idf_component_register(SRCS "main.c" "graphics.c" "space.c" "${TOUCH_SOURCE}" "ui.c" "preferences.c" "audio.c" "audio_clocks.c" "mic.c" "headend.c" "intercom.c" "barge.c" "preroll.c" "preroll_check.c" "wake.cpp" "metrics.c" "${PANEL_SOURCE}"
    INCLUDE_DIRS "." "${VENDOR_DIR}"
    REQUIRES esp_lcd esp_driver_gpio esp_driver_spi esp_driver_i2c
             esp_driver_ledc esp_driver_usb_serial_jtag esp_psram esp_timer vfs
             esp_wifi esp_event esp_netif nvs_flash esp_driver_i2s esp_driver_pcnt app_update bootloader_support spi_flash esp_http_client json micro_wake_word esp_websocket_client esp-sr
    EMBED_FILES "wake_model.tflite" "listen_cue.pcm")
if(CONFIG_ADAM_BOARD_146)
    # Scaling every frame to the denser panel is CPU-bound in the default -Og
    # build. Optimize the shared render/audio code without disabling assertions.
    target_compile_options(${COMPONENT_LIB} PRIVATE -O2)
endif()
