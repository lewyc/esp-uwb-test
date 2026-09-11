set(root "${CMAKE_CURRENT_LIST_DIR}/upstream")
if(NOT EXISTS "${root}/dwt_uwb_driver/deca_device_api.h")
    message(FATAL_ERROR "Missing pinned driver. Run python tools/fetch_driver.py")
endif()
idf_component_register(SRCS "hal.c"
    "${root}/dwt_uwb_driver/deca_interface.c"
    "${root}/dwt_uwb_driver/deca_rsl.c"
    "${root}/dwt_uwb_driver/lib/qmath/src/qmath.c"
    "${root}/platform/deca_compat.c"
    "${root}/dwt_uwb_driver/dw3000/dw3000_device.c"
    INCLUDE_DIRS "." "${root}/platform" "${root}/dwt_uwb_driver"
    "${root}/dwt_uwb_driver/dw3000" "${root}/dwt_uwb_driver/lib/qmath/include"
    REQUIRES esp_driver_spi esp_driver_gpio esp_timer)
