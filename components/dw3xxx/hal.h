#pragma once
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "deca_device_api.h"
esp_err_t uwb_hal_init(TaskHandle_t owner);
void uwb_hal_reset(void);
void uwb_hal_fast(void);
int32_t uwb_hal_probe_device_id(uint8_t out[4]);
extern struct dwt_probe_s uwb_probe;
