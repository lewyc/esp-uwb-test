#include "suite.h"
#include "sdkconfig.h"
#include "driver/usb_serial_jtag.h"
#include "driver/uart.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"
#include "esp_timer.h"
#include "nvs.h"
#include "lwip/sockets.h"
#include "freertos/semphr.h"
#include <stdatomic.h>
#include <string.h>
#include <stdlib.h>
#include <stdio.h>

static QueueHandle_t output;
static SemaphoreHandle_t socket_lock;
static int client=-1;
static atomic_uint drops=0, sequence=0;
static bool enabled=false;
static esp_netif_t *netif;
static void disconnect_client(void) { if(client>=0) { shutdown(client,SHUT_RDWR); close(client); client=-1; } }
void emit(cJSON *j) {
    num(j,"seq",atomic_fetch_add(&sequence,1));
    num(j,"dropped_total",atomic_load(&drops));
    char *s=cJSON_PrintUnformatted(j); cJSON_Delete(j);
    if(!s || xQueueSend(output,&s,0)!=pdTRUE) { free(s); atomic_fetch_add(&drops,1); }
}
static int usb_read(void *b,int n) {
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    return usb_serial_jtag_read_bytes(b,n,pdMS_TO_TICKS(100));
#else
    return uart_read_bytes(UART_NUM_0,b,n,pdMS_TO_TICKS(100));
#endif
}
static void usb_write(const char *s,int n) {
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    int sent=usb_serial_jtag_write_bytes(s,n,pdMS_TO_TICKS(100));
    if(sent!=n) atomic_fetch_add(&drops,1);
#else
    uart_write_bytes(UART_NUM_0,s,n);
#endif
}
static void writer(void *arg) {
    char *s;
    while(1) if(xQueueReceive(output,&s,portMAX_DELAY)==pdTRUE) {
        size_t n=strlen(s); char *line=malloc(n+2);
        if(!line) { free(s); atomic_fetch_add(&drops,1); continue; }
        memcpy(line,s,n); line[n]='\n'; line[n+1]=0; free(s);
        usb_write(line,n+1);
        xSemaphoreTake(socket_lock,portMAX_DELAY);
        if(client>=0) {
            size_t offset=0;
            while(offset<n+1) {
                int k=send(client,line+offset,n+1-offset,0);
                if(k<=0) { disconnect_client(); break; }
                offset+=k;
            }
        }
        xSemaphoreGive(socket_lock); free(line);
    }
}
static void feed(char ch,char *line,size_t *len,bool *discard,bool usb) {
    if(ch=='\n') {
        if(!*discard) { line[*len]=0; if(*len) command_submit(line,usb); }
        else { cJSON *e=event_new("protocol_error"); str(e,"status","line_too_long"); emit(e); }
        *len=0; *discard=false;
    } else if(ch!='\r') {
        if(*len<2047) line[(*len)++]=ch; else *discard=true;
    }
}
static void usb_reader(void *arg) {
    char line[2048],buf[128]; size_t n=0; bool discard=false;
    while(1) { int k=usb_read(buf,sizeof(buf)); for(int i=0;i<k;i++) feed(buf[i],line,&n,&discard,true); }
}
static void server(void *arg) {
    int listener=socket(AF_INET,SOCK_STREAM,IPPROTO_IP);
    int yes=1; setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));
    struct sockaddr_in addr={.sin_family=AF_INET,.sin_port=htons(8765),.sin_addr.s_addr=htonl(INADDR_ANY)};
    if(bind(listener,(struct sockaddr*)&addr,sizeof(addr))<0 || listen(listener,1)<0) { close(listener); vTaskDelete(NULL); return; }
    while(1) {
        int fd=accept(listener,NULL,NULL); if(fd<0) { vTaskDelay(100); continue; }
        struct timeval tv={.tv_sec=0,.tv_usec=200000};
        setsockopt(fd,SOL_SOCKET,SO_SNDTIMEO,&tv,sizeof(tv));
        setsockopt(fd,SOL_SOCKET,SO_RCVTIMEO,&tv,sizeof(tv));
        xSemaphoreTake(socket_lock,portMAX_DELAY); client=fd; xSemaphoreGive(socket_lock);
        char line[2048],buf[128]; size_t n=0; bool discard=false;
        while(1) {
            xSemaphoreTake(socket_lock,portMAX_DELAY);
            int k=client<0 ? 0 : recv(client,buf,sizeof(buf),MSG_DONTWAIT);
            int err=errno;
            xSemaphoreGive(socket_lock);
            if(k==0 || (k<0 && err!=EAGAIN && err!=EWOULDBLOCK)) break;
            if(k>0) for(int i=0;i<k;i++) feed(buf[i],line,&n,&discard,false);
            vTaskDelay(pdMS_TO_TICKS(5));
        }
        xSemaphoreTake(socket_lock,portMAX_DELAY); disconnect_client(); xSemaphoreGive(socket_lock);
    }
}
static void wifi_event(void *arg,esp_event_base_t base,int32_t id,void *data) {
    if(base==WIFI_EVENT && id==WIFI_EVENT_STA_START && enabled) esp_wifi_connect();
    if(base==WIFI_EVENT && id==WIFI_EVENT_STA_DISCONNECTED && enabled) esp_wifi_connect();
    if(base==IP_EVENT && id==IP_EVENT_STA_GOT_IP) {
        char ip[24]; wifi_address(ip,sizeof(ip)); cJSON *e=event_new("network"); str(e,"ip",ip); emit(e);
    }
}
bool wifi_is_enabled(void) { return enabled; }
void wifi_address(char *out,size_t n) {
    esp_netif_ip_info_t info={0}; esp_netif_get_ip_info(netif,&info);
    snprintf(out,n,IPSTR,IP2STR(&info.ip));
}
void wifi_enable(bool on) {
    if(on==enabled) return;
    enabled=on;
    if(on) esp_wifi_start(); else esp_wifi_stop();
}
void wifi_provision(const char *ssid,const char *password) {
    wifi_enable(false);
    wifi_config_t conf={0};
    strlcpy((char*)conf.sta.ssid,ssid,sizeof(conf.sta.ssid));
    strlcpy((char*)conf.sta.password,password,sizeof(conf.sta.password));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA,&conf));
    wifi_enable(true);
}
void transport_start(void) {
    output=xQueueCreate(64,sizeof(char*)); socket_lock=xSemaphoreCreateMutex();
#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG
    usb_serial_jtag_driver_config_t cfg={.rx_buffer_size=2048,.tx_buffer_size=8192};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
#else
    uart_config_t cfg={.baud_rate=115200,.data_bits=UART_DATA_8_BITS,.parity=UART_PARITY_DISABLE,.stop_bits=UART_STOP_BITS_1,.flow_ctrl=UART_HW_FLOWCTRL_DISABLE,.source_clk=UART_SCLK_DEFAULT};
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0,&cfg));
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0,2048,8192,0,NULL,0));
#endif
    ESP_ERROR_CHECK(esp_netif_init()); ESP_ERROR_CHECK(esp_event_loop_create_default());
    netif=esp_netif_create_default_wifi_sta();
    wifi_init_config_t w=WIFI_INIT_CONFIG_DEFAULT(); ESP_ERROR_CHECK(esp_wifi_init(&w));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT,ESP_EVENT_ANY_ID,wifi_event,NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT,IP_EVENT_STA_GOT_IP,wifi_event,NULL));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
    xTaskCreate(writer,"telemetry",6144,NULL,4,NULL);
    xTaskCreate(usb_reader,"usb_input",4096,NULL,3,NULL);
    xTaskCreate(server,"tcp_input",4096,NULL,3,NULL);
}
