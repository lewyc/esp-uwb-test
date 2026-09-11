#include "suite.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "esp_system.h"
#include <string.h>
#include <math.h>
QueueHandle_t commands;
uint16_t node_id=1;
uint32_t boot_id;
char run_id[64]="idle";
void num(cJSON *j,const char *k,double v) { if(isfinite(v)) cJSON_AddNumberToObject(j,k,v); else cJSON_AddNullToObject(j,k); }
void str(cJSON *j,const char *k,const char *v) { cJSON_AddStringToObject(j,k,v); }
cJSON *event_new(const char *type) {
    cJSON *j=cJSON_CreateObject();
    num(j,"v",1); str(j,"type",type); num(j,"node",node_id); num(j,"boot",boot_id);
    str(j,"run",run_id); num(j,"device_us",esp_timer_get_time());
    return j;
}
void command_submit(const char *line,bool usb) {
    cJSON *j=cJSON_Parse(line);
    if(!cJSON_IsObject(j)) { cJSON_Delete(j); return; }
    cJSON *v=cJSON_GetObjectItemCaseSensitive(j,"v");
    cJSON *id=cJSON_GetObjectItemCaseSensitive(j,"request");
    cJSON *cmd=cJSON_GetObjectItemCaseSensitive(j,"cmd");
    if(!cJSON_IsNumber(v) || v->valueint!=1 || !cJSON_IsNumber(id) || id->valuedouble<1 || id->valuedouble>2147483647 || floor(id->valuedouble)!=id->valuedouble || !cJSON_IsString(cmd)) {
        cJSON *e=event_new("protocol_error"); str(e,"status","invalid_envelope"); emit(e); cJSON_Delete(j); return;
    }
    cJSON_DeleteItemFromObjectCaseSensitive(j,"_usb"); cJSON_AddBoolToObject(j,"_usb",usb);
    if(xQueueSend(commands,&j,0)!=pdTRUE) {
        cJSON *e=event_new("ack"); num(e,"request",id->valuedouble); str(e,"status","command_queue_full"); emit(e); cJSON_Delete(j);
    }
}
void app_main(void) {
    esp_err_t nvs_status=nvs_flash_init();
    if(nvs_status==ESP_ERR_NVS_NO_FREE_PAGES || nvs_status==ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_status=nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_status);
    nvs_handle_t h;
    if(nvs_open("uwb",NVS_READWRITE,&h)==ESP_OK) { nvs_get_u16(h,"node",&node_id); nvs_close(h); }
    boot_id=esp_random(); commands=xQueueCreate(16,sizeof(cJSON*));
    transport_start();
    cJSON *e=event_new("boot"); num(e,"reset_reason",esp_reset_reason()); emit(e);
    xTaskCreate(radio_task,"uwb_radio",12288,NULL,12,NULL);
}
