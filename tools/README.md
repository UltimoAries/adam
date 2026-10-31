# Developer & Operator Tools

Utilities in `tools/` support local head-end setup, ESP32-S3 firmware builds, USB serial/framebuffer/audio diagnostics, Raspberry Pi packaging, and automated verification. Run Python scripts from the repository root (`python -m tools.<script>` or `python tools/<script>.py`).

---

## 1. Head-End Startup & Model Provisioning

| Script | Purpose |
| --- | --- |
| `start_headend.ps1` | Starts the local Windows head-end server (`headend/.venv`) in the background on `127.0.0.1:8765` (pass `-Lan` to bind LAN, `-OpenBrowser` to open the web UI). |
| `stop_headend.ps1` | Verifies and stops the background head-end process recorded in `headend/data/server.pid`. |
| `fetch_stt_model.py` | Downloads and pins the local `Systran/faster-whisper-base.en` INT8 speech-to-text model into `headend/models/stt/base.en`. |
| `fetch_voice_model.py` | Downloads and verifies the pinned default English Piper TTS voice (`basic-english@1.0.0`) into `headend/models/voices/`. |
| `package_voice.py` | Packages an exported `.onnx` Piper model, config, and license metadata into a validated v1 `.zip` voice archive (`--probe` optionally runs local synthesis). |
| `migrate_headend.py` | Exports or restores offline SHA-256-verified backups of `headend/data` and `headend/models` (`export` / `restore` subcommands; requires the local server to be stopped). |
| `package_pi_headend.py` | Builds a clean `.tar.gz` source bundle (`--output`) for Raspberry Pi deployment, excluding runtime data, models, secrets, archives, and `test_*.py` files. |

---

## 2. Firmware Build, Wake Model & Cue Generation

| Script | Purpose |
| --- | --- |
| `idf.ps1` | PowerShell wrapper that runs `idf.py` inside `firmware/`. Uses `idf.py` if already on `PATH`; otherwise resolves `$env:IDF_PATH\export.ps1`, `$env:USERPROFILE\.espressif`, or `C:\Espressif\esp-idf\export.ps1` while respecting `$env:IDF_TOOLS_PATH`. |
| `install_wake_model.py` | Validates a microWakeWord v2 manifest + `.tflite` model and writes `wake_model.tflite`, `wake_model.json`, and `wake_model_config.h` into `firmware/main/`. |
| `install_listen_cue.py` | Decodes a custom audio file into 16 kHz mono PCM (`firmware/main/listen_cue.pcm` + `listen_cue.json`) for firmware flashing. |
| `make_trigger_chime.py` | Synthesizes a low-level two-note acknowledgement chime (`listen_cue.pcm`) designed not to overload the adjacent microphones. |
| `make_listen_cue.ps1` | Synthesizes a raw spoken `"Huh?"` WAV on Windows via `System.Speech` and invokes `process_listen_cue.py`. |
| `process_listen_cue.py` | Time-stretches, pitch-adjusts, normalizes, and writes the synthesized listening cue into `firmware/main/listen_cue.pcm`. |
| `elevenlabs_cue_studio.py` | Optional local `tkinter` GUI for auditioning ElevenLabs listening-cue candidates; keeps the API key in process memory (never saved to disk). |

---

## 3. USB Device Control & Framebuffer/Audio Diagnostics

All USB serial scripts accept `--port <PORT>` and default to `VOICE_USB_PORT` if set (`COM6` on Windows or `/dev/ttyACM0` on Linux/macOS when unset). None toggle `DTR`/`RTS`, preventing unintended ESP32 resets.

**Optional Python dependencies for host-side imaging/webcam scripts:**
- `Pillow` (`PIL`) — required for RGB565 framebuffer decoding and panel pixel checks (`snapshot.py`, `check_*_panel.py`, `check_llm_ring.py`).
- `opencv-python` (`cv2`) — required only for physical webcam capture scripts (`capture.py`, `bench.py`).

### Interactive Control & Capture

| Script | Purpose |
| --- | --- |
| `control.py` | Sends a single newline-terminated console command over USB serial and verifies `OK` acknowledgement. |
| `snapshot.py` | Downloads the live 360x360 RGB565 framebuffer over USB (`snapshot`) and saves a circular-masked PNG (requires `Pillow`). |
| `mic_capture.py` | Triggers or downloads an on-device stereo 16 kHz diagnostic recording, saving stereo/per-mic WAVs and FFT/RMS metrics. |
| `watch_serial.py` | Passively logs ESP32 USB serial output (with automatic USB reconnect and optional `--status` polling) without resetting the board. |
| `capture.py` | Captures a settled frame from a local USB webcam (`--camera`, `--exposure`; requires `opencv-python`). |
| `bench.py` | Steps through display color, brightness, rotation, and eye suites over serial while capturing webcam evidence (requires `opencv-python`). |
| `review_mic.py` | Writes a gain-normalized review copy of a mono 16-bit PCM WAV without modifying the original recording. |
| `analyze_capture_integrity.py` | Inspects saved device clips in `headend/data/clips` for DMA block-boundary discontinuities, DC offset, and spectral balance. |
| `check_connection_log.py` | Validates a `watch_serial.py` capture log for panics, brownouts, frame-counter stalls, stack headroom, and Wi-Fi drops. |

### Hardware-in-the-Loop Verification

| Script | Purpose |
| --- | --- |
| `check_audio_start.py` | Measures speaker/codec acoustic tone onset latency across repeated captures and restores device volume/gain. |
| `check_mic_fidelity.py` | Verifies I2S clock ratios plus digital-loopback and acoustic 700 Hz stereo capture without sample gaps. |
| `check_microphone.py` | Exercises 15-second concurrent capture/playback, parameter bounds, clip reservation, and touch-stop paths. |
| `check_preroll_device.py` | Triggers a live `headend listen` session over USB and verifies the uploaded 10-second WAV (2 s pre-roll + 8 s capture). |
| `check_wake_display.py` | Confirms the listening scene remains green continuously while the acknowledgement cue plays. |
| `profile_resources.py` | Samples heap, CPU, storage, audio DMA, and concurrent Wi-Fi scan stability across device UI states. |
| `scan_check.py` | Exercises live Wi-Fi scanning, pagination, and keyboard navigation over USB without modifying saved credentials. |
| `ui_check.py` | Tests touch hit regions, volume/brightness sliders, control auto-hide, and settings navigation, restoring original levels. |
| `check_daylight_panel.py` | Previews night, sunrise, midday, and sunset skies over USB and verifies framebuffer contrast and sun/star rendering (`Pillow`). |
| `check_space_panel.py` | Verifies animated sky motion, green/blue activity mist, and center-tap stop behavior on the idle/active home panel (`Pillow`). |
| `check_weather_panel.py` | Captures clear, overcast, drizzle, rain, snow, fog, and storm panel states and checks row legibility and rain animation (`Pillow`). |
| `check_llm_ring.py` | Verifies the outer status ring color (`--expect pi` or `--expect gpu`) directly from the USB framebuffer (`Pillow`). |

---

## 4. Automated Verification & Upstream Reference Pinning

### Head-End, Browser & Deployment Checks

| Script | Purpose |
| --- | --- |
| `check_fleet_browser.py` | End-to-end headless browser QA across login, 3D monitor map, paging, voices, Home Assistant, weather queries (`Seattle` / `Portland`), training review, and mobile layout. Requires `requests`, `websockets`, and Chrome/Chromium (auto-detected or set via `CHROME_BIN`). |
| `fleet_ui_fixture.py` | Isolated FastAPI fixture server launched automatically by `check_fleet_browser.py` on `127.0.0.1:8766` using temporary test state and fake hardware/services. |
| `test_gpu_control.py` | Standard `unittest` suite for the Windows GPU model-host tray controller (`deploy/model-host/adam_control.pyw`), runnable without live services. |
| `check_voice_runtime.py` | Offline integration check that packages, imports, previews, pages, removes, and locally transcribes a real Piper voice in an isolated temp directory. |
| `check_pi_host.py` | Read-only Raspberry Pi preflight reporting OS, memory, disk, thermals, throttling flags, and Docker/Compose availability. |
| `check_pi_runtime.py` | Read-only container/local check (`--headend-dir` / `ADAM_HEADEND_DIR`) verifying SQLite/archive SHA-256 integrity, Home Assistant reachability, and Piper + Faster-Whisper latency. |
| `check_live_speech.py` | Exercises live `/api/voices/preview` and `/api/clips/.../transcriptions` on a running head-end (`--headend-dir` / `--data-dir` / `ADAM_DATA_DIR`) using a temporary admin session. |
| `check_conversation_runtime.py` | Tests `/api/conversation/test` and archive integrity on a running head-end (`--data-dir` / `ADAM_DATA_DIR`; pass `--enable --remote-url <URL>` to configure remote Ollama routing). |
| `check_speech_routing.py` | Re-transcribes an archived recording through the live STT route (`--data-dir` / `ADAM_DATA_DIR`; pass `--enable --url <URL>` to configure remote STT) while verifying audio/label immutability. |

### Upstream Reference Pinning (`vendor/`)

| Script | Purpose |
| --- | --- |
| `fetch_audio_reference.py` | Fetches SHA-1 blob-verified ES7210/ES8311 board examples and schematics pinned in `vendor/waveshare-tree.json`. |
| `fetch_native_codec_reference.py` | Pins the upstream Apache-2.0 `espressif/esp-adf` `es7210.c` driver and writes SHA-256 provenance metadata. |
| `fetch_touch_reference.py` | Fetches SHA-1 blob-verified `CST816` touch driver reference files pinned in `vendor/waveshare-tree.json`. |
| `fetch_wake_reference.py` | Fetches Git-blob-verified `micro_wake_word`, `micro_speech_features`, and `hey_jarvis` test model files into `vendor/wake/`. |

