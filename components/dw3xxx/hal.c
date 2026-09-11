#include "hal.h"
#include "deca_interface.h"
#include "driver/spi_master.h"
#include "driver/gpio.h"
#include "esp_rom_sys.h"
#include "sdkconfig.h"
#include <string.h>

static spi_device_handle_t spi;
static TaskHandle_t radio_owner;
static uint8_t tx[6200];
static spi_device_interface_config_t dev = {
    .mode=0, .clock_speed_hz=2000000, .queue_size=1,
    .spics_io_num=CONFIG_UWB_SPI_CS,
};
static void irq(void *arg) {
    BaseType_t wake=pdFALSE;
    vTaskNotifyGiveFromISR(radio_owner, &wake);
    if(wake) portYIELD_FROM_ISR();
}
static void speed(int hz) {
    ESP_ERROR_CHECK(spi_bus_remove_device(spi));
    dev.clock_speed_hz=hz;
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &dev, &spi));
}
static void slow(void) { speed(2000000); }
void uwb_hal_fast(void) { speed(CONFIG_UWB_SPI_MHZ*1000000); }
static int32_t write_transfer(uint16_t hn, const uint8_t *h, uint16_t n,
                              const uint8_t *body, bool crc, uint8_t c) {
    size_t len=hn+n+(crc?1:0);
    if(len>sizeof(tx)) return DWT_ERROR;
    memset(tx,0,len); memcpy(tx,h,hn);
    if(body) memcpy(tx+hn,body,n);
    if(crc) tx[len-1]=c;
    spi_device_acquire_bus(spi,portMAX_DELAY);
    spi_transaction_t t={.length=len*8,.tx_buffer=tx};
    esp_err_t rc=spi_device_polling_transmit(spi,&t);
    spi_device_release_bus(spi);
    return rc==ESP_OK?DWT_SUCCESS:DWT_ERROR;
}
static int32_t rd(uint16_t hn,uint8_t *h,uint16_t n,uint8_t *b) {
    spi_device_acquire_bus(spi,portMAX_DELAY);
    spi_transaction_t header={.flags=SPI_TRANS_CS_KEEP_ACTIVE,.length=hn*8,.tx_buffer=h};
    spi_transaction_t body={.length=n*8,.rxlength=n*8,.rx_buffer=b};
    esp_err_t rc=spi_device_polling_transmit(spi,&header);
    if(rc==ESP_OK) rc=spi_device_polling_transmit(spi,&body);
    spi_device_release_bus(spi);
    return rc==ESP_OK?DWT_SUCCESS:DWT_ERROR;
}
static int32_t wr(uint16_t hn,const uint8_t *h,uint16_t n,const uint8_t *b) { return write_transfer(hn,h,n,b,false,0); }
static int32_t wc(uint16_t hn,const uint8_t *h,uint16_t n,const uint8_t *b,uint8_t c) { return write_transfer(hn,h,n,b,true,c); }
void wakeup_device_with_io(void) {
    gpio_set_level(CONFIG_UWB_WAKEUP,1); esp_rom_delay_us(600);
    gpio_set_level(CONFIG_UWB_WAKEUP,0); vTaskDelay(pdMS_TO_TICKS(3));
}
int32_t uwb_hal_probe_device_id(uint8_t out[4]) {
    uint8_t addr=0;
    wakeup_device_with_io();
    return rd(1,&addr,4,out);
}
/* All driver calls are owned by one task; IRQ never touches driver or SPI. */
decaIrqStatus_t decamutexon(void) { configASSERT(xTaskGetCurrentTaskHandle()==radio_owner); return 0; }
void decamutexoff(decaIrqStatus_t s) { (void)s; }
void deca_sleep(unsigned int ms) { vTaskDelay(pdMS_TO_TICKS(ms)+1); }
void deca_usleep(unsigned long us) { esp_rom_delay_us(us); }
extern const struct dwt_driver_s dw3000_driver;
static const struct dwt_spi_s ops={.readfromspi=rd,.writetospi=wr,.writetospiwithcrc=wc,.setslowrate=slow,.setfastrate=uwb_hal_fast};
static const struct dwt_driver_s *drivers[]={&dw3000_driver};
struct dwt_probe_s uwb_probe={.dw=NULL,.spi=(void*)&ops,.wakeup_device_with_io=wakeup_device_with_io,.driver_list=(struct dwt_driver_s**)drivers,.dw_driver_num=1};
bool uwb_hal_reset(void) {
    gpio_set_level(CONFIG_UWB_RESET,0);
    gpio_set_direction(CONFIG_UWB_RESET,GPIO_MODE_OUTPUT_OD);
    vTaskDelay(pdMS_TO_TICKS(2));
    gpio_set_level(CONFIG_UWB_RESET,1); /* releases line; never drives high */
    gpio_set_direction(CONFIG_UWB_RESET,GPIO_MODE_INPUT);
    /* RSTn is also a ready indication. The EVB/module pull-up must return it
     * high after the open-drain reset is released. */
    for(int i=0;i<20;i++) {
        if(gpio_get_level(CONFIG_UWB_RESET)) { vTaskDelay(pdMS_TO_TICKS(2)); return true; }
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    return false;
}
void uwb_hal_pin_levels(int *reset, int *irq, int *miso, int *cs) {
    if(reset)*reset=gpio_get_level(CONFIG_UWB_RESET);
    if(irq)*irq=gpio_get_level(CONFIG_UWB_IRQ);
    if(miso)*miso=gpio_get_level(CONFIG_UWB_SPI_MISO);
    if(cs)*cs=gpio_get_level(CONFIG_UWB_SPI_CS);
}
esp_err_t uwb_hal_init(TaskHandle_t owner) {
    radio_owner=owner;
    const int pins[]={CONFIG_UWB_SPI_CLK,CONFIG_UWB_SPI_MISO,CONFIG_UWB_SPI_MOSI,CONFIG_UWB_SPI_CS,CONFIG_UWB_IRQ,CONFIG_UWB_RESET,CONFIG_UWB_WAKEUP};
    for(int i=0;i<7;i++) {
        if(!GPIO_IS_VALID_GPIO(pins[i]) || (i!=1 && i!=4 && !GPIO_IS_VALID_OUTPUT_GPIO(pins[i]))) return ESP_ERR_INVALID_ARG;
        if(pins[i]==19 || pins[i]==20 || pins[i]==0 || pins[i]==45 || pins[i]==46 || (pins[i]>=26 && pins[i]<=37)) return ESP_ERR_INVALID_ARG;
        for(int j=0;j<i;j++) if(pins[i]==pins[j]) return ESP_ERR_INVALID_ARG;
    }
    gpio_config_t io={.pin_bit_mask=1ULL<<CONFIG_UWB_WAKEUP,.mode=GPIO_MODE_OUTPUT};
    ESP_ERROR_CHECK(gpio_config(&io)); gpio_set_level(CONFIG_UWB_WAKEUP,0);
    io=(gpio_config_t){.pin_bit_mask=1ULL<<CONFIG_UWB_IRQ,.mode=GPIO_MODE_INPUT,.intr_type=GPIO_INTR_POSEDGE};
    ESP_ERROR_CHECK(gpio_config(&io));
    /* A weak pull-up gives a deterministic 0xffffffff read when MISO is open,
     * making it distinguishable from a line held low. The DW3110 overrides it. */
    ESP_ERROR_CHECK(gpio_set_pull_mode(CONFIG_UWB_SPI_MISO,GPIO_PULLUP_ONLY));
    ESP_ERROR_CHECK(gpio_install_isr_service(0));
    ESP_ERROR_CHECK(gpio_isr_handler_add(CONFIG_UWB_IRQ,irq,NULL));
    spi_bus_config_t bus={.mosi_io_num=CONFIG_UWB_SPI_MOSI,.miso_io_num=CONFIG_UWB_SPI_MISO,.sclk_io_num=CONFIG_UWB_SPI_CLK,.quadwp_io_num=-1,.quadhd_io_num=-1,.max_transfer_sz=sizeof(tx)};
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST,&bus,SPI_DMA_CH_AUTO));
    return spi_bus_add_device(SPI2_HOST,&dev,&spi);
}
