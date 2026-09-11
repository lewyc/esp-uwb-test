#include "suite.h"
#include "hal.h"
#include "twr_math.h"
#include "deca_version.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "esp_app_desc.h"
#include "sdkconfig.h"
#include "nvs.h"
#include <string.h>
#include <stdio.h>
#include <math.h>

#define FRAME_LEN 64
#define PAYLOAD 14
#define POLL 1
#define RESPONSE 2
#define FINAL 3
#define REPORT 4
#define PACKET 5
#define REPLY_UUS 5000
#define ERROR_BITS (SYS_STATUS_ALL_RX_ERR|SYS_STATUS_ALL_RX_TO)
static bool ready=false, listening=false;
static int channel=5, ant_delay=16385;
static uint8_t frame_seq=0;
static uint32_t last_packet[256];
static uint32_t packet_duplicates=0,packet_count=0,rx_errors=0;
static uint32_t probe_raw_id=0; static int32_t probe_raw_rc=DWT_ERROR;
static uint64_t last_cir_us=0;
static double cir_hz=0;
static const char *last_error="ok";
static uint32_t last_status=0;
static dwt_config_t phy={.chan=5,.txPreambLength=DWT_PLEN_128,.rxPAC=DWT_PAC8,
    .txCode=9,.rxCode=9,.sfdType=1,.dataRate=DWT_BR_6M8,.phrMode=DWT_PHRMODE_STD,
    .phrRate=DWT_PHRRATE_STD,.sfdTO=129,.stsMode=DWT_STS_MODE_OFF,.stsLength=DWT_STS_LEN_64,.pdoaMode=DWT_PDOA_M0};
static void put16(uint8_t *p,uint16_t v) { p[0]=v;p[1]=v>>8; }
static uint16_t get16(const uint8_t *p) { return p[0]|((uint16_t)p[1]<<8); }
static void put32(uint8_t *p,uint32_t v) { for(int i=0;i<4;i++)p[i]=v>>(i*8); }
static uint32_t get32(const uint8_t *p) { uint32_t v=0;for(int i=3;i>=0;i--)v=(v<<8)|p[i];return v; }
static void put40(uint8_t *p,uint64_t v) { for(int i=0;i<5;i++)p[i]=v>>(i*8); }
static uint64_t get40(const uint8_t *p) { uint64_t v=0;for(int i=4;i>=0;i--)v=(v<<8)|p[i];return v; }
static uint64_t tx_time(void) { uint8_t b[5];dwt_readtxtimestamp(b);return get40(b); }
static uint64_t rx_time(void) { uint8_t b[5];dwt_readrxtimestamp_ipatov(b);return get40(b); }
static void make_frame(uint8_t *b,int type,uint16_t peer,uint32_t exchange) {
    memset(b,0,FRAME_LEN); b[0]=0x41;b[1]=0x88;b[2]=frame_seq++;
    put16(b+3,0xdeca);put16(b+5,peer);put16(b+7,node_id);b[9]=type;put32(b+10,exchange);
}
static void rx_start(void) { dwt_setrxtimeout(0);dwt_rxenable(DWT_START_RX_IMMEDIATE); }
static bool wait_status(uint32_t wanted,int ms) {
    int64_t until=esp_timer_get_time()+ms*1000;
    do {
        last_status=dwt_readsysstatuslo();
        if(last_status&wanted) { dwt_writesysstatuslo(wanted);return true; }
        if(last_status&ERROR_BITS) { dwt_writesysstatuslo(ERROR_BITS);last_error="rx_error";rx_errors++;return false; }
        ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(1));
    } while(esp_timer_get_time()<until);
    last_error="timeout";return false;
}
static bool send_frame(uint8_t *b,bool delayed,uint64_t when,bool receive) {
    dwt_forcetrxoff(); dwt_writesysstatuslo(0xffffffff);
    dwt_writetxdata(FRAME_LEN,b,0);dwt_writetxfctrl(FRAME_LEN,0,1);
    if(delayed)dwt_setdelayedtrxtime((uint32_t)(when>>8));
    dwt_setrxaftertxdelay(0);dwt_setrxtimeout(0);
    if(dwt_starttx((delayed?DWT_START_TX_DELAYED:DWT_START_TX_IMMEDIATE)|(receive?DWT_RESPONSE_EXPECTED:0))!=DWT_SUCCESS) {
        last_error="late_tx";return false;
    }
    return wait_status(DWT_INT_TXFRS_BIT_MASK,30);
}
static bool receive_frame(uint8_t *b,int type,uint16_t peer,uint32_t ex) {
    int64_t until=esp_timer_get_time()+30000;
    while(esp_timer_get_time()<until) {
        if(!wait_status(DWT_INT_RXFCG_BIT_MASK,30))return false;
        uint8_t rng;int n=dwt_getframelength(&rng);
        if(n==FRAME_LEN) {
            dwt_readrxdata(b,FRAME_LEN,0);
            if(b[0]==0x41 && b[1]==0x88 && get16(b+3)==0xdeca && get16(b+5)==node_id && get16(b+7)==peer && b[9]==type && get32(b+10)==ex)return true;
        }
        rx_start();
    }
    last_error="unexpected_frame";return false;
}
static uint64_t delayed_time(uint64_t reference) { return ((reference+REPLY_UUS*UUS_TO_DTU)&TS_MASK)&~UINT64_C(511); }
static void diagnostics(cJSON *j) {
    dwt_cirdiags_t diag={0}; int16_t rssi,fp;
    if(dwt_readdiagnostics_acc(&diag,DWT_ACC_IDX_IP_M)==DWT_SUCCESS) {
        num(j,"rx_power_dbm",dwt_calculate_rssi(&diag,DWT_ACC_IDX_IP_M,&rssi)==DWT_SUCCESS?rssi/256.0:NAN);
        num(j,"first_path_power_dbm",dwt_calculate_first_path_power(&diag,DWT_ACC_IDX_IP_M,&fp)==DWT_SUCCESS?fp/256.0:NAN);
        num(j,"first_path_index",diag.FpIndex/64.0); num(j,"accum_count",diag.accumCount);
    } else { num(j,"rx_power_dbm",NAN);num(j,"first_path_power_dbm",NAN); }
    num(j,"clock_offset_raw",dwt_readclockoffset()); num(j,"sts_quality",NAN);
    str(j,"diagnostic_frame","report_rx");
    int64_t now=esp_timer_get_time();
    if(cir_hz>0 && now-last_cir_us>=1000000.0/cir_hz) {
        static uint8_t bytes[1016*6]; static char hex[1016*12+1];
        dwt_readcir_48b(bytes,DWT_ACC_IDX_IP_M,0,1016);
        for(size_t i=0;i<sizeof(bytes);i++)sprintf(hex+2*i,"%02x",bytes[i]);
        str(j,"cir_hex",hex);num(j,"cir_samples",1016);str(j,"cir_format","ipatov_complex_48bit");
        last_cir_us=now;
    }
}
static bool initialise(void) {
    ready=false;uwb_hal_reset();
    uint8_t raw[4]={0}; probe_raw_rc=uwb_hal_probe_device_id(raw);
    probe_raw_id=(uint32_t)raw[0]|((uint32_t)raw[1]<<8)|((uint32_t)raw[2]<<16)|((uint32_t)raw[3]<<24);
    if(dwt_probe(&uwb_probe)!=DWT_SUCCESS) { last_error="probe_failed";return false; }
    int64_t end=esp_timer_get_time()+100000;
    while(!dwt_checkidlerc() && esp_timer_get_time()<end)vTaskDelay(1);
    if(!dwt_checkidlerc() || dwt_initialise(DWT_DW_INIT)!=DWT_SUCCESS) { last_error="initialise_failed";return false; }
    if(dwt_readdevid()!=0xdeca0302) { last_error="unexpected_device";return false; }
    uwb_hal_fast();phy.chan=channel;
    if(dwt_configure(&phy)!=DWT_SUCCESS) {last_error="configure_failed";return false;}
    dwt_txconfig_t txrf={.PGdly=0x34,.power=0xfdfdfdfd,.PGcount=0};dwt_configuretxrf(&txrf);
    dwt_setrxantennadelay(ant_delay);dwt_settxantennadelay(ant_delay);
    dwt_configciadiag(DW_CIA_DIAG_LOG_ALL);dwt_configeventcounters(1);
    dwt_setinterrupt(DWT_INT_RXFCG_BIT_MASK|DWT_INT_TXFRS_BIT_MASK|ERROR_BITS,0,DWT_ENABLE_INT_ONLY);
    dwt_writesysstatuslo(0xffffffff);ready=true;last_error="ok";return true;
}
static void information(cJSON *e) {
    str(e,"firmware",esp_app_get_description()->version);str(e,"idf",esp_get_idf_version());
    str(e,"driver_revision","effcf6e601d20c323e8932575692bd7d565b8720");
#if CONFIG_UWB_BOARD_XIAO
    str(e,"board","xiao_esp32s3");
#else
    str(e,"board","esp32s3_devkitc");
#endif
    cJSON_AddBoolToObject(e,"radio_ready",ready);num(e,"device_id",ready?dwt_readdevid():0);
    num(e,"channel",channel);num(e,"antenna_delay",ant_delay);num(e,"spi_mhz",CONFIG_UWB_SPI_MHZ);
    num(e,"preamble_symbols",128);num(e,"preamble_code",9);num(e,"data_rate_mbps",6.8);
    num(e,"sfd_type",1);num(e,"sfd_timeout",129);num(e,"reply_uus",REPLY_UUS);num(e,"tx_power",0xfdfdfdfd);
    str(e,"sts","off");str(e,"phr","standard");num(e,"cir_hz",cir_hz);
    cJSON *pins=cJSON_AddObjectToObject(e,"pins");num(pins,"clk",CONFIG_UWB_SPI_CLK);num(pins,"miso",CONFIG_UWB_SPI_MISO);num(pins,"mosi",CONFIG_UWB_SPI_MOSI);num(pins,"cs",CONFIG_UWB_SPI_CS);num(pins,"irq",CONFIG_UWB_IRQ);num(pins,"reset",CONFIG_UWB_RESET);num(pins,"wakeup",CONFIG_UWB_WAKEUP);
    cJSON_AddBoolToObject(e,"wifi_enabled",wifi_is_enabled());char ip[24];wifi_address(ip,sizeof(ip));str(e,"ip",ip);
    num(e,"heap_free",esp_get_free_heap_size());num(e,"packet_rx",packet_count);num(e,"duplicates",packet_duplicates);num(e,"rx_errors",rx_errors);
    if(ready) {
        dwt_forcetrxoff();uint16_t tv=dwt_readtempvbat();
        num(e,"chip_temperature_c",dwt_convertrawtemperature(tv>>8));num(e,"chip_voltage_v",dwt_convertrawvoltage(tv&255));
        dwt_deviceentcnts_t counts;dwt_readeventcounters(&counts);
        num(e,"hw_tx",counts.TXF);num(e,"hw_rx_good",counts.CRCG);num(e,"hw_crc_errors",counts.CRCB);num(e,"hw_rx_timeouts",counts.RTO);
        if(listening)rx_start();
    } else {num(e,"chip_temperature_c",NAN);num(e,"chip_voltage_v",NAN);}
}
static void initiate(uint16_t peer,uint32_t ex,cJSON *e) {
    uint8_t b[FRAME_LEN];uint64_t t[6]={0};
    make_frame(b,POLL,peer,ex);
    if(!send_frame(b,false,0,true))goto done;
    t[0]=tx_time();
    if(!receive_frame(b,RESPONSE,peer,ex))goto done;
    t[3]=rx_time();uint64_t delayed=delayed_time(t[3]);t[4]=(delayed+ant_delay)&TS_MASK;
    make_frame(b,FINAL,peer,ex);put40(b+PAYLOAD,t[0]);put40(b+PAYLOAD+5,t[3]);put40(b+PAYLOAD+10,t[4]);
    if(!send_frame(b,true,delayed,true))goto done;
    if(tx_time()!=t[4]) {last_error="tx_timestamp_mismatch";goto done;}
    if(!receive_frame(b,REPORT,peer,ex))goto done;
    for(int i=0;i<6;i++)t[i]=get40(b+PAYLOAD+i*5);
    double distance=twr_distance(t);
    if(!isfinite(distance)) {last_error="invalid_timestamps";goto done;}
    num(e,"range_m",distance);str(e,"status","ok");
    cJSON *times=cJSON_AddArrayToObject(e,"timestamps_dtu");for(int i=0;i<6;i++)cJSON_AddItemToArray(times,cJSON_CreateNumber(t[i]));
    diagnostics(e);return;
done:
    num(e,"range_m",NAN);num(e,"rx_power_dbm",NAN);num(e,"first_path_power_dbm",NAN);
    num(e,"first_path_index",NAN);num(e,"accum_count",NAN);num(e,"clock_offset_raw",NAN);
    num(e,"sts_quality",NAN);str(e,"status",last_error);
}
static void responder(void) {
    uint32_t s=dwt_readsysstatuslo();
    if(s&ERROR_BITS) {rx_errors++;dwt_writesysstatuslo(ERROR_BITS);rx_start();return;}
    if(!(s&DWT_INT_RXFCG_BIT_MASK))return;
    dwt_writesysstatuslo(DWT_INT_RXFCG_BIT_MASK);
    uint8_t b[FRAME_LEN],rng;int n=dwt_getframelength(&rng);
    if(n!=FRAME_LEN) {rx_start();return;}
    dwt_readrxdata(b,FRAME_LEN,0);
    if(b[0]!=0x41 || b[1]!=0x88 || get16(b+3)!=0xdeca || get16(b+5)!=node_id) {rx_start();return;}
    uint16_t peer=get16(b+7);uint32_t ex=get32(b+10);
    if(b[9]==PACKET) {
        bool dup=peer<256 && last_packet[peer]==ex;
        if(peer<256)last_packet[peer]=ex;
        packet_count++;if(dup)packet_duplicates++;
        cJSON *e=event_new("packet_rx");num(e,"peer",peer);num(e,"exchange",ex);cJSON_AddBoolToObject(e,"duplicate",dup);emit(e);rx_start();return;
    }
    if(b[9]!=POLL) {rx_start();return;}
    uint64_t t[6]={0};t[1]=rx_time();uint64_t delayed=delayed_time(t[1]);
    make_frame(b,RESPONSE,peer,ex);
    if(!send_frame(b,true,delayed,true))goto done;
    t[2]=tx_time();
    if(!receive_frame(b,FINAL,peer,ex))goto done;
    t[5]=rx_time();t[0]=get40(b+PAYLOAD);t[3]=get40(b+PAYLOAD+5);t[4]=get40(b+PAYLOAD+10);
    make_frame(b,REPORT,peer,ex);for(int i=0;i<6;i++)put40(b+PAYLOAD+i*5,t[i]);
    if(!send_frame(b,false,0,false))goto done;
    rx_start();return;
done:;
    cJSON *e=event_new("radio_error");num(e,"peer",peer);num(e,"exchange",ex);str(e,"status",last_error);emit(e);rx_start();
}
static int integer(cJSON *j,const char *key,int fallback,int lo,int hi) {
    cJSON *v=cJSON_GetObjectItemCaseSensitive(j,key);if(!v)return fallback;
    if(!cJSON_IsNumber(v)||v->valuedouble<lo||v->valuedouble>hi||floor(v->valuedouble)!=v->valuedouble)return -1;
    return v->valueint;
}
static void handle(cJSON *j) {
    int req=cJSON_GetObjectItem(j,"request")->valueint;
    const char *cmd=cJSON_GetObjectItem(j,"cmd")->valuestring;
    cJSON *ack=event_new("ack");num(ack,"request",req);const char *status="ok";
    cJSON *run=cJSON_GetObjectItem(j,"run");
    if(run && (!cJSON_IsString(run)||strlen(run->valuestring)>=sizeof(run_id))) {status="invalid_run";goto out;}
    if(run) {strlcpy(run_id,run->valuestring,sizeof(run_id));cJSON_ReplaceItemInObject(ack,"run",cJSON_CreateString(run_id));}
    if(!strcmp(cmd,"hello")||!strcmp(cmd,"health")) information(ack);
    else if(!strcmp(cmd,"reset")) {listening=false;status=initialise()?"ok":last_error;information(ack);}
    else if(!strcmp(cmd,"configure")) {
        int id=integer(j,"node",node_id,1,254),ch=integer(j,"channel",channel,5,9),ad=integer(j,"antenna_delay",ant_delay,0,65535);
        cJSON *cir=cJSON_GetObjectItem(j,"cir_hz");
        if(id<0||(ch!=5&&ch!=9)||ad<0||(cir&&(!cJSON_IsNumber(cir)||!isfinite(cir->valuedouble)||cir->valuedouble<0||cir->valuedouble>10))) {status="invalid_config";goto out;}
        node_id=id;channel=ch;ant_delay=ad;cir_hz=cir?cir->valuedouble:0;listening=false;
        nvs_handle_t h;ESP_ERROR_CHECK(nvs_open("uwb",NVS_READWRITE,&h));nvs_set_u16(h,"node",node_id);nvs_set_u16(h,"channel",channel);nvs_set_u16(h,"ant",ant_delay);nvs_commit(h);nvs_close(h);
        status=initialise()?"ok":last_error;information(ack);
    } else if(!strcmp(cmd,"listen")) {if(!ready)status="radio_not_ready";else {listening=true;dwt_forcetrxoff();dwt_writesysstatuslo(0xffffffff);rx_start();}}
    else if(!strcmp(cmd,"stop")) {listening=false;if(ready)dwt_forcetrxoff();}
    else if(!strcmp(cmd,"range")||!strcmp(cmd,"packet")) {
        int peer=integer(j,"peer",-1,1,254);
        if(peer<0||peer==node_id) {status="invalid_peer";goto out;}
        if(!ready) {status="radio_not_ready";goto out;}
        bool was_listening=listening;listening=false;last_error="ok";
        cJSON *e=event_new(!strcmp(cmd,"range")?"measurement":"packet_tx");num(e,"request",req);num(e,"exchange",req);num(e,"peer",peer);
        int64_t start=esp_timer_get_time();
        if(!strcmp(cmd,"range"))initiate(peer,req,e);
        else {uint8_t b[FRAME_LEN];make_frame(b,PACKET,peer,req);str(e,"status",send_frame(b,false,0,false)?"ok":last_error);}
        num(e,"exchange_ms",(esp_timer_get_time()-start)/1000.0);num(e,"status_bits",last_status);emit(e);
        if(was_listening) {listening=true;rx_start();}
    } else if(!strcmp(cmd,"wifi")) {
        cJSON *on=cJSON_GetObjectItem(j,"enabled");
        if(!cJSON_IsBool(on))status="invalid_enabled";
        else if(!cJSON_IsTrue(on)&&!cJSON_IsTrue(cJSON_GetObjectItem(j,"_usb")))status="disable_wifi_over_usb";
        else wifi_enable(cJSON_IsTrue(on));
    } else if(!strcmp(cmd,"provision")) {
        cJSON *ssid=cJSON_GetObjectItem(j,"ssid"),*pass=cJSON_GetObjectItem(j,"password");
        if(!cJSON_IsTrue(cJSON_GetObjectItem(j,"_usb")))status="usb_required";
        else if(!cJSON_IsString(ssid)||!cJSON_IsString(pass)||strlen(ssid->valuestring)==0||strlen(ssid->valuestring)>31||strlen(pass->valuestring)>63)status="invalid_credentials";
        else wifi_provision(ssid->valuestring,pass->valuestring);
    } else status="unknown_command";
out:
    str(ack,"status",status);emit(ack);
}
void radio_task(void *unused) {
    nvs_handle_t h;uint16_t v;
    if(nvs_open("uwb",NVS_READONLY,&h)==ESP_OK) {if(nvs_get_u16(h,"channel",&v)==ESP_OK)channel=v;if(nvs_get_u16(h,"ant",&v)==ESP_OK)ant_delay=v;nvs_close(h);}
    bool math_ok=twr_math_selftest();
    esp_err_t rc=math_ok?uwb_hal_init(xTaskGetCurrentTaskHandle()):ESP_FAIL;
    if(!math_ok)last_error="twr_math_selftest_failed";
    else if(rc==ESP_OK)initialise();else last_error="invalid_pin_or_spi_config";
    cJSON *e=event_new("radio_boot");str(e,"status",ready?"ok":last_error);
    cJSON_AddBoolToObject(e,"twr_math_selftest",math_ok);num(e,"probe_raw_rc",probe_raw_rc);num(e,"probe_raw_id",probe_raw_id);emit(e);
    while(1) {
        cJSON *j=NULL;
        if(xQueueReceive(commands,&j,0)==pdTRUE) {handle(j);cJSON_Delete(j);}
        if(ready&&listening)responder();
        ulTaskNotifyTake(pdTRUE,pdMS_TO_TICKS(1));
    }
}
