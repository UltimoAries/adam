# Audio output and capacity (historical bring-up baselines)

> [!NOTE]
> This document records early hardware bring-up and FreeRTOS resource baselines measured on **Firmware v4–v12** (October 2026), when the unit rendered an animated eye/ear and ran the stock `Hey Jarvis` wake word. For current **Firmware v27** behavior — including the illustrated weather sky and LLM availability ring, **Hey Adam** (`hey_adam-20261030-c60f0456`), quieter 20 dB chime, Home Assistant control, and Raspberry Pi / GPU head-end architecture — see [`README.md`](../README.md), [`HEADEND_NETWORK.md`](HEADEND_NETWORK.md), [`SPACE_PANEL.md`](SPACE_PANEL.md), and [`TOUCH_UI.md`](TOUCH_UI.md).

Measured on the Waveshare **ESP32-S3-Touch-LCD-1.85C-BOX V2** using ESP-IDF 5.5.1.
The sections below preserve the progression from the v4 speaker baseline through
v5/v6 full-duplex microphone capture and v10–v12 wake-word + pre-roll integration.

## Speaker output and V2 hardware chain

**Settings → Audio** contains **CHIME**, **TONE**, **MIC TEST** and **STOP**. The
left curved volume rail adjusts hardware output (`0` mutes and disables the
amplifier; settings persist in NVS). Playback runs in its own FreeRTOS task so
display rendering and touch input stay responsive. **MIC TEST** records a short
stereo diagnostic clip into PSRAM, and **REPLAY** plays channel 1 back on the
enclosure speaker. Browser WAV inspection and playback are available on `/bench`
(see [HEADEND_TEST_UI.md](HEADEND_TEST_UI.md)).

The Waveshare V2 output chain is **ES8311 codec → NS4150B amplifier → speaker**:

- ES8311 responds at I2C address `0x18` (`chip ID 0x8311`) on shared bus `SDA 11` / `SCL 10`.
- Shared full-duplex I2S clocks/pins: `MCLK 2`, `BCLK 48`, `LRCK 38`, `DOUT 47`, `DIN 39` (ES7210 at `0x40`).
- Power amplifier enable: `GPIO 15` (enabled only during active playback with volume > 0).

Input and output share 16 kHz, 32-bit stereo I2S slots with six 160-frame DMA
buffers per direction (7,680 bytes per direction, 60 ms). Diagnostic clips store
the upper 16 bits of each 32-bit input slot; playback shifts 16-bit samples into
32-bit slots on core 1 (4,096-byte task stack). Acoustic checks verified three
clean ascending chime notes at volume 20, following the saved NVS volume curve.

## Early v5 capture baseline (16-bit I2S trial)

With the early animated eye, connected Wi-Fi, active 15-second stereo recording
and muted tone synthesis, rendering ran at 17.9–18.0 fps with 31.2–31.5%
combined dual-core CPU (~2.2% of one core for the microphone worker) and zero
capture overruns:

- **Internal heap free:** ~190.6 KiB (116 KiB largest contiguous block; 185.4 KiB minimum).
- **PSRAM free:** 6.55 MiB after reserving the 960,000-byte diagnostic stereo clip buffer.
- **Stack minima:** main `2,480` B, audio `2,776` B, mic `3,200` B.

*(Historical note: v5–v7 captured tones cleanly at 440 Hz, but speech sounded
choppy due to an ES7210 register `0x08` initialization bug that zeroed repeating
frames; Firmware v8 fixed register `0x08` to `0x10`, restoring clean speech
capture — see [HEADEND_TEST_UI.md](HEADEND_TEST_UI.md).)*

## Original v4 headroom baseline

Hardware resources on the ESP32-S3-Touch-LCD-1.85C-BOX V2: dual-core 240 MHz
Xtensa LX7, 512 KiB internal SRAM, 8 MiB octal PSRAM, and 16 MiB QSPI flash.

| Resource | v4 baseline (animated eye + connected Wi-Fi + active muted audio) |
| --- | --- |
| CPU core 0 | 59.2–59.4% busy |
| CPU core 1 | 1.4% busy; audio task ~1.3% of one core |
| Both cores, combined | 30.3–30.4% busy (~70% aggregate headroom) |
| Frame rate (early eye scene) | 18.2–18.3 fps (18.3–18.4 fps without audio) |
| Internal heap free | 202,383 bytes (~198 KiB out of 338 KiB managed heap) |
| Lowest internal heap observed | 195,067 bytes (~190 KiB) |
| Largest contiguous internal block | 126,976 bytes (124 KiB) |
| DMA-capable internal heap free | ~190 KiB (subset of internal SRAM) |
| PSRAM free | 7,839,488 bytes (7.48 MiB out of 8 MiB) |
| Firmware image (v4) | 903,184 bytes (0.86 MiB; 71% of 3 MiB app partition free) |
| Flash outside current partitions | 12.94 MiB unallocated |
| Stack minimum remaining | Main `2,576` B; audio `2,792` B; Wi-Fi event task `3,648` B |

The USB `resources` command exposes these live FreeRTOS and heap counters. CPU
percentages use two-second runtime windows and serve as practical load
indicators rather than hard real-time bounds.

## Architectural capacity constraints

1. **Display frame time:** One 360×360 RGB565 frame is 259,200 bytes (253 KiB),
   taking ~23 ms to render and ~30–31 ms to transfer over 40 MHz QSPI DMA. Heavy
   work must stay off the main render task.
2. **Internal SRAM vs. PSRAM:** QSPI/I2S DMA descriptors and task stacks require
   internal SRAM; large audio, pre-roll and page buffers belong in PSRAM. Always
   monitor largest contiguous internal block and per-task stack watermarks.
3. **Flash layout:** Heavy STT, Piper TTS and LLM weights run on the LAN head-end
   rather than on-device flash, leaving over half of the 3 MiB factory app
   partition free even with TFLite Micro and the embedded wake model.

## Wake inference and Wi-Fi paging baseline (v10–v12)

Firmware v10–v12 added native microWakeWord inference, an independent Wi-Fi
HTTP audio worker, bounded 15-second mono page playback (480,000 bytes in PSRAM),
and two seconds of rolling pre-roll history:

- **CPU load (idle with continuous wake inference + Wi-Fi):** ~50.5% combined
  (core 0 ~63%, core 1 ~37%; wake inference ~33% of one core, mic capture ~3.6%).
- **Memory (v12):** ~140 KiB internal heap free (56 KiB largest contiguous block)
  and ~6.42 MiB PSRAM free after allocating the TFLite Micro arena (64 KiB),
  page buffer, diagnostic buffer, and rolling pre-roll banks.
- **Stack margins after live Wi-Fi uploads:** ~2.5 KiB remaining on the network
  worker and ~5.1 KiB on the wake worker, with zero microphone read errors, RX
  overflows or wake queue drops.
- **Firmware size:** ~1.37–1.39 MiB (~56% of the 3 MiB factory partition free;
  later Firmware v13–v27 expanded the live capture spool to 544,000 PSRAM bytes
  for 13-second silence-ended questions while keeping a ~1.39 MiB binary).

## Primary hardware references

- [Waveshare ESP32-S3-Touch-LCD-1.85C documentation](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C)
- [Pinned Waveshare audio example (`8ead4a96`)](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85C/tree/8ead4a96bf3a278fc4ebd8ef4768657e17fa2880/Arduino/examples/03_audio_out_no_tf)
- [ES8311 audio codec datasheet](https://files.waveshare.com/wiki/common/ES8311.DS.pdf)

Pinned vendor reference sources and blob hashes are preserved under `vendor/`
and verified by `tools/fetch_audio_reference.py`.
