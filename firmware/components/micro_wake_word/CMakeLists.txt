set(WAKE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../vendor/wake/micro_wake_word")
idf_component_register(SRCS
    "${WAKE_ROOT}/esphome/components/micro_wake_word/micro_wake_word.cpp"
    "${WAKE_ROOT}/esphome/components/micro_wake_word/streaming_model.cpp"
    "${WAKE_ROOT}/esphome/core/component.cpp"
    "${WAKE_ROOT}/esphome/core/helpers.cpp"
    "${WAKE_ROOT}/esphome/core/ring_buffer.cpp"
    INCLUDE_DIRS "${WAKE_ROOT}/include" "compat"
    REQUIRES micro_speech_features espressif__esp-tflite-micro esp_common esp_system esp_timer esp_rom freertos log)
target_compile_definitions(${COMPONENT_LIB} PUBLIC USE_ESP32 USE_ESP_IDF)
# Upstream diagnostic logs use %d for size_t on this 32-bit target.
target_compile_options(${COMPONENT_LIB} PRIVATE -Wno-format)
