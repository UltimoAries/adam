# ADAM

**Automation Display and Mapping** is a local voice and monitoring system built
around an ESP32-S3 round-display speaker/microphone unit and a Python head-end.

## What works today

- Browser dashboard with **Monitor Map**, a sample 3D house with zoom, pan and tilt.
- On-device **Hey Jarvis** wake detection, two-second audio pre-roll, and silence-ended capture.
- Wi-Fi audio streaming and local English transcription using faster-whisper.
- Local Piper voices, typed announcements, and recorded pages to selected units.
- Five-minute page expiry, unit pairing, accounts, and delivery acknowledgements.
- Weather answers in the selected voice, with configurable locations and aliases.
- Animated eye/listening ear, touch menus, volume, brightness, and Wi-Fi setup.

The custom **Hey Adam** model is in preparation. Real house-model import,
map device placement, Home Assistant, and other smart-home adapters are future
work. Speech inference stays local; weather and initial dependency/model
provisioning require internet access.

## Start the head-end (Windows)

Use Python 3.12. From the repository root in PowerShell:

```powershell
py -3.12 -m venv headend/.venv
.\headend\.venv\Scripts\python.exe -m pip install -r headend/requirements-lock.txt
.\headend\.venv\Scripts\python.exe -m tools.fetch_stt_model
.\headend\.venv\Scripts\python.exe -m tools.fetch_voice_model
.\tools\start_headend.ps1 -Lan -OpenBrowser
```

The dashboard opens at <http://127.0.0.1:8765>. Create the local administrator
account, configure Wi-Fi on the device, then use **Add a USB-connected unit**
to pair it. Allow the head-end through Windows Firewall on your private LAN if
needed. Omit `-Lan` for laptop-only use. Stop it with
`.\tools\stop_headend.ps1`.

Existing installations can use the start command directly. Model weights,
accounts, device keys, and recordings are local and are not in this repository.
The current LAN prototype uses HTTP; keep it on a trusted local network.

## Build the unit firmware

Target: **Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2**, 16 MB flash / 8 MB PSRAM.
Install ESP-IDF **5.5.1** at `C:\Espressif\esp-idf` with tools under
`C:\Espressif`, or use an activated matching ESP-IDF shell from `firmware/`.
The included wrapper uses those Windows installation paths.

```powershell
.\tools\idf.ps1 -DIDF_TARGET=esp32s3 build
# Replace COM6 with your unit's serial port. Flashing replaces its firmware.
.\tools\idf.ps1 -p COM6 flash
```

The source includes the small pinned Hey Jarvis wake model and generated chime
needed by the build. Larger speech models are provisioned separately. See the
[hardware and development history](docs/DEVELOPMENT_HISTORY.md) for wiring and
[display controls](docs/DISPLAY_CONTROLS.md) for the serial interface.

## Project layout

| Directory | Purpose |
| --- | --- |
| `headend/` | Python API, audio pipeline, local speech, browser UI and tests |
| `firmware/` | ESP-IDF unit firmware and component adapters |
| `tools/` | Startup, model provisioning, device control and diagnostics |
| `training/hey_adam/` | Pinned custom wake-word training research and benchmarks |
| `docs/` | Design, setup, hardware notes and feature guides |
| `vendor/` | Required upstream source, references, licenses and provenance |
| `artifacts/` | Ignored local recordings, screenshots and diagnostic reports |
| `backups/` | Ignored local recovery copies |

## Guides

- [Head-end architecture and network protocol](docs/HEADEND_NETWORK.md)
- [Head-end setup and developer notes](headend/README.md)
- [Wake-word model generation on the GPU machine](docs/WAKEWORD_MODEL_GENERATION.md)
- [Voice model packages](docs/VOICE_PACKAGES.md)
- [Information queries and weather](docs/INFORMATION_QUERIES.md)
- [Audio and board capacity](docs/AUDIO_AND_CAPACITY.md)
- [Response latency and silence detection](docs/RESPONSE_LATENCY.md)
- [Touch UI](docs/TOUCH_UI.md) and [listening cue](docs/LISTENING_CUE.md)
- [Audio test UI](docs/HEADEND_TEST_UI.md) and [longer-term plan](docs/HEADEND_PLAN.md)
- [Workspace maintenance](docs/WORKSPACE.md)
- [Third-party notices](THIRD_PARTY_NOTICES.md)

## Validation

```powershell
.\headend\.venv\Scripts\python.exe -m unittest headend.test_session headend.test_server headend.test_fleet headend.test_cue headend.test_voices headend.test_queries -q
```

Tests use synthetic fixtures; they do not flash or record the physical unit.
Browser checks are separate: `tools/check_fleet_browser.py` uses local Chrome,
Python packages `requests` and `websockets`, ports 8766/9223, and an isolated
fixture/profile. Run it from the root with `py -3.12 tools/check_fleet_browser.py`.
Hardware diagnostic scripts may record audio or play sounds; read their usage
before running them. Initial development results are in the local-only
`artifacts/` directory and summarized in the guides.

## Licensing

Third-party files retain their own licenses, including GPLv3 components.
No project-wide license has been selected for original ADAM code yet; this
repository does not grant an additional license to that code. See
[third-party notices](THIRD_PARTY_NOTICES.md) for the included material.
