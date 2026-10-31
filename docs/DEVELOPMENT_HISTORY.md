# Hardware bring-up and early development history (v1–v13)

> [!NOTE]
> This document records the initial October 2026 hardware bring-up milestones (**Firmware v1–v13**), from probing the ST77916 QSPI round LCD and CST816 touch controller through early eye/ear visuals and stock `Hey Jarvis` testing. For current **Firmware v27** behavior — including the illustrated sky/weather home screen, **Hey Adam** (`hey_adam-20261030-c60f0456`), Home Assistant integration, and Raspberry Pi / GPU LLM routing — see [`README.md`](../README.md), [`HEADEND_NETWORK.md`](HEADEND_NETWORK.md), [`SPACE_PANEL.md`](SPACE_PANEL.md), and [`TOUCH_UI.md`](TOUCH_UI.md).

Target hardware: Waveshare **ESP32-S3-Touch-LCD-1.85C-BOX V2** (silkscreen `V2`
and `E-S-T-L 1.85C-Box`), featuring an ESP32-S3 with 16 MiB QSPI flash, 8 MiB
octal PSRAM, and native USB Serial/JTAG.

## Dashboard Monitor Map prototype

The head-end dashboard at <http://127.0.0.1:8765> includes **Monitor Map**, an
interactive Three.js (`0.180.0`, bundled locally under
`headend/web/dist/vendor/three` with provenance and MIT license in
`provenance.json`) sample house layout supporting orbit, pan, zoom, top view (`T`)
and reset (`R`) via mouse, touch or keyboard. It renders on demand in WebGL 2
browsers as a placeholder spatial view without consuming ESP32 resources.

## Milestone v13 snapshot: touch menus, animated eye, speaker and microphone

By Firmware v13 and head-end v0.6, the bring-up stack established the core
device and head-end pipeline that later visual and model updates built upon:

- **Touch & audio controls:** Curved left volume and right brightness rails
  persisted in NVS (defaults 50%, zero mutes the NS4150B amplifier), Wi-Fi
  2.4 GHz scanning/entry, diagnostic chime/tone/8-second stereo mic test, and a
  **Voice** menu with local wake toggle and manual Wi-Fi listen.
- **Wake, pre-roll & silence endpoint:** Initial wake bring-up used the stock
  `Hey Jarvis` microWakeWord model before `Hey Adam` (`hey_adam-20261030-c60f0456`)
  was trained and embedded in v18+. Triggering freezes two seconds of rolling
  pre-roll history, plays an embedded 120 ms two-note chime while capture
  continues, and ends wake recordings automatically after 1.2 seconds of silence
  (up to a 13-second post-trigger cap; see [RESPONSE_LATENCY.md](RESPONSE_LATENCY.md)
  and [LISTENING_CUE.md](LISTENING_CUE.md)).
- **Head-end services:** Authenticated unit pairing, ordered Wi-Fi PCM uploads,
  local `faster-whisper` transcription, Piper ONNX voice library (`/voices`; see
  [VOICE_PACKAGES.md](VOICE_PACKAGES.md)), five-minute expiring TTS/recorded
  pages, `/bench` USB audio diagnostics, and Open-Meteo weather queries (for
  example, asking for the weather in **Seattle, Washington** on `/queries`; see
  [INFORMATION_QUERIES.md](INFORMATION_QUERIES.md)).
- **Driver fixes validated during bring-up:**
  - *ES7210 microphone capture (v8):* Preserved register `0x08` rate bits (`0x10`)
    to eliminate repeating zero-sample gaps, restoring clean speech and loopback capture.
  - *Wi-Fi event stack:* Increased the ESP-IDF system event task stack to 6,144
    bytes, eliminating DHCP/scan stack-overflow reboots while USB Serial/JTAG logging is active.

*(Note: Firmware v14–v27 subsequently replaced the early animated eye/ear with
the illustrated time-of-day weather sky, green/blue ripple mist and LLM
availability edge ring documented in [SPACE_PANEL.md](SPACE_PANEL.md).)*

## Milestone v1: initial ST77916 QSPI display bring-up

**Completed October 28, 2026:** Verified the exact Waveshare V2 pin map, panel
ID (`00 02 7F 7F`), alternate ST77916 manufacturer initialization sequence, RGB565
big-endian byte order, PWM backlight, and USB Serial/JTAG command interface by
booting cleanly into a uniform solid red screen (`#FF0000`, `rgb565=0xF800`) and
stepping through color/brightness/grid patterns:

```powershell
# ESP-IDF v5.5.1 installed at C:\Espressif
.\tools\idf.ps1 -DIDF_TARGET=esp32s3 build
.\tools\idf.ps1 -p COM6 flash

python tools/control.py status
python tools/control.py red
python tools/control.py fill 00FF00
python tools/control.py brightness 50
```

`tools/control.py` disables serial `DTR`/`RTS` assertions on open so sending USB
commands does not inadvertently reset the ESP32-S3.

## Confirmed Waveshare V2 hardware pinout

| Subsystem | Connection |
| --- | --- |
| Round LCD | ST77916, 360 × 360, QSPI (40 MHz pixel clock, 3 MHz ID read) |
| QSPI CLK / CS | `GPIO 40` / `GPIO 21` |
| QSPI D0 / D1 / D2 / D3 | `GPIO 46` / `GPIO 45` / `GPIO 42` / `GPIO 41` |
| Backlight | `GPIO 5`, active high, 5 kHz LEDC PWM |
| Shared I2C SDA / SCL | `GPIO 11` / `GPIO 10` |
| LCD reset | TCA9554 I/O expander (`0x20`) pin `P1` (`EXIO2`), active low |
| Capacitive touch | CST816 family (`0x15`, chip ID `0xB5`), interrupt `GPIO 4` |
| Touch reset | TCA9554 (`0x20`) pin `P0` (`EXIO1`), active low |
| Speaker codec | ES8311 (`0x18`, chip ID `0x8311`) |
| Shared I2S MCLK / BCLK / LRCK / DOUT | `GPIO 2` / `GPIO 48` / `GPIO 38` / `GPIO 47` |
| Microphone ADC | ES7210 (`0x40`), `DIN` on `GPIO 39` (ADC1/ADC2 stereo) |
| Speaker amplifier enable | `GPIO 15` (NS4150B; enabled only during unmuted playback) |

The firmware toggles only `P0` and `P1` on the TCA9554 expander so other expander
lines remain undisturbed. Rendering streams 12-row internal-SRAM DMA staging
buffers from a 360×360 PSRAM framebuffer, waiting for transfer completion before
buffer reuse. Full-duplex 16 kHz 32-bit stereo I2S clocks stay active with
silence on TX when idle so ES7210 capture clocks remain continuous.

## Bring-up verification sequence

1. **Solid color & panel ID:** Verify full-circle fill, panel ID `00 02 7F 7F`, and startup logs over USB.
2. **RGB565 & PWM brightness:** Verify red, green, blue, white and black bars plus 0–100% backlight steps.
3. **Coordinate grid & MADCTL:** Verify center (`180,180`), circular boundary clipping, clockwise rotation (`0/90/180/270`), and X/Y mirroring.
4. **Canvas primitives:** Verify clipped rectangles, lines, circles and ASCII text without DMA tearing.
5. **Touch & persistence:** Verify CST816 hit mapping, Wi-Fi scan/join, and NVS persistence across resets.

## Hardware sources

- [Waveshare ESP32-S3-Touch-LCD-1.85C V2 overview](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C)
- [Waveshare schematics and datasheets](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C/Resources-And-Documents)
- [Waveshare reference firmware (`8ead4a96`)](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85C/tree/8ead4a96bf3a278fc4ebd8ef4768657e17fa2880)

Pinned vendor driver files and their Apache-2.0 license live under `vendor/`
with SHA-1 Git blob manifests (`vendor/waveshare-tree.json`). The V2 panel
initialization table in `firmware/main/panel_init_v2.h` comes directly from
`ST77916.c`, with two local driver fixes applied in `firmware/`: MADCTL axis swap
uses the QSPI command-prefix helper, and SPI pixel-transfer errors propagate to
callers.
