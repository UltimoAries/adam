# ADAM

**Automation Display and Mapping** is a local-first voice assistant, paging,
and smart-home control system built around a round-touchscreen ESP32-S3
speaker/microphone unit and a Python head-end.

## Features

- **Wake-free follow-up conversation:** Updated speakers listen for five to twelve seconds
  after answering. Weather follow-ups retain the day/city, and general model
  questions retain recent exchanges. Say "that's all" or tap the center to finish.
  Wake-word interruption during playback remains experimental and unreliable
  in the Office pilot; investigation is paused with results and next steps documented.
  See [Follow-up conversation](docs/FOLLOWUP_CONVERSATION.md).
- **Live status and short follow-ups:** Ask which lights are on, which devices
  or speakers are unavailable, or which brain/model ADAM uses. Status answers
  read actual service data; “Which one?” and “Is it back online?” retain brief,
  isolated context. See [Status questions](docs/STATUS_QUESTIONS.md).

- **Type to ADAM:** A phone-friendly command box on the signed-in
  home page runs the same commands as voice, without a wake word. Replies appear
  on the page; optionally speak them through a selected unit. Supports Home
  Assistant controls, timers, information queries, and speaker-to-speaker calls.
  See [Text commands](docs/TEXT_COMMANDS.md).

- **Experimental live intercom:** Opt-in automatic drop-in between speakers,
  named voice calls, simultaneous two-way audio with on-device echo cancellation,
  and voice/touch/dashboard hangup. Normal calls are not recorded. Both units need
  v31 firmware. The mixed 1.85/1.46 pilot has usable audio in only one direction;
  validation with two 1.85 units is still pending. See
  [Intercom](docs/INTERCOM.md).

- **Illustrated round-display home screen & countdown timers:**
  Live local clock and cached weather over an animated day/night sky with rain,
  drizzle, and snow particles, green listening mist, blue playback mist, stacked
  on-screen countdown timer bars (2-tap `[X]` → `[OK?]` cancel while running,
  1-tap anywhere to silence when ringing), touch settings menus, and a glowing
  idle edge ring showing GPU vs. local LLM readiness.
- **On-device wake word & low-latency capture:** Embedded experimental
  **Hey Adam** microWakeWord model, quieter 20 dB trigger chime, rolling
  two-second pre-roll buffer, and silence-ended Wi-Fi audio streaming with a
  configurable 750 ms quiet timeout and one-second minimum live capture.
- **Local speech pipeline with optional GPU offload:** Local CPU INT8
  `faster-whisper` transcription and persistent Piper ONNX speech synthesis, plus
  optional LAN offload to a GPU host (Speaches Whisper `large-v3-turbo` and
  Ollama `qwen3.5:9b`). One GPU/Pi preference and installed-model dropdowns on
  `/models` configure both routes, with a shared failure cooldown and automatic
  local fallback. Piper speaking voice stays on the Pi.
- **Named countdown timers (`/` & voice):** Set, extend, check, and cancel up to
  8 named countdown timers per speaker unit by voice or from the dashboard, with
  local monotonic countdowns that survive brief Wi-Fi drops, originating-speaker
  display/ringing, and fleet-wide status/cancel by name.
- **Home Assistant integration (`/home-assistant`):** Import controllable devices
  and curated household sensors (temperature, humidity, air quality, power/energy,
  doors/windows, locks, motion, leak sensors), assign persistent spoken names and
  aliases, ask indoor room sensor and bedtime summary questions (*"What's the
  temperature in the bedroom?"*, *"Are any doors or windows open?"*), and run
  spoken commands with confirmations, including up to four actions in one
  utterance. "Turn on the living room light and set it to 30%" sends one
  brightness command. "Can we turn those off?" can control up to four lights
  just reported on; "shut the garage door" means "close the garage door".
  Targets, permissions, and capabilities are checked before execution. See
  [Home Assistant commands](docs/HOME_ASSISTANT.md).
  Independent thermostat commands such as "set the heat to 65" and "set the
  cooling to 71" change only the named setpoint. Automatic-mode requests must
  specify heating or cooling, and gap conflicts leave both limits unchanged.
- **Model-assisted home request preview (`/models`):** Inspect a proposed
  device/action/value using the configured local or LAN model. This preview
  never executes actions or reads live device states; live commands continue
  through deterministic handlers.
- **Time, world clock, calendar & weather answers (`/queries`):** Spoken local
  time and date per speaker timezone, world clock lookups (*"What time is it in
  Tokyo?"*), calendar date math (*"How many days until Christmas?"*), and
  Open-Meteo current weather and forecasts in your chosen Piper voice.
- **Multi-unit paging & dashboard (`/`):** Pair speaker units over USB, send
  typed or browser-recorded voice announcements with five-minute expiry and
  playback acknowledgements, and open **Menu → Map** for movable light markers,
  live status, and marker toggles on a private PNG or the fallback reference plan.
  CPU, physical memory, and data-drive usage remain on Home, refreshing
  every five seconds while the signed-in dashboard is visible.
  Assign imported lights to rooms by dropdown or drag-and-drop. Saved placements
  show live on/off status, warm room illumination, and unavailable readings.
  Private maps include front/back outdoor placement bands. Edit mode shows the
  placement inspector; viewing hides it and expands the map. Custom PNG and
  `layout.json` setup is currently manual; image upload and guided layout creation
  still need to be finished.
  See [Room lights on the map](docs/MAP_LIGHTS.md).
- **Durable training archive (`/training`):** Optional bounded archive of
  original device recordings, transcripts, wake-phrase boundary markers, and
  human review labels for future wake-word/speech tuning.
- **Cameras & gesture actions (`/cameras`):** Save private RTSP connections,
  preview one active stream, and map seven sustained hand gestures to imported
  Home Assistant devices and supported actions. Optional event snapshots retain
  the triggering image and debugging metadata for up to seven days within the
  latest 200 events. The Pi camera worker pauses during voice processing.

> **Status notes:** The embedded `Hey Adam` wake model (`hey_adam-20261030-c60f0456`)
> loads and handles live commands reliably; formal held-out false-accept /
> false-reject benchmarking remains in progress (see [docs/HEY_ADAM_PILOT.md](docs/HEY_ADAM_PILOT.md)).
> Custom 3D house-model import is planned; light placement uses the current sample rooms.
> Paging remains message-based. Live intercom is experimental and requires v31
> firmware on both speakers; SIP is not implemented.
> All speech inference runs on your LAN; Open-Meteo weather lookups and one-time
> dependency/model downloads require internet access.

---

## Hardware Target & Setup Notes

Current source firmware targets are **`v33-185`** and **`v33-146`**. Check the
dashboard or `status` over USB for the version actually installed on each unit;
updating the head-end does not flash speakers.

- **Target unit:** **Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2** (`ESP32-S3`,
  16 MB flash, 8 MB octal PSRAM, ST77916 360×360 QSPI round LCD, CST816 touch,
  ES7210 dual-mic ADC, ES8311 DAC/amp).
  - Verify your enclosure/PCB is the **V2** hardware revision (`E-S-T-L 1.85C-Box`
    V2); V1 uses a different display initialization sequence.
  - **Physical orientation:** Orient the unit with the **USB-C port pointing
    down**. On the rear/side enclosure buttons, the **upper button is `BOOT`
    (`GPIO0`)** and the **lower button is hardware `RESET`**.
  - **USB interface:** The single USB-C port uses the ESP32-S3 native
    `USB-Serial/JTAG` controller (`COMx` on Windows, `/dev/ttyACM0` on Linux) for
    flashing, pairing, and diagnostics. If first-time flashing requires download
    mode, hold the upper `BOOT` button, tap the lower `RESET` button, then release `BOOT`.
- **Additional target:** The non-suffixed **Waveshare ESP32-S3-Touch-LCD-1.46**
  has a 412×412 SPD2010 screen/touch controller, PCM5101 output, and one digital
  microphone. It retains the same logical 360×360 UI and voice features, with
  separate firmware and board-specific audio gain. See [board support and
  limitations](docs/BOARD_146.md); do not cross-flash the two targets.
- **Network requirements:**
  - The ESP32-S3 supports **2.4 GHz WPA2/WPA3-Personal Wi-Fi** (printable ASCII
    SSID and password).
  - Speaker units validate the head-end address strictly as
    **`http://<RFC1918-IPv4>:8765`** (for example `http://192.168.1.50:8765`).
    Hostnames (`.local`), HTTPS, and non-`8765` ports are rejected by firmware;
    run on a trusted private LAN and reserve a static DHCP lease for your head-end.

---

## Quick Start: Run the Head-End

ADAM's head-end can run directly on Windows/Linux for development or permanently
on a headless **Raspberry Pi 5** in Docker (optionally paired with a **Windows
GPU model host**):

- **Raspberry Pi 5 / Linux Docker deployment:** see [deploy/pi5/README.md](deploy/pi5/README.md)
- **Optional Windows GPU model host (Ollama + Speaches):** see [deploy/model-host/README.md](deploy/model-host/README.md)

### Local Python setup (Windows PowerShell)

Require **Python 3.12**. From the repository root:

```powershell
py -3.12 -m venv headend/.venv
.\headend\.venv\Scripts\python.exe -m pip install -r headend/requirements-lock.txt
.\headend\.venv\Scripts\python.exe -m tools.fetch_stt_model
.\headend\.venv\Scripts\python.exe -m tools.fetch_voice_model
.\tools\start_headend.ps1 -Lan -OpenBrowser
```

*(Stop the background server anytime with `.\tools\stop_headend.ps1`. Omit `-Lan`
for localhost-only testing.)*

### Local Python setup (Linux / macOS)

```sh
python3.12 -m venv headend/.venv
./headend/.venv/bin/python -m pip install -r headend/requirements-lock.txt
./headend/.venv/bin/python -m tools.fetch_stt_model
./headend/.venv/bin/python -m tools.fetch_voice_model
./headend/.venv/bin/python -m headend.server --lan
```

### Initial sign-in & speaker pairing

1. Open <http://127.0.0.1:8765> on the head-end machine (or via an SSH tunnel
   `ssh -L 8765:127.0.0.1:8765 <user>@<headend-ip>`) and create the local
   administrator account (**12–128 character password**). First-account creation
   is restricted to `127.0.0.1`.
2. On the speaker's touch screen, open **Settings → Wi-Fi** and join your 2.4 GHz
   network.
3. Connect the speaker via USB to the head-end host, expand **Add a USB-connected
   unit** on the dashboard, enter your head-end's LAN URL
   (`http://<headend-lan-ip>:8765`), and click **Pair over USB**.
4. Other devices on your LAN can access the dashboard at
   `http://<headend-lan-ip>:8765` (note: browser microphone recording requires
   `127.0.0.1` or HTTPS; over plain LAN HTTP you can send typed announcements or
   upload WAV files).

---

## Build & Flash the Firmware

Install **ESP-IDF 5.5.1** for `esp32s3`. The repository already includes the
embedded wake-word model (`firmware/main/wake_model.tflite`) and generated
listening chime (`firmware/main/listen_cue.pcm`).

### Windows (PowerShell)

`tools\idf.ps1` uses an active ESP-IDF environment (`$env:IDF_PATH`) or falls
back to standard Windows installations (`%USERPROFILE%\.espressif` or
`C:\Espressif\esp-idf`):

```powershell
# Original 1.85C BOX V2 (v33-185):
.\tools\idf.ps1 -DIDF_TARGET=esp32s3 build
# Replace COM6 with your unit's serial port:
.\tools\idf.ps1 -p COM6 flash

# Non-suffixed 1.46 only (v33-146), using its own USB port:
.\tools\idf146.ps1 build
.\tools\idf146.ps1 -p <new-board-port> flash
```

### Linux / macOS

For the original 1.85C BOX V2:

```sh
. "$IDF_PATH/export.sh"
idf.py -C firmware -DIDF_TARGET=esp32s3 build
idf.py -C firmware -p /dev/ttyACM0 flash
```

See [docs/TOUCH_UI.md](docs/TOUCH_UI.md) for on-device menus and
[docs/DISPLAY_CONTROLS.md](docs/DISPLAY_CONTROLS.md) for the USB serial command
interface.

---

## Project Layout

| Path | Purpose |
| --- | --- |
| `headend/` | FastAPI server, audio session pipeline, speech/LLM routing, web UI, and unit tests |
| `firmware/` | ESP-IDF v5.5.1 C/C++ firmware for the Waveshare 1.85C-BOX V2 and non-suffixed 1.46 |
| `deploy/pi5/` | Headless Raspberry Pi 5 / ARM64 Docker Compose stack & setup guide |
| `deploy/model-host/` | Optional Windows GPU Ollama + Speaches setup & desktop controller |
| `tools/` | Model provisioning, migration, firmware helpers, and hardware diagnostics ([tools/README.md](tools/README.md)) |
| `training/hey_adam/` | Custom microWakeWord training configuration, pilot reports, and CPU/GPU benchmarks |
| `docs/` | Architecture, protocol, feature, and hardware bring-up documentation |
| `vendor/` | Pinned upstream drivers, `micro_wake_word` runner, schematics, and licenses |
| `artifacts/` | Git-ignored local diagnostic captures and reports ([artifacts/README.md](artifacts/README.md)) |
| `backups/` | Git-ignored local runtime/migration backups |

---

## Documentation Index

### Deployment & Operations
- [Headless Raspberry Pi 5 Docker deployment & migration](deploy/pi5/README.md)
- [Optional Windows GPU model host & desktop controller](deploy/model-host/README.md)
- [Head-end developer overview & CLI usage](headend/README.md)
- [Tools & diagnostic scripts reference](tools/README.md)
- [Workspace layout & local data hygiene](docs/WORKSPACE.md)

### Architecture & Features
- [Head-end architecture & device HTTP protocol](docs/HEADEND_NETWORK.md)
- [Conversation routing & LLM fallback](docs/CONVERSATION.md)
- [Home Assistant inventory & spoken controls](docs/HOME_ASSISTANT.md)
- [RTSP cameras, gesture actions & event snapshots](docs/CAMERAS.md)
- [Information queries & Open-Meteo weather](docs/INFORMATION_QUERIES.md)
- [Piper voice package format & library](docs/VOICE_PACKAGES.md)
- [Response latency, pre-roll & silence endpointing](docs/RESPONSE_LATENCY.md)
- [Current Pi voice latency tuning, GPU fallback & timing measurements](docs/VOICE_LATENCY.md)
- [Training archive & human review workflow](docs/TRAINING_ARCHIVE.md)

### Firmware, Hardware & Wake Word
- [Waveshare 1.46-inch board target, build, and feature differences](docs/BOARD_146.md)
- [Illustrated sky home screen & status ring](docs/SPACE_PANEL.md)
- [Touch UI & Wi-Fi configuration](docs/TOUCH_UI.md)
- [USB serial display, audio & diagnostic controls](docs/DISPLAY_CONTROLS.md)
- [Listening chime design & synthesis](docs/LISTENING_CUE.md)
- [Custom "Hey Adam" wake-word generation guide](docs/WAKEWORD_MODEL_GENERATION.md)
- [Hey Adam pilot model validation & rollback](docs/HEY_ADAM_PILOT.md)
- [Wake-word training benchmarks](training/hey_adam/README.md)

### Historical Bring-Up & Design Notes
- [Audio pipeline & board memory capacity notes](docs/AUDIO_AND_CAPACITY.md)
- [Diagnostic audio bench (`/bench`)](docs/HEADEND_TEST_UI.md)
- [Development history & change log](docs/DEVELOPMENT_HISTORY.md)
- [Initial architecture & roadmap notes](docs/HEADEND_PLAN.md)

---

## Validation

Run the offline synthetic test suites (no physical hardware or network access required):

```powershell
.\headend\.venv\Scripts\python.exe -m unittest discover -s headend -t . -q
.\headend\.venv\Scripts\python.exe tools/test_gpu_control.py
```

End-to-end browser verification (`tools/check_fleet_browser.py`) uses a headless
local Chrome instance plus `requests` and `websockets` against an isolated
fixture server on port `8766`; see [tools/README.md](tools/README.md) for
prerequisites before running hardware or browser checks.

---

## Licensing

Original ADAM code is licensed under the **GNU General Public License v3.0 or
later** (`GPL-3.0-or-later`); see [LICENSE](LICENSE).

Third-party components in `vendor/`, `headend/web/dist/vendor/`, and `training/`
retain their respective upstream licenses (Apache-2.0, MIT, BSD-3-Clause, and
GPL-3.0). Note that the pre-trained **Hey Adam** wake-word `.tflite` weights
embedded in `firmware/main/` and `training/hey_adam/export/` were trained using
upstream datasets that include **noncommercial restrictions** and are provided
for noncommercial personal/research use only; see
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for complete provenance and
commercial replacement instructions.
