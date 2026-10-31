# Display and USB serial controls

Firmware v28 boots into the [illustrated sky home screen](SPACE_PANEL.md) with
six centered rows, white black-outlined lettering, live weather animations,
stacked countdown timer bars when active, green/blue activity mist and the
perimeter LLM availability ring. Tap once to reveal volume, brightness and
navigation controls; they hide automatically after eight seconds. Saved volume,
brightness, wake enable state, Wi-Fi credentials and head-end pairing survive
reboot.

USB diagnostics and control commands are newline-terminated ASCII sent over the
ESP32-S3 native USB Serial/JTAG port (for example `COM6` on Windows or
`/dev/ttyACM0` on Linux) via `tools/control.py`. See [TOUCH_UI.md](TOUCH_UI.md)
for on-screen menus.

`menu home` returns to the normal sky home screen (`menu eye` remains accepted
as a compatibility alias; legacy gaze, blink and pupil commands no longer render
an eye). `fps 5..30` adjusts the target frame-rate cap. `status` reports the
measured frame rate, render/transfer times and free PSRAM; frame rate reflects
the most recent animated interval when viewing a static diagnostic canvas.

## Display, UI and touch commands

| Command | Meaning |
| --- | --- |
| `status` / `help` | Report display/frame diagnostics / list serial commands |
| `menu home` / `menu settings` | Open the Home sky screen or Settings menu and disable raw touch logging (`menu eye` aliases `menu home`) |
| `ui status` | Active UI page, saved volume/brightness, wake toggle, Wi-Fi state/IP, press count and event-task stack margin |
| `ui tap X Y` | Inject a logical-coordinate (`0..359`) press into the active UI |
| `sky HH MM` / `sky live` | Preview an idle time-of-day sky palette for 60 seconds / restore live local time |
| `sky weather CODE` / `sky weather live` | Preview a WMO background weather scene for 60 seconds / restore live weather (text readings stay live) |
| `timer preview SEC [LABEL]` / `timer clear` | Add a diagnostic countdown timer bar on Home (`0..86400` s) / clear preview timers |
| `brightness 0..100` | Set backlight PWM (`0` turns the backlight off; touch UI enforces a `10` floor) |
| `fps 5..30` | Set target animation frame-rate limit |
| `display on` / `display off` | Enable or blank the ST77916 panel while retaining scene state and brightness |
| `rotation 0\|90\|180\|270` | Set clockwise panel rotation (`90` moves TOP to the right) |
| `mirror 0\|1 0\|1` | Set panel X/Y MADCTL mirror bits relative to rotation |
| `red` / `fill RRGGBB` | Pause animation and fill the 360×360 canvas with solid hex RGB |
| `pattern colors` | Draw red, green, blue, white, black and gray calibration bars |
| `pattern grid` | Draw a 30-pixel coordinate grid, edge labels and center crosshair at `180,180` |
| `rect X Y WIDTH HEIGHT RRGGBB` | Draw a clipped filled rectangle |
| `line X1 Y1 X2 Y2 RRGGBB` | Draw a clipped one-pixel line |
| `circle X Y RADIUS RRGGBB FILLED` | Draw a circle (`FILLED=0` outline, `FILLED=1` solid) |
| `text X Y SCALE RRGGBB WORDS` | Draw block ASCII text (`SCALE` `1..6`) |
| `touch status` | Touch controller ID, last raw contact coordinates, press/release and I2C error counts |
| `touch target X Y` | Show a diagnostic touch crosshair (`X`/`Y` `20..339`) and enable raw touch logging |
| `touch off` | Disable raw touch contact logging |
| `snapshot` | Stream raw RGB565 framebuffer over USB (use `tools/snapshot.py`, not `control.py`) |

## Wake, head-end, audio, microphone and resource commands

| Command | Meaning |
| --- | --- |
| `wake status` | Report embedded wake model ID (`hey_adam-20261030-c60f0456`), `ready`/`feeding`/enabled flags, detections and queue overflows |
| `wake enable 0\|1` | Mute (`0`) or enable (`1`) local on-device wake detection (persisted in NVS, matching **Settings → Voice**) |
| `headend status` | Pairing state, poll/upload status, pre-roll/captured duration, byte counters, LLM ring state and input gaps |
| `headend config ...` | Inspect, provision (`URL UNIT_ID DEVICE_KEY`) or clear head-end pairing over USB (normally driven by head-end USB pairing) |
| `headend listen` / `headend stop` | Start an immediate eight-second Wi-Fi capture with up to two seconds of pre-roll / cancel active capture or page playback |
| `headend selftest` | Run non-destructive ring-buffer wraparound, frozen-bank, silence-endpoint, delayed-reader and gap checks |
| `volume 0..100` | Set and persist speaker output volume (`0` mutes and disables the amplifier) |
| `audio status` | ES8311 codec state, active playback mode, underrun/write errors and audio task stack margin |
| `audio clocks` | Report shared I2S clock/channel readiness and active full-duplex slot configuration |
| `audio loopback ...` | Run diagnostic codec/I2S loopback verification |
| `audio test` | Play the built-in three-note speaker test chime |
| `audio tone HZ MS` | Play a sine tone (`100..4000` Hz for `100..30000` ms) |
| `audio stop` | Stop active or queued speaker playback |
| `mic status` | Live ES7210 input levels, recorded frames, gain, clipping count and read/overflow counters |
| `mic diagnose` | Read ES7210 registers and inspect raw I2S capture channel activity |
| `mic record MS` | Record stereo diagnostic clip into PSRAM for `100..15000` ms (in 10 ms steps) |
| `mic stop` / `mic clear` | Stop diagnostic recording early / clear the RAM diagnostic clip |
| `mic gain DB` | Set ES7210 input gain (`0..36` dB in 3 dB steps; default `36`, runtime-only) |
| `mic play 1\|2` | Replay recorded diagnostic microphone channel `1` or `2` through the speaker |
| `mic download` | Stream recorded stereo PCM over USB (use `tools/mic_capture.py` or `/bench`) |
| `resources` | Internal/DMA/PSRAM heap free and minimums, per-core/per-task CPU load and flash partition usage |

CPU estimates use two-second FreeRTOS runtime windows; ignore readings while
`valid=0`. Per-task percentages are relative to a single core, while
`combined_busy` averages both cores. Internal and DMA-capable heap figures
overlap and must not be summed. Stack minimums report remaining free bytes.
Flash `unallocated` reports flash capacity outside the current partition table,
not a mounted filesystem.

```powershell
python tools/control.py volume 20
python tools/control.py audio test
python tools/control.py wake status
python tools/control.py headend status
python tools/control.py resources
```

## Optional Python tool prerequisites

- `tools/control.py` and `tools/mic_capture.py` require `pyserial` (included in
  `headend/requirements-lock.txt`).
- `tools/snapshot.py` requires `pyserial` and `Pillow` (`pip install Pillow`) to
  decode the raw 360×360 RGB565 framebuffer into PNG images.
- Optional camera-based bench scripts (`tools/capture.py`, `tools/bench.py`)
  additionally require `opencv-python` (`pip install opencv-python`) and a
  connected USB webcam.

`tools/mic_capture.py artifacts/recordings/test.wav --record 8` records and
downloads a stereo diagnostic WAV, splits mono channels, and writes level/clipping
metrics. `tools/profile_resources.py` and `tools/check_microphone.py` run
automated concurrency and bounds checks while restoring saved volume afterward;
run them while the unit is idle. Interactive USB clip testing is also available
in the browser at `/bench` (see [HEADEND_TEST_UI.md](HEADEND_TEST_UI.md)).

## Canvas drawing behavior

Coordinates span `0..359`, with `X` increasing right and `Y` increasing down at
`rotation 0`. Width, height and radius span `1..360`. Primitives clip cleanly to
the 360×360 framebuffer; the circular LCD bezel masks the corners. `text` renders
printable ASCII characters at integer scales `1..6`.

Canvas commands pause home-screen animation and draw over the current buffer;
start with `fill` for a clean backdrop and run `menu home` to resume the live sky
panel. `rotation` and `mirror` update the panel's MADCTL orientation axes (`mirror 1 0`
flips horizontally and `mirror 0 1` flips vertically at `rotation 0`).

```powershell
python tools/control.py fill 080C14
python tools/control.py circle 180 180 70 00BBDC 1
python tools/control.py text 105 65 3 FFFFFF HELLO
python tools/control.py rotation 0
python tools/control.py mirror 0 0
python tools/control.py menu home
```

## Firmware module map

- `firmware/main/main.c`: hardware bring-up, USB serial command parser, panel orientation and QSPI DMA transfers.
- `firmware/main/space.c`: illustrated day/night sky, weather particle animations, green/blue activity mist and LLM availability edge ring.
- `firmware/main/graphics.c`: clipped RGB565 primitives and calibration patterns.
- `firmware/main/ui.c`, `touch.c`, `preferences.c`: touch controls, Settings/Wi-Fi/Audio/Voice menus and NVS persistence.
- `firmware/main/audio.c`, `mic.c`: shared 16 kHz 32-bit full-duplex I2S bus for ES8311 speaker output and ES7210 stereo capture.
- `firmware/main/wake.cpp`, `preroll.c`, `headend.c`: on-device microWakeWord inference, two-second rolling history + silence endpoint, and HTTP polling/streaming/paging.
- `firmware/main/metrics.c`: FreeRTOS runtime CPU, stack and heap telemetry.

## Automated bench checks

```powershell
python tools/ui_check.py
python tools/snapshot.py artifacts/screen.png
python tools/bench.py colors
python tools/bench.py brightness
python tools/bench.py mapping
```

`tools/snapshot.py` captures exact pixel-accurate framebuffers over USB without a
camera and is used by `tools/ui_check.py`, `tools/check_space_panel.py`,
`tools/check_daylight_panel.py`, and `tools/check_weather_panel.py`. Camera
scripts (`tools/bench.py`) are optional physical-backlight checks for setups with
a webcam and `opencv-python`.
