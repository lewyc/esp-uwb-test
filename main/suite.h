#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "cJSON.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
extern QueueHandle_t commands;
extern uint16_t node_id;
extern char run_id[64];
extern uint32_t boot_id;
void emit(cJSON *event); /* takes ownership; never blocks radio */
cJSON *event_new(const char *type);
void transport_start(void);
void wifi_provision(const char *ssid,const char *password);
void wifi_enable(bool enabled);
bool wifi_is_enabled(void);
void wifi_address(char *out,size_t n);
void radio_task(void *unused);
void command_submit(const char *line,bool usb);
void num(cJSON *j,const char *key,double v);
void str(cJSON *j,const char *key,const char *v);
