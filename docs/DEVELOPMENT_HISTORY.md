# ADAM — Automation Display and Mapping

The local head-end at <http://127.0.0.1:8765> now starts with **Monitor Map**:
an interactive sample house with zoom, pan, orbit/tilt, top view and reset.
Drag to orbit, right-drag to pan, and scroll to zoom; the toolbar also offers
a dedicated Pan mode. Touch supports one-finger orbit and two-finger pan/zoom.
Focus the canvas for arrow-key panning, Shift + arrow orbit/tilt, +/- zoom,
T for top view and R for reset.

This is a placeholder layout, not a scan of your home. Real house-model import,
room/device placement and map-based device controls are future work. The viewer
renders on demand in the browser and does not use ESP32 resources. Three.js
0.180.0 is bundled locally under `headend/web/dist/vendor/three`, with its MIT
license and source URLs/SHA-256 checksums in `provenance.json`; no CDN is required.
The sample geometry and viewer are in `headend/web/dist/monitor-map.js`.
WebGL 2 is needed for the map; the rest of the dashboard works without it.

Target: Waveshare **ESP32-S3-Touch-LCD-1.85C-BOX V2**, labeled `V2` and
`E-S-T-L 1.85C-Box`. The connected ESP32-S3 reports 16 MB flash and 8 MB embedded
PSRAM, via native USB Serial/JTAG on COM6.

Head-end v0.6 now answers **“Hey Jarvis, what's the weather in Shelton,
Washington?”** using your selected local voice. Configure weather, saved
locations and aliases at [Information & sources](http://127.0.0.1:8765/queries).
See [INFORMATION_QUERIES.md](INFORMATION_QUERIES.md) for supported questions,
provider details, privacy and how to add future handlers.

## Current firmware: touch menus, animated eye, speaker and microphone

Firmware v13 boots into a smaller animated blue eye with curved touch controls:
volume on the left, brightness on the right, and page arrows above and below.
Settings contains Wi-Fi configuration with automatic network discovery and a
Eye button. Settings also has an Audio page with a three-note chime,
10-second tone, Stop, and MIC TEST. The microphone page records an 8-second
stereo clip and replays it on the box while the eye animates. The volume rail controls speaker output;
zero mutes the amplifier. Brightness and volume persist across restarts;
defaults are 50%. No sound plays automatically at startup. The Voice page adds
Wi-Fi listening, a local wake toggle, and paging from the laptop. The current
development wake phrase is **Hey Jarvis**; **Hey Adam** still needs its trained
model. On a trigger, the eye becomes an ear and capture starts immediately,
with two seconds of recent audio prepended. Wake questions end after 1.2 seconds
of quiet, with a 13-second post-trigger cap. The diagnostic Listen/Talk action
still records eight seconds. The head-end keeps speech models warm for faster
replies; see [response latency and validation](RESPONSE_LATENCY.md).
An embedded **120 ms chime** signals the trigger while capture continues, so
there is no need to pause before the request. The operator confirmed that
pre-roll retains the first word in both recording and transcript.
See [cue details and replacement workflow](LISTENING_CUE.md).
See [TOUCH_UI.md](TOUCH_UI.md) for operation, [DISPLAY_CONTROLS.md](DISPLAY_CONTROLS.md)
for USB commands, and [AUDIO_AND_CAPACITY.md](AUDIO_AND_CAPACITY.md) for audio
wiring, measured resource headroom, and current limitations.

The running [head-end design and protocol](HEADEND_NETWORK.md) supports named
units, account login, Wi-Fi audio streaming, local English transcription,
typed announcements and recorded voice pages with five-minute expiry. The
operator confirmed streaming, both paging methods and stock on-device wake.
The head-end also has a **Voices** library at `http://127.0.0.1:8765/voices`,
with a local English starter model and import/preview/default selection for
custom Piper ONNX voices. See [voice model format and creation guide](VOICE_PACKAGES.md).
The next voice milestone is a trained **Hey Adam** model on this same runner;
Home Assistant/device integration remains in [HEADEND_PLAN.md](HEADEND_PLAN.md).

The **local audio test server is now implemented** at `http://127.0.0.1:8765`.
Run `./tools/start_headend.ps1 -Lan -OpenBrowser`. Its diagnostic bench at `/bench` uploads WAVs, records/fetches
the box's clip over USB, plays individual channels in the browser, and runs
local English transcription after a clip finishes. Firmware v8 fixes the
microphone's repeating zero-sample gaps; the user confirmed clear speech and a
correct transcript from a new recording. See
microphone validation (`artifacts/validation-microphone-v8.md`, local only) and
[headend/README.md](../headend/README.md).

The Wi-Fi connection reboot loop is fixed: the event task has adequate stack
space, and startup connection, scanning while connected, and explicit reconnect
passed. See Wi-Fi fix validation (`artifacts/validation-wifi-reboot-fix.md`, local only).

The eye runs at about 15–16 fps with local wake inference active (about 19 fps
before wake inference), with a 30 fps target, using 240 MHz CPU, 80 MHz
octal PSRAM and 40 MHz QSPI. The user confirmed its appearance, the default
coordinate grid, clockwise 90-degree rotation, and horizontal mirroring.

## First milestone

**Completed on October 28, 2026:** the device boots directly into a solid red
screen and accepts commands over USB. The operator confirmed that red looks
good and uniform in person. That first milestone left red at 80% brightness;
the current firmware instead boots into the eye at the saved brightness.

Validation: ESP-IDF v5.5.1 build passed; esptool verified the hashes of all
three flashed images; `red` returned `OK fill #FF0000 rgb565=0xF800`; `status`
confirmed the 360 x 360 configuration and panel ID `00 02 7F 7F`, selecting the
manufacturer's alternate initialization sequence. The webcam showed the lit
display but did not reliably capture its color: normal exposure clipped to
white, and the manual -10 test was too dark. Human inspection is the evidence
for color and uniformity. No other colors, brightness levels or touch controls
had yet been visually validated at that milestone. See `artifacts/validation.md`
for the historical record and `artifacts/validation-v2.md` for subsequent checks.

```powershell
# Installed ESP-IDF v5.5.1 at C:\Espressif
.\tools\idf.ps1 -DIDF_TARGET=esp32s3 build
.\tools\idf.ps1 -p COM6 flash

python tools/control.py status
python tools/control.py red
python tools/control.py fill 00FF00
python tools/control.py brightness 50
python tools/control.py red

# Python 3.12 has OpenCV installed on this laptop. Requires camera access.
py -3.12 tools/capture.py artifacts/screen.jpg
```

The serial port must be free of other monitors during flashing or commands.
Opening a serial connection can reset some ESP32 firmware; the helper disables
DTR/RTS before opening. Firmware v8 restores the saved brightness.

## Confirmed manufacturer wiring

| Function | Connection |
| --- | --- |
| LCD | ST77916, 360 x 360, QSPI |
| Clock / chip select | GPIO 40 / GPIO 21 |
| Data 0 / 1 / 2 / 3 | GPIO 46 / 45 / 42 / 41 |
| Backlight | GPIO 5, active high, 5 kHz PWM |
| I2C SDA / SCL | GPIO 11 / GPIO 10 |
| LCD reset | TCA9554 at 0x20, P1 (vendor name EXIO2), active low |
| Touch | CST816 family at 0x15; detected chip ID B5; interrupt GPIO 4 |
| Touch reset | TCA9554 P0 (vendor EXIO1), active low |
| Audio codec | ES8311 at I2C 0x18; detected ID 0x8311 |
| Audio MCLK / BCLK / LRCK / DOUT | GPIO 2 / 48 / 38 / 47 |
| Microphone | ES7210 at 0x40, input GPIO 39; ADC1/ADC2 stereo |
| Amplifier enable | GPIO 15; enabled during playback with volume above zero |

The firmware changes P1 and P0 of the expander for LCD and touch reset, preserving
the other pins. It reads the LCD ID at 3 MHz,
selects the corresponding manufacturer initialization table, and draws at
40 MHz. Color data is RGB565 with its high byte first. Drawing uses 12-row DMA
buffers and waits for transfer completion before reuse. Touch is polled every
10 ms; Wi-Fi starts when scanning, connecting, or restoring saved credentials.
Audio input/output share 16 kHz, 32-bit stereo I2S slots, with separate workers
on core 1. Idle playback feeds silence to keep microphone clocks running.
Captured clips are converted to signed 16-bit stereo PCM for USB and the web UI.
SD card and RTC are not initialized. The framebuffer and eye textures use PSRAM;
the DMA staging buffer uses internal RAM.

## Control-mapping plan

1. **Red fill:** verify the complete visible circle lights red using both a USB
   acknowledgement and a webcam photo. Record panel ID and startup logs.
2. **Color and brightness:** check green, blue, white and black; compare byte
   order and RGB/BGR behavior. Verify 0%, 25%, 50%, 80% and 100% brightness.
   Completed; automatic eye mode is the current resting state.
3. **Coordinates and orientation:** add a marked grid and labeled axes; test
   corners, center, clipping at the round boundary, rotations and mirroring.
4. **Drawing controls:** add bounded rectangle, line, circle, text and bitmap
   commands; measure redraw time and watch for tearing or incomplete transfers.
5. **Animated eye:** implemented with eased gaze, blinking and adjustable pupil.
   Touch menus were added in v3 after the user's subsequent request.
6. **Lifecycle:** test display enable, sleep/wake, reset and unplug/replug;
   record which settings persist. Keep repeatable commands and before/after
   captures for each verified operation.

Only request physical checks when the webcam cannot resolve the result or a
test needs button input. Audio and other board peripherals can be mapped
after screen controls are established.

## Sources

- [Waveshare board and V2 identification](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C)
- [Waveshare resource links](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C/Resources-And-Documents)
- [Manufacturer source pinned to 8ead4a96](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85C/tree/8ead4a96bf3a278fc4ebd8ef4768657e17fa2880)

The selected manufacturer files are preserved under `vendor/` with their
Apache-2.0 license. Their Git blob hashes were checked against the repository
tree in `vendor/waveshare-tree.json`. The alternate panel initialization table
in `firmware/main/panel_init_v2.h` is extracted unchanged from `ST77916.c`.
Two local driver fixes now differ from the original Git blob: axis swap uses
the QSPI command-prefix helper, and pixel-transfer errors propagate to callers.

An older local project in `C:\Work\smart_speaker_v2` uses a different SPI pin
map, a direct GPIO reset, and an address endpoint of 383 for a 360-pixel panel.
Its source is not used by this probe. The running firmware initially printed
`Display diagnostic: backlight + reset + SPI heartbeat`; the baseline webcam
capture showed a dark display. The user authorized replacing existing firmware.
