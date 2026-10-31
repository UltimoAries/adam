# ADAM

**Automation Display and Mapping** is a local-first voice assistant, intercom,
and smart-home control system built around a round-touchscreen ESP32-S3
speaker/microphone unit and a Python head-end.

## Features

- **Illustrated round-display home screen (Firmware v27):** Live local clock and
  cached weather over an animated day/night sky with rain, drizzle, and snow
  particles, green listening mist, blue playback mist, touch settings menus, and
  a glowing idle edge ring showing GPU vs. local LLM readiness.
- **On-device wake word & low-latency capture:** Embedded experimental
  **Hey Adam** microWakeWord model, quieter 20 dB trigger chime, rolling
  two-second pre-roll buffer, and silence-ended Wi-Fi audio streaming.
- **Local speech pipeline with optional GPU offload:** Local CPU INT8
  `faster-whisper` transcription and persistent Piper ONNX speech synthesis, plus
  optional LAN offload to a GPU host (Speaches Whisper `large-v3-turbo` and
  Ollama `qwen3.5:9b`) with automatic per-request local fallback.
- **Home Assistant integration (`/home-assistant`):** Import entities, assign
  persistent spoken names and aliases, use capability-based web controls, and
  run spoken light/switch commands with spoken confirmations.
- **Weather & information answers (`/queries`):** Spoken current weather and
  forecasts via Open-Meteo in your chosen Piper voice, with per-unit or default
  home locations and aliases.
- **Multi-unit paging & dashboard (`/`):** Pair speaker units over USB, send
  typed or browser-recorded voice announcements with five-minute expiry and
  playback acknowledgements, and explore the **Monitor Map** 3D floor-plan preview.
- **Durable training archive (`/training`):** Optional bounded archive of
  original device recordings, transcripts, wake-phrase boundary markers, and
  human review labels for future wake-word/speech tuning.

> **Status notes:** The embedded `Hey Adam` wake model (`hey_adam-20261030-c60f0456`)
> loads and handles live commands reliably; formal held-out false-accept /
> false-reject benchmarking remains in progress (see [docs/HEY_ADAM_PILOT.md](docs/HEY_ADAM_PILOT.md)).
> Custom 3D house-model import and map device placement are planned future work.
> All speech inference runs on your LAN; Open-Meteo weather lookups and one-time
> dependency/model downloads require internet access.

---

## Hardware Target & Setup Notes

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
.\tools\idf.ps1 -DIDF_TARGET=esp32s3 build
# Replace COM6 with your unit's serial port:
.\tools\idf.ps1 -p COM6 flash
```

### Linux / macOS

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
| `firmware/` | ESP-IDF v5.5.1 C/C++ firmware for the Waveshare 1.85C-BOX V2 |
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
- [Information queries & Open-Meteo weather](docs/INFORMATION_QUERIES.md)
- [Piper voice package format & library](docs/VOICE_PACKAGES.md)
- [Response latency, pre-roll & silence endpointing](docs/RESPONSE_LATENCY.md)
- [Training archive & human review workflow](docs/TRAINING_ARCHIVE.md)

### Firmware, Hardware & Wake Word
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
- [Hardware bring-up & development history](docs/DEVELOPMENT_HISTORY.md)
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
