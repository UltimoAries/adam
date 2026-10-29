#include "audio_clocks.h"
#include <stdio.h>
#include "driver/pulse_cnt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "soc/soc.h"
#include "soc/i2s_reg.h"

static esp_err_t frequency(int pin,double *hz)
{
    pcnt_unit_handle_t unit=NULL; pcnt_channel_handle_t channel=NULL;
    bool enabled=false,running=false;
    const pcnt_unit_config_t config={.low_limit=-1,.high_limit=30000,.flags.accum_count=true};
    esp_err_t err=pcnt_new_unit(&config,&unit);
    if (err!=ESP_OK) return err;
    const pcnt_chan_config_t input={.edge_gpio_num=pin,.level_gpio_num=-1,.flags.io_loop_back=true};
    if ((err=pcnt_new_channel(unit,&input,&channel))!=ESP_OK) goto done;
    if ((err=pcnt_channel_set_edge_action(channel,PCNT_CHANNEL_EDGE_ACTION_INCREASE,PCNT_CHANNEL_EDGE_ACTION_HOLD))!=ESP_OK) goto done;
    if ((err=pcnt_unit_add_watch_point(unit,30000))!=ESP_OK) goto done;
    if ((err=pcnt_unit_enable(unit))!=ESP_OK) goto done;
    enabled=true;
    pcnt_unit_clear_count(unit);
    if ((err=pcnt_unit_start(unit))!=ESP_OK) goto done;
    running=true;
    int64_t start=esp_timer_get_time();
    vTaskDelay(pdMS_TO_TICKS(100));
    int count;
    err=pcnt_unit_get_count(unit,&count);
    int64_t elapsed=esp_timer_get_time()-start;
    *hz=count*1000000.0/elapsed;
done:
    if (running) pcnt_unit_stop(unit);
    if (enabled) pcnt_unit_disable(unit);
    if (channel) pcnt_del_channel(channel);
    pcnt_unit_remove_watch_point(unit,30000);
    pcnt_del_unit(unit);
    return err;
}
void audio_clock_diagnostics(void)
{
    double mclk=0,bclk=0,ws=0;
    esp_err_t a=frequency(2,&mclk),b=frequency(48,&bclk),c=frequency(38,&ws);
    printf("OK clocks mclk_hz=%.0f bclk_hz=%.0f ws_hz=%.0f errors=%s,%s,%s rx_channels=%lu tx_channels=%lu "
           "rx_tdm=%08lX tx_tdm=%08lX rx_conf1=%08lX tx_conf1=%08lX\n",
           mclk,bclk,ws,esp_err_to_name(a),esp_err_to_name(b),esp_err_to_name(c),
           (unsigned long)REG_GET_FIELD(I2S_RX_TDM_CTRL_REG(0),I2S_RX_TDM_TOT_CHAN_NUM)+1,
           (unsigned long)REG_GET_FIELD(I2S_TX_TDM_CTRL_REG(0),I2S_TX_TDM_TOT_CHAN_NUM)+1,
           (unsigned long)REG_READ(I2S_RX_TDM_CTRL_REG(0)),(unsigned long)REG_READ(I2S_TX_TDM_CTRL_REG(0)),
           (unsigned long)REG_READ(I2S_RX_CONF1_REG(0)),(unsigned long)REG_READ(I2S_TX_CONF1_REG(0)));
}
