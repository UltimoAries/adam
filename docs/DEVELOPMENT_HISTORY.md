# Development history and change log

## November 4, 2026 — Outdoor light placement and expanded map view

Private maps now have a bordered outdoor area with full-width Front outside and
Back outside placement bands that wrap around the sides. The expanded viewport
preserves existing interior coordinates and placement identity. Outdoor markers
use the same authenticated light inventory, status, and bounded action routes.

Outside Edit mode, the inspector is hidden and the map expands without stretching
the artwork's proportions. Room clicks do nothing; only individual markers send
light commands. Command feedback remains visible above the map. Edge labels stay
inside the border.

All 18 map/API tests passed. Browser checks verified front/back drag placement,
reload persistence, unchanged indoor alignment, panel visibility and expansion,
marker controls, inactive room clicks, mobile layout, and session expiry.
Deployed with rollback backups; 81 files matched, health/model readiness passed,
and live desktop/mobile checks passed without JavaScript errors. Verification
sent no device commands and changed no placements. Camera and Ollama containers
were unchanged.

## November 4, 2026 — Map artwork cleanup and stronger lighting contrast

Removed the title/area panel and garage square-footage text from the private
image while preserving its dimensions and marker alignment. Increased warm
illumination and on-marker glow, deepened confirmed-off room shading, and added
gentle shading to rooms without assigned lights. Dimmer intensity still follows
reported brightness, and unknown states retain their separate presentation.

Private-map browser checks passed marker switching, brightness, image alignment,
authentication, and mobile layout. Deployed to the Pi with rollback backups;
all 81 runtime/private-map files matched and health/model readiness passed.
Live desktop/mobile checks found no JavaScript errors. Verification changed no
placements and sent no device commands; camera and Ollama were unchanged.

## November 4, 2026 — Edit mode, marker switching, and room illumination

The Map toolbar now explicitly toggles Edit mode. Editing enables placement,
movement, and removal while disabling device controls; marker clicks only select.
With editing off, clicking or keyboard-activating a marker sends a bounded on/off
command. Pending commands wait for refreshed status before further input is
enabled. Unavailable lights cannot be toggled.

Room polygons now darken confirmed-off rooms and add a brightness-sensitive warm
glow around on-light markers. Rooms with unknown status lose stale illumination,
and unassigned rooms retain the original artwork. The private floor-plan image
and saved marker positions are unchanged.

Validation: all 17 map/API tests and isolated browser checks passed, including
edit-only movement, normal marker toggles, pending-command handling, and room
illumination at different brightness levels. Deployed to the Pi with rollback
backups; all 81 runtime/private-map files verified and health/model readiness
passed. Live desktop/mobile checks verified the edit toggle, room overlays, and
nine-page navigation without JavaScript errors. Existing placements were
preserved; live verification sent no light commands. Camera and Ollama containers
were unchanged.

## November 4, 2026 — Private floor-plan image

Added optional private PNG artwork and polygon room hit areas for the Map page.
The supplied image was edited with built-in image generation to remove the
compass and scale and make the exterior background transparent. The image,
geometry, and complete edit prompt remain in ignored `headend/data/maps/`.
The source contains only the reusable loader and renderer; image access requires
authentication. Image/layout changes preserve earlier marker sets separately.

Validation: 17 map/API tests passed, including polygon cutouts, private-image
authentication, layout isolation, and invalid-configuration fallback. Isolated
desktop/mobile previews verified the supplied image's aspect ratio, marker
placement, and removal of the image from the page on session expiry. No real
lights were operated and no Pi deployment was performed.

November 4 Pi testing deployment: the updated head-end and private map were
deployed with rollback image, source, database snapshot, and previous map files
retained. All 81 deployed head-end/private-map files matched their manifests;
retired map assets were absent. Health and recognition were ready, and the camera
and Ollama containers were unchanged. Authenticated live desktop/mobile checks
loaded the ten-room image map, found four imported lights with status available,
verified navigation on nine pages, and observed no JavaScript errors. Private
artwork rejected unauthenticated access. No light actions or marker changes were
made during deployment verification. LAN addresses and deployment details remain
in ignored local artifacts.

## November 4, 2026 — Dedicated floor plan and shared navigation

Replaced the four-room Three.js map on Home with a dedicated `/map` page based
on the eight-space map-tap-control reference layout. SVG rooms and movable HTML
light markers support mouse, touch, keyboard arrows, and room-selector placement.
Markers show live on/off/unavailable state; the inspector exposes validated
on/off, brightness, color temperature, and color controls when supported.
Sliders require Apply, and placement never sends a device command.

Marker coordinates persist in SQLite by HA origin and entity ID. Old assignments
migrate into corresponding rooms once. Stale settings versions cannot retarget
light actions after an HA connection change. Commands reuse the existing bounded
Home Assistant service and its no-POST-retry behavior. Unknown/disconnected
lights lose actionable state; session expiry clears the map.

All nine pages now share one Menu and current-page label. Home retains resource
readings, typed commands, speakers, timers, intercom, and paging. Removed the old
map scripts/styles and unused Three.js assets; the historical prototype below
describes those retired files. No firmware changes or new runtime dependencies.

All 283 head-end tests passed. The final 15 map/API tests and isolated Chrome
checks passed migration, placement,
mouse/touch/keyboard movement, persistence, unavailable-state recovery, synthetic
light commands, mobile layout, session expiry, and navigation on all nine pages.
Browser checks observed zero JavaScript errors and sent two commands to mocked
lights only. Changes are prepared locally; no live deployment was performed.

## Feature branch trial — Room light placement on the Monitor Map

The `feature/map-room-lights` branch adds persistent light assignments to the
sample house's four rooms, through dropdowns, room selection, or desktop drag
and drop. Live status drives amber room illumination and brightness, with
unavailable readings distinguished from off. Multiple lights can share a room;
moving/removing a placement changes only map metadata. Status reads are cached
for five seconds and assignments are isolated by HA origin and stable entity
ID. See [Room lights on the map](MAP_LIGHTS.md).

Validation: 277 head-end tests passed, along with JavaScript syntax checks and
isolated desktop/mobile browser checks for placement, room picking, canvas drops,
illumination, unavailable status, persistence, and removal. No device actions
were sent during the isolated tests.

November 3 deployment trial: runtime commit `ec50284` was deployed to the Pi
with the previous source and container image retained for rollback. All 80
packaged head-end files matched the running image, and health/recognizer checks
passed. One explicitly authorized light was toggled off and back on; the live
browser removed/restored room illumination in about 3.9/3.8 seconds. Original
power and brightness were restored. Its assignment survived page reload and
a container restart. Ten map reads with five concurrent callers succeeded
(median 70.5 ms, maximum 188 ms), alongside resource and read-only status checks.

Expanded isolated browser checks passed native mouse drag-and-drop, connection
failure/recovery without stale illumination, assignment after WebGL loss, and
clearing map details on logout. No JavaScript errors were observed. The branch
is ready for review as a first light-only implementation on the four sample
rooms; custom room/model editing and other device types remain out of scope.
The deployment trial did not merge the feature into `main`.

## November 3, 2026 — Natural Home Assistant commands and model preview

- Expanded deterministic parsing for polite requests such as "Can we set that
  to 30%?", percentage follow-ups, and equivalent "backyard"/"back yard" names.
  Conflicting names and negated controls still require clarification.
- Added current-state questions such as "Did I leave the lights on?" and
  context-aware "Yeah, check" / "Check again". These refresh status without
  replaying actions or claiming knowledge of past states.
- Fixed three-device summaries to list all three names. "Can we turn those
  off?" now controls only the lights reported on in the preceding summary,
  within 90 seconds and the same conversation. The group is capped at four;
  unavailable targets, changed settings, and stale references block execution.
- Added unambiguous references within compound commands. "Turn on the living
  room light and set it to 30%" combines into one brightness request. Conflicting
  actions and unsupported values reject the complete plan before commands run.
- Added "shut" as a synonym for the supported `close` control, including polite
  and compound phrasing. "Is the garage door shut?" remains a status read.
- Added **Preview home requests** on Model services and the authenticated
  `/api/home-assistant/intent-preview` endpoint. The model proposes bounded,
  structured intents against enabled inventory; the preview never executes,
  reads live HA states, speaks, or changes dialogue context. Live voice/text
  dispatch remains deterministic. Preview requests have a separate 30-second
  deadline per provider; normal conversation timeouts are unchanged.
- Corrected dashboard labels for authorized follow-up recordings. Improved
  USB pairing errors to explain that USB access belongs to the head-end host
  and pairing requires already-flashed firmware.

Validation for this update: all 268 head-end tests, both firmware target builds,
JavaScript syntax checks for changed scripts, and the PowerShell wrapper argument
tests passed. Head-end changes were deployed to the Pi and readiness checked.
Regression tests use synthetic inventory and mocked services; deployment verification did
not operate household devices. See [Home Assistant](HOME_ASSISTANT.md),
[status questions](STATUS_QUESTIONS.md), and [conversation](CONVERSATION.md).

## November 2, 2026 — Follow-up listening, status, and v33 firmware

- Added short follow-up listening windows (5, 8, or 12 seconds), single-use
  same-speaker grants after delivered replies, speech/noise gating, bounded
  capture, end phrases, and touch cancellation. Manual Listen and diagnostic
  captures remain unable to dispatch commands.
- Added 90-second context isolated by speaker or authenticated browser session,
  tab, and selected speaker. Weather carries day/city, model chat keeps up to
  three recent exchanges, and contextual device corrections retain identity
  and existing capability/replay checks.
- Added read-only home, speaker, and model status with live rechecks and
  evidence-based availability explanations. Browser replies expose status
  details without inventing causes for outages.
- Added experimental reply interruption using local echo cancellation and
  explicit, bounded USB reply diagnostics. Normal follow-ups were confirmed
  working; interruption remained unreliable. Investigation is paused at the
  user's request, with evidence and next steps in
  [Follow-up conversation](FOLLOWUP_CONVERSATION.md).
- Added `v33-185` and `v33-146` source targets and fixed PowerShell build wrappers
  to forward an explicitly selected serial port. Updating the head-end does
  not flash installed speakers.

## Early hardware bring-up (v1–v13)

> [!NOTE]
> This document records the initial October 2026 hardware bring-up milestones (**Firmware v1–v13**), from probing the ST77916 QSPI round LCD and CST816 touch controller through early eye/ear visuals and stock `Hey Jarvis` testing. For current firmware behavior — including the illustrated sky/weather home screen, **Hey Adam** (`hey_adam-20261030-c60f0456`), Home Assistant integration, and Raspberry Pi / GPU LLM routing — see [`README.md`](../README.md), [`HEADEND_NETWORK.md`](HEADEND_NETWORK.md), [`SPACE_PANEL.md`](SPACE_PANEL.md), and [`TOUCH_UI.md`](TOUCH_UI.md).

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
