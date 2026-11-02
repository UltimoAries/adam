cmake_minimum_required(VERSION 3.16)
set(COMPONENTS main)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(display_probe)
if(CONFIG_ADAM_BOARD_146)
    # Xtensa GCC 14.2 crashes in IDF's RGB-panel driver at -Og on a clean
    # Windows build. Compile the LCD component at -O2; keep assertions enabled.
    idf_component_get_property(adam_lcd_lib esp_lcd COMPONENT_LIB)
    target_compile_options(${adam_lcd_lib} PRIVATE -O2)
endif()
