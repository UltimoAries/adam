set(FEATURE_ROOT "${CMAKE_CURRENT_LIST_DIR}/../../../vendor/wake/micro_speech_features")
file(GLOB FEATURE_SOURCES "${FEATURE_ROOT}/src/*.c")
idf_component_register(SRCS ${FEATURE_SOURCES} INCLUDE_DIRS "${FEATURE_ROOT}/include" "${FEATURE_ROOT}/src")
