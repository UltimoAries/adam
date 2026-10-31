file(GLOB_RECURSE srcs
          "src/*.c")

idf_component_register(SRCS "${srcs}"
                    INCLUDE_DIRS "include")

idf_build_set_property(CXX_COMPILE_OPTIONS "-DUSE_ESP32 -DUSE_ESP_IDF" APPEND)
