#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/ledc.h"
#include "driver/spi_master.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_st77916.h"
#include "panel_init_v2.h"
#include "esp_timer.h"
#include "graphics.h"
#include "space.h"
#include "touch.h"
#include "ui.h"
#include "preferences.h"
#include "audio.h"
#include "audio_clocks.h"
#include "mic.h"
#include "headend.h"
#include "wake.h"
#include "metrics.h"

// Waveshare ESP32-S3-Touch-LCD-1.85C V2, manufacturer pin mapping.
enum { WIDTH = 360, HEIGHT = 360, STRIPE = 12, BACKLIGHT = 5 };
static esp_lcd_panel_handle_t panel;
static SemaphoreHandle_t transfer_done;
static uint8_t *pixels;
static unsigned current_rgb = 0xFF0000;
static unsigned brightness = 80;
static uint8_t panel_id[4];
static bool home_mode = true, display_on = true, mirror_x, mirror_y;
static unsigned rotation, target_fps = 30;
static uint32_t frame_count, render_us, transfer_us;
static float actual_fps;
static int64_t stats_since;
static unsigned stats_frames;
static bool touch_logging;
static bool ui_mode = true;

static bool color_done(esp_lcd_panel_io_handle_t io,
                       esp_lcd_panel_io_event_data_t *event, void *context)
{
    BaseType_t wake = pdFALSE;
    xSemaphoreGiveFromISR((SemaphoreHandle_t)context, &wake);
    return wake == pdTRUE;
}

static void set_brightness(unsigned value)
{
    ESP_ERROR_CHECK(ledc_set_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0,
                                 8191U * value / 100U));
    ESP_ERROR_CHECK(ledc_update_duty(LEDC_LOW_SPEED_MODE, LEDC_CHANNEL_0));
    brightness = value;
}

static void present(void)
{
    int64_t start = esp_timer_get_time();
    for (int y = 0; y < HEIGHT; y += STRIPE) {
        memcpy(pixels, gfx_buffer() + y * WIDTH, WIDTH * STRIPE * 2);
        ESP_ERROR_CHECK(esp_lcd_panel_draw_bitmap(panel, 0, y, WIDTH,
                                                 y + STRIPE, pixels));
        // Keep the buffer intact until the DMA transaction has completed.
        if (xSemaphoreTake(transfer_done, pdMS_TO_TICKS(2000)) != pdTRUE) {
            printf("ERR display transfer timeout\n");
            abort();
        }
    }
    transfer_us = esp_timer_get_time() - start;
    ++frame_count;
    ++stats_frames;
    int64_t now = esp_timer_get_time();
    if (now - stats_since >= 1000000) {
        actual_fps = stats_frames * 1000000.0f / (now-stats_since);
        stats_frames = 0;
        stats_since = now;
    }
}

static void fill(unsigned rgb)
{
    ui_mode = false;
    home_mode = false;
    gfx_clear(rgb);
    present();
    current_rgb = rgb;
    printf("OK fill #%06X\n", rgb);
}

static uint8_t read_reg(i2c_master_dev_handle_t device, uint8_t reg)
{
    uint8_t value;
    ESP_ERROR_CHECK(i2c_master_transmit_receive(device, &reg, 1, &value, 1, 200));
    return value;
}

static void write_reg(i2c_master_dev_handle_t device, uint8_t reg, uint8_t value)
{
    uint8_t data[] = {reg, value};
    ESP_ERROR_CHECK(i2c_master_transmit(device, data, sizeof(data), 200));
}

static void init_display(void)
{
    // Keep the amplifier quiet and the backlight off during panel startup.
    const gpio_config_t outputs = {
        .pin_bit_mask = (1ULL << BACKLIGHT) | (1ULL << 15),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_ERROR_CHECK(gpio_config(&outputs));
    ESP_ERROR_CHECK(gpio_set_level(BACKLIGHT, 0));
    ESP_ERROR_CHECK(gpio_set_level(15, 0));
    const ledc_timer_config_t timer = {
        .speed_mode = LEDC_LOW_SPEED_MODE, .timer_num = LEDC_TIMER_0,
        .duty_resolution = LEDC_TIMER_13_BIT, .freq_hz = 5000,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ESP_ERROR_CHECK(ledc_timer_config(&timer));
    const ledc_channel_config_t channel = {
        .gpio_num = BACKLIGHT, .speed_mode = LEDC_LOW_SPEED_MODE,
        .channel = LEDC_CHANNEL_0, .timer_sel = LEDC_TIMER_0, .duty = 0,
    };
    ESP_ERROR_CHECK(ledc_channel_config(&channel));

    i2c_master_bus_handle_t bus;
    const i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0, .sda_io_num = 11, .scl_io_num = 10,
        .clk_source = I2C_CLK_SRC_DEFAULT, .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_cfg, &bus));
    i2c_master_dev_handle_t expander;
    const i2c_device_config_t expander_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = 0x20, .scl_speed_hz = 400000,
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(bus, &expander_cfg, &expander));
    const uint8_t out = read_reg(expander, 1);
    const uint8_t config = read_reg(expander, 3);
    printf("TCA9554 detected, output=%02X config=%02X\n", out, config);
    // Only P1 (vendor EXIO2) is changed. Leave other expander pins intact.
    write_reg(expander, 1, out & ~0x02);
    write_reg(expander, 3, config & ~0x02);
    vTaskDelay(pdMS_TO_TICKS(10));
    write_reg(expander, 1, out | 0x02);
    vTaskDelay(pdMS_TO_TICKS(120));

    const spi_bus_config_t spi_cfg = {
        .sclk_io_num = 40, .data0_io_num = 46, .data1_io_num = 45,
        .data2_io_num = 42, .data3_io_num = 41,
        .data4_io_num = -1, .data5_io_num = -1,
        .data6_io_num = -1, .data7_io_num = -1,
        .max_transfer_sz = WIDTH * STRIPE * 2,
        .flags = SPICOMMON_BUSFLAG_MASTER,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &spi_cfg, SPI_DMA_CH_AUTO));
    esp_lcd_panel_io_spi_config_t io_cfg = {
        .cs_gpio_num = 21, .dc_gpio_num = -1, .spi_mode = 0,
        .pclk_hz = 3000000, .trans_queue_depth = 1,
        .lcd_cmd_bits = 32, .lcd_param_bits = 8,
        .flags.quad_mode = true,
    };
    esp_lcd_panel_io_handle_t io;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
                                            &io_cfg, &io));
    ESP_ERROR_CHECK(esp_lcd_panel_io_rx_param(io, 0x0B000400,
                                             panel_id, sizeof(panel_id)));
    printf("LCD ID: %02X %02X %02X %02X\n",
           panel_id[0], panel_id[1], panel_id[2], panel_id[3]);
    ESP_ERROR_CHECK(esp_lcd_panel_io_del(io));

    transfer_done = xSemaphoreCreateBinary();
    assert(transfer_done);
    io_cfg.pclk_hz = 40000000;
    io_cfg.on_color_trans_done = color_done;
    io_cfg.user_ctx = transfer_done;
    ESP_ERROR_CHECK(esp_lcd_new_panel_io_spi((esp_lcd_spi_bus_handle_t)SPI2_HOST,
                                            &io_cfg, &io));
    st77916_vendor_config_t vendor_cfg = {.flags.use_qspi_interface = true};
    const uint8_t alternate_id[] = {0x00, 0x02, 0x7F, 0x7F};
    const uint8_t default_id[] = {0x00, 0x7F, 0x7F, 0x7F};
    if (memcmp(panel_id, alternate_id, 4) == 0) {
        vendor_cfg.init_cmds = vendor_specific_init_new;
        vendor_cfg.init_cmds_size = sizeof(vendor_specific_init_new) /
                                     sizeof(vendor_specific_init_new[0]);
        printf("Using Waveshare alternate panel initialization\n");
    } else if (memcmp(panel_id, default_id, 4) == 0) {
        printf("Using Waveshare default panel initialization\n");
    } else {
        printf("ERR unexpected LCD ID; stop before panel initialization\n");
        abort();
    }
    const esp_lcd_panel_dev_config_t panel_cfg = {
        .reset_gpio_num = -1, .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16, .vendor_config = &vendor_cfg,
    };
    ESP_ERROR_CHECK(esp_lcd_new_panel_st77916(io, &panel_cfg, &panel));
    ESP_ERROR_CHECK(esp_lcd_panel_reset(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_init(panel));
    ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel, true));
    pixels = heap_caps_malloc(WIDTH * STRIPE * 2, MALLOC_CAP_DMA);
    assert(pixels);
    gfx_init();
    space_init();
    space_render(esp_timer_get_time(),SPACE_IDLE,false);
    present();
    set_brightness(preference_brightness());
    printf("Touch initialization: %s\n",touch_init(bus,expander)?"ready":"failed");
    printf("Audio initialization: %s\n",audio_init(bus,preference_volume())?"ready":"failed");
}

static bool number(const char *s, int min, int max, int *result)
{
    if (!s || !*s) return false;
    char *end;
    long value = strtol(s, &end, 10);
    if (*end || value < min || value > max) return false;
    *result = (int)value;
    return true;
}
static bool color(const char *s, unsigned *result)
{
    if (*s == '#') ++s;
    if (strlen(s) != 6 || strspn(s,"0123456789abcdefABCDEF") != 6) return false;
    *result = strtoul(s,NULL,16);
    return true;
}
static void orient(void)
{
    bool swap = rotation == 90 || rotation == 270;
    bool mx = (rotation == 90 || rotation == 180) ^ mirror_x;
    bool my = (rotation == 180 || rotation == 270) ^ mirror_y;
    ESP_ERROR_CHECK(esp_lcd_panel_swap_xy(panel,swap));
    ESP_ERROR_CHECK(esp_lcd_panel_mirror(panel,mx,my));
    present();
}
static void command(char *line)
{
    char *args[24], *save;
    int n = 0;
    for (char *p=strtok_r(line," \t",&save); p; p=strtok_r(NULL," \t",&save)) {
        if (n == 24) { printf("ERR too many arguments\n"); return; }
        args[n++] = p;
    }
    if (!n) return;
    int a,b,c,d;
    unsigned rgb;
    if (n==1 && !strcmp(args[0],"resources")) {
        metrics_status();
    } else if (n==5 && !strcmp(args[0],"headend") && !strcmp(args[1],"config")) {
        printf("%s headend configured\n",headend_configure(args[2],args[3],args[4])?"OK":"ERR");
    } else if (n==2 && !strcmp(args[0],"headend") && !strcmp(args[1],"status")) {
        headend_status();
    } else if (n==2 && !strcmp(args[0],"headend") && !strcmp(args[1],"selftest")) {
        printf("%s headend preroll selftest\n",headend_selftest()?"OK":"ERR");
    } else if (n==2 && !strcmp(args[0],"headend") && !strcmp(args[1],"listen")) {
        printf("%s headend listen\n",headend_start(NULL)?"OK":"ERR");
    } else if (n==2 && !strcmp(args[0],"headend") && !strcmp(args[1],"stop")) {
        headend_stop(); printf("OK headend stop\n");
    } else if (n==2 && !strcmp(args[0],"wake") && !strcmp(args[1],"status")) {
        wake_status();
    } else if (n==3 && !strcmp(args[0],"wake") && !strcmp(args[1],"enable") && number(args[2],0,1,&a)) {
        wake_enable(a); printf("OK wake enabled=%d\n",a);
    } else if (n==2 && !strcmp(args[0],"mic") && !strcmp(args[1],"status")) {
        mic_status();
    } else if (n==2 && !strcmp(args[0],"mic") && !strcmp(args[1],"diagnose")) {
        mic_diagnose();
    } else if (n==3 && !strcmp(args[0],"mic") && !strcmp(args[1],"record") &&
               number(args[2],100,15000,&a)) {
        printf("%s mic record\n",mic_record(a)?"OK":"ERR");
    } else if (n==3 && !strcmp(args[0],"mic") && !strcmp(args[1],"gain") &&
               number(args[2],0,36,&a)) {
        printf("%s mic gain\n",mic_set_gain(a)?"OK":"ERR");
    } else if (n==3 && !strcmp(args[0],"mic") && !strcmp(args[1],"play") &&
               number(args[2],1,2,&a)) {
        printf("%s mic play\n",audio_play_clip(a-1)?"OK":"ERR");
    } else if (n==2 && !strcmp(args[0],"mic") && !strcmp(args[1],"stop")) {
        mic_stop(); printf("OK mic stop\n");
    } else if (n==2 && !strcmp(args[0],"mic") && !strcmp(args[1],"clear")) {
        printf("%s mic clear\n",mic_clear()?"OK":"ERR");
    } else if (n==2 && !strcmp(args[0],"mic") && !strcmp(args[1],"download")) {
        size_t bytes=0;
        const uint8_t *pcm=(const uint8_t *)mic_clip(&bytes);
        if (!pcm) { printf("ERR no completed clip\n"); return; }
        printf("AUDIO %u 16000 2 16\n",(unsigned)bytes); fflush(stdout);
        for (size_t sent=0;sent<bytes;) {
            size_t chunk=bytes-sent; if (chunk>512) chunk=512;
            int wrote=usb_serial_jtag_write_bytes(pcm+sent,chunk,pdMS_TO_TICKS(3000));
            if (wrote<=0) break;
            sent+=wrote;
        }
    } else if (n==2 && !strcmp(args[0],"audio") && !strcmp(args[1],"status")) {
        audio_status();
    } else if (n==2 && !strcmp(args[0],"audio") && !strcmp(args[1],"clocks")) {
        audio_clock_diagnostics();
    } else if (n==3 && !strcmp(args[0],"audio") && !strcmp(args[1],"loopback") &&
               number(args[2],0,1,&a)) {
        printf("%s audio loopback\n",audio_diagnostic_loopback(a)?"OK":"ERR");
    } else if (n==2 && !strcmp(args[0],"audio") && !strcmp(args[1],"test")) {
        printf("%s audio chime\n",audio_play_chime()?"OK":"ERR");
    } else if (n==4 && !strcmp(args[0],"audio") && !strcmp(args[1],"tone") &&
               number(args[2],100,4000,&a) && number(args[3],100,30000,&b)) {
        printf("%s audio tone\n",audio_play_tone(a,b)?"OK":"ERR");
    } else if (n==2 && !strcmp(args[0],"audio") && !strcmp(args[1],"stop")) {
        audio_stop(); printf("OK audio stop\n");
    } else if (n==2 && !strcmp(args[0],"volume") && number(args[1],0,100,&a)) {
        preference_levels(a,preference_brightness()); printf("OK volume %d\n",a);
    } else if (n==2 && !strcmp(args[0],"ui") && !strcmp(args[1],"status")) {
        ui_status();
    } else if (n==4 && !strcmp(args[0],"ui") && !strcmp(args[1],"tap") &&
               number(args[2],0,359,&a) && number(args[3],0,359,&b)) {
        if (!ui_mode || !display_on) { printf("ERR UI is not active\n"); return; }
        touch_event_t tap={.x=a,.y=b,.down=true,.pressed=true};
        ui_touch(&tap,esp_timer_get_time());
        set_brightness(preference_brightness());
        printf("OK ui tap\n");
    } else if (n==2 && !strcmp(args[0],"menu") &&
               (!strcmp(args[1],"home") || !strcmp(args[1],"eye") || !strcmp(args[1],"settings"))) {
        ui_mode=true; touch_logging=false; display_on=true;
        ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel,true));
        set_brightness(preference_brightness()); ui_page(!strcmp(args[1],"settings"));
        printf("OK menu %s\n",args[1]);
    } else if (n==1 && !strcmp(args[0],"snapshot")) {
        printf("FRAME %d\n",WIDTH*HEIGHT*2); fflush(stdout);
        const uint8_t *frame=(const uint8_t *)gfx_buffer();
        for (int sent=0;sent<WIDTH*HEIGHT*2;) {
            int chunk=WIDTH*HEIGHT*2-sent; if (chunk>512) chunk=512;
            int wrote=usb_serial_jtag_write_bytes(frame+sent,chunk,pdMS_TO_TICKS(3000));
            if (wrote<=0) break;
            sent+=wrote;
        }
    } else if (n==2 && !strcmp(args[0],"touch") && !strcmp(args[1],"status")) {
        touch_status();
    } else if (n==4 && !strcmp(args[0],"touch") && !strcmp(args[1],"target") &&
               number(args[2],20,339,&a) && number(args[3],20,339,&b)) {
        ui_mode=false; home_mode=false; touch_logging=true; gfx_clear(0x030810);
        gfx_circle(a,b,18,0x00D6DD,false); gfx_circle(a,b,4,0xFFFFFF,true);
        gfx_line(a-25,b,a+25,b,0x00D6DD); gfx_line(a,b-25,a,b+25,0x00D6DD);
        gfx_text(126,285,2,0xFFFFFF,"TAP TARGET"); present();
        printf("OK touch target %d %d\n",a,b);
    } else if (n==2 && !strcmp(args[0],"touch") && !strcmp(args[1],"off")) {
        touch_logging=false; printf("OK touch logging off\n");
    } else if (n==1 && strcmp(args[0],"status")==0) {
        printf("OK status version=27 mode=%s size=360x360 last_fill=#%06X brightness=%u "
               "rotation=%u mirror=%d,%d display=%d lcd_id=%02X%02X%02X%02X "
               "qspi_hz=40000000 frames=%lu fps=%.1f target_fps=%u render_us=%lu "
               "transfer_us=%lu psram_free=%u\n",
               ui_mode?ui_name():home_mode?"home":"canvas",current_rgb,
               brightness,rotation,mirror_x,mirror_y,display_on,
               panel_id[0],panel_id[1],panel_id[2],panel_id[3],
               (unsigned long)frame_count,actual_fps,target_fps,(unsigned long)render_us,
               (unsigned long)transfer_us,
               (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    } else if (n==1 && strcmp(args[0],"red")==0) {
        fill(0xFF0000);
    } else if (n==2 && strcmp(args[0],"fill")==0 && color(args[1],&rgb)) {
        fill(rgb);
    } else if (n==2 && strcmp(args[0],"brightness")==0 && number(args[1],0,100,&a)) {
        set_brightness(a); printf("OK brightness %d\n",a);
    } else if (n==2 && strcmp(args[0],"pattern")==0 &&
               (!strcmp(args[1],"grid") || !strcmp(args[1],"colors"))) {
        ui_mode=false; home_mode=false; gfx_pattern(!strcmp(args[1],"grid")); present();
        printf("OK pattern %s\n",args[1]);
    } else if (n==2 && strcmp(args[0],"rotation")==0 &&
               number(args[1],0,270,&a) && a%90==0) {
        rotation=a; orient(); printf("OK rotation %d\n",a);
    } else if (n==3 && strcmp(args[0],"mirror")==0 &&
               number(args[1],0,1,&a) && number(args[2],0,1,&b)) {
        mirror_x=a; mirror_y=b; orient(); printf("OK mirror %d %d\n",a,b);
    } else if (n==2 && strcmp(args[0],"display")==0 &&
               (!strcmp(args[1],"on") || !strcmp(args[1],"off"))) {
        display_on=!strcmp(args[1],"on");
        ESP_ERROR_CHECK(esp_lcd_panel_disp_on_off(panel,display_on));
        if (display_on) present();
        printf("OK display %s\n",args[1]);
    } else if ((n==2 && !strcmp(args[0],"sky") && !strcmp(args[1],"live")) ||
               (n==3 && !strcmp(args[0],"sky") && number(args[1],0,23,&a) && number(args[2],0,59,&b))) {
        space_preview(n==2?-1:a*60+b,esp_timer_get_time());
        ui_mode=true;touch_logging=false;ui_page(false);
        printf("OK sky %s\n",n==2?"live":"sky preview (60 seconds)");
    } else if (n==3 && !strcmp(args[0],"sky") && !strcmp(args[1],"weather") &&
               (!strcmp(args[2],"live") || number(args[2],0,99,&a))) {
        space_weather_preview(!strcmp(args[2],"live")?-1:a,esp_timer_get_time());
        ui_mode=true;touch_logging=false;ui_page(false);
        printf("OK sky weather %s\n",!strcmp(args[2],"live")?"live":"preview (60 seconds)");
    } else if (n==2 && strcmp(args[0],"eye")==0 &&
               (!strcmp(args[1],"auto") || !strcmp(args[1],"hold"))) {
        ui_mode=true; touch_logging=false; ui_page(false);
        home_mode=true;
        printf("OK home (legacy eye command)\n");
    } else if (n==3 && strcmp(args[0],"look")==0 &&
               number(args[1],-100,100,&a) && number(args[2],-100,100,&b)) {
        ui_mode=true; touch_logging=false; ui_page(false);
        home_mode=true; printf("OK home (gaze retired)\n");
    } else if (n==1 && strcmp(args[0],"blink")==0) {
        ui_mode=true; touch_logging=false; ui_page(false);
        home_mode=true; printf("OK home (blink retired)\n");
    } else if (n==2 && strcmp(args[0],"pupil")==0 && number(args[1],12,50,&a)) {
        printf("OK home (pupil control retired)\n");
    } else if (n==2 && strcmp(args[0],"fps")==0 && number(args[1],5,30,&a)) {
        target_fps=a; printf("OK fps %d\n",a);
    } else if (n==6 && !strcmp(args[0],"rect") && number(args[1],0,359,&a) &&
               number(args[2],0,359,&b) && number(args[3],1,360,&c) &&
               number(args[4],1,360,&d) && color(args[5],&rgb)) {
        ui_mode=false; home_mode=false; gfx_rect(a,b,c,d,rgb); present(); printf("OK rect\n");
    } else if (n==6 && !strcmp(args[0],"line") && number(args[1],0,359,&a) &&
               number(args[2],0,359,&b) && number(args[3],0,359,&c) &&
               number(args[4],0,359,&d) && color(args[5],&rgb)) {
        ui_mode=false; home_mode=false; gfx_line(a,b,c,d,rgb); present(); printf("OK line\n");
    } else if (n==6 && !strcmp(args[0],"circle") && number(args[1],0,359,&a) &&
               number(args[2],0,359,&b) && number(args[3],1,360,&c) &&
               color(args[4],&rgb) && number(args[5],0,1,&d)) {
        ui_mode=false; home_mode=false; gfx_circle(a,b,c,rgb,d); present(); printf("OK circle\n");
    } else if (n>=6 && !strcmp(args[0],"text") && number(args[1],0,359,&a) &&
               number(args[2],0,359,&b) && number(args[3],1,6,&c) && color(args[4],&rgb)) {
        ui_mode=false; home_mode=false;
        for (int i=5; i<n; ++i) {
            gfx_text(a,b,c,rgb,args[i]); a+=(strlen(args[i])+1)*6*c;
        }
        present(); printf("OK text\n");
    } else if (n==1 && !strcmp(args[0],"help")) {
        printf("OK commands: resources | headend config URL UNIT TOKEN | headend status/selftest/listen/stop | "
               "wake status | wake enable 0/1 | mic status/diagnose/stop/clear | mic record 100..15000 | mic gain 0..36(step3) | "
               "mic download (binary) | mic play 1/2 | audio status/clocks/test/stop | audio loopback 0/1 | "
               "audio tone 100..4000 100..30000 | volume 0..100 | sky live/HH MM | sky weather live/CODE | "
               "status | ui status | ui tap X Y | menu home/settings | touch status | touch target X Y | "
               "touch off | snapshot (binary) | red | fill RRGGBB | brightness 0..100 | "
               "pattern grid/colors | rotation 0/90/180/270 | mirror 0/1 0/1 | "
               "display on/off | fps 5..30 | rect x y w h RRGGBB | "
               "line x1 y1 x2 y2 RRGGBB | circle x y r RRGGBB filled0/1 | "
               "text x y scale RRGGBB words\n");
    } else printf("ERR invalid command or arguments; use help\n");
}

void app_main(void)
{
    usb_serial_jtag_driver_config_t usb_cfg = {.rx_buffer_size=512,.tx_buffer_size=2048};
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&usb_cfg));
    usb_serial_jtag_vfs_use_driver();
    setvbuf(stdout,NULL,_IONBF,0);
    printf("Display UI v27: softer LLM ring glow, blue rain, quiet listening cue, Waveshare 1.85C V2\n");
    preferences_init();
    init_display();
    headend_init();
    wake_init();
    ui_init();
    metrics_init();
    printf("READY cosmic home, USB commands enabled, touch enabled\n");
    char line[192], input[128];
    size_t used=0;
    bool overflow=false;
    int64_t next_frame=0;
    for (;;) {
        int count=usb_serial_jtag_read_bytes(input,sizeof(input),0);
        for (int i=0; i<count; ++i) {
            char c=input[i];
            if (c=='\r' || c=='\n') {
                if (overflow) printf("ERR command too long\n");
                else if (used) { line[used]='\0'; command(line); }
                used=0; overflow=false;
            } else if (used<sizeof(line)-1 && !overflow) line[used++]=c;
            else overflow=true;
        }
        int64_t now=esp_timer_get_time();
        touch_event_t touch;
        while (touch_read(&touch)) {
            if (touch_logging && (touch.pressed || touch.released))
                printf("TOUCH %s raw=%d,%d\n",touch.pressed?"down":"up",touch.x,touch.y);
            if (ui_mode && display_on) {
                // Undo the panel's mirrors and axis swap to hit-test logical coordinates.
                if ((rotation==90 || rotation==180)^mirror_x) touch.x=359-touch.x;
                if ((rotation==180 || rotation==270)^mirror_y) touch.y=359-touch.y;
                if (rotation==90 || rotation==270) { int x=touch.x; touch.x=touch.y; touch.y=x; }
                ui_touch(&touch,now);
                if (touch.pressed && brightness!=preference_brightness()) set_brightness(preference_brightness());
            }
        }
        preferences_tick(now);
        metrics_tick(now);
        if ((ui_mode || home_mode) && display_on && now>=next_frame) {
            if (ui_mode) ui_render(now); else space_render(now,SPACE_IDLE,false);
            render_us=esp_timer_get_time()-now;
            present();
            next_frame=now+(ui_mode && strcmp(ui_name(),"home")?100000:1000000/target_fps);
        }
        vTaskDelay(1);
    }
}

