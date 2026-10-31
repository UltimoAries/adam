# ADAM Head-End

The Python head-end serves the browser dashboard, authenticates ESP32-S3 speaker
units over the local network, transcribes incoming audio, routes spoken commands
and questions, synthesizes spoken replies, and stores the optional training
archive.

- **Dashboard & fleet management (`/`)**: Pair units over USB, monitor online
  status, review incoming audio sessions, and send typed or recorded pages.
- **Model services (`/models`)**: Prefer optional LAN GPU servers for speech
  recognition (Speaches / faster-whisper) and general conversation (Ollama),
  with automatic local CPU fallbacks (`base.en` faster-whisper, local Piper, and
  optional local Ollama). See [CONVERSATION.md](../docs/CONVERSATION.md).
- **Home Assistant (`/home-assistant`)**: Persistent local names and aliases,
  optional automatic entity import, capability-based controls, and spoken
  commands. See [HOME_ASSISTANT.md](../docs/HOME_ASSISTANT.md).
- **Information & weather (`/queries`)**: Spoken weather and forecast answers via
  Open-Meteo, plus cached time/weather metadata for each unit's sky display
  ([SPACE_PANEL.md](../docs/SPACE_PANEL.md) and [INFORMATION_QUERIES.md](../docs/INFORMATION_QUERIES.md)).
- **Voice library (`/voices`)**: Import, validate, preview, and select versioned
  Piper ONNX voice packages. See [VOICE_PACKAGES.md](../docs/VOICE_PACKAGES.md).
- **Training archive (`/training`)**: Durable original interaction recordings,
  wake-phrase boundary markers, and human review labels. See
  [TRAINING_ARCHIVE.md](../docs/TRAINING_ARCHIVE.md).
- **Audio diagnostic bench (`/bench`)**: Direct USB/WAV clip capture, waveform
  inspection, and local transcription checks. See [HEADEND_TEST_UI.md](../docs/HEADEND_TEST_UI.md).

See [HEADEND_NETWORK.md](../docs/HEADEND_NETWORK.md) for the network protocol and
[deploy/pi5/README.md](../deploy/pi5/README.md) for headless Raspberry Pi 5 /
Docker deployment.

## Architecture modules

- `server.py`: FastAPI application, security headers, health endpoint, static UI
  routes, and background service lifecycle.
- `fleet.py`, `state.py`, `reconnect_unit.py`: Administrator sessions, SQLite
  account/unit persistence, authenticated device polling/streaming, five-minute
  page expiry, playback acknowledgements, and credential-rotating USB reconnects.
- `session.py`, `clips.py`, `device.py`: Ordered 16 kHz mono PCM frame assembly
  (with 2-second pre-roll and silence-ended capture), WAV validation/storage,
  and serialized USB serial bridge commands.
- `stt.py`, `model_services.py`: Local CPU INT8 `faster-whisper` adapter (requiring
  an explicit local model directory including `tokenizer.json`) and optional
  remote HTTP speech routing with failure cooldowns.
- `voices.py`, `voice_package.py`, `voice_worker.py`, `voice_api.py`: Validated
  Piper ONNX package registry and bounded persistent worker subprocess
  (`PiperRuntime`).
- `home_assistant.py`, `ha_inventory.py`, `ha_devices.py`: Home Assistant REST
  integration, local naming/aliasing, and spoken device-command matching.
- `queries.py`, `panel.py`: Weather intent parser, cached Open-Meteo lookups,
  and non-blocking home-screen metadata snapshots.
- `conversation.py`: Short spoken answers via preferred LAN Ollama with bounded
  loopback Ollama fallback and asynchronous loaded-model status probing.
- `training_archive.py`, `training_api.py`: Bounded recording archive with
  SHA-256 verification, free-space/size caps, and review endpoints.
- `__main__.py`: CLI tool to inspect a local 16 kHz mono WAV or run offline
  transcription against an installed model directory.

## Local development & CLI usage

Require **Python 3.12**. From the repository root:

### Windows (PowerShell)

```powershell
py -3.12 -m venv headend/.venv
.\headend\.venv\Scripts\python.exe -m pip install -r headend/requirements-lock.txt
.\headend\.venv\Scripts\python.exe -m tools.fetch_stt_model
.\headend\.venv\Scripts\python.exe -m tools.fetch_voice_model
.\tools\start_headend.ps1 -Lan -OpenBrowser
```

### Linux / macOS

```sh
python3.12 -m venv headend/.venv
./headend/.venv/bin/python -m pip install -r headend/requirements-lock.txt
./headend/.venv/bin/python -m tools.fetch_stt_model
./headend/.venv/bin/python -m tools.fetch_voice_model
./headend/.venv/bin/python -m headend.server --lan
```

Without `-Lan` / `--lan`, the server binds to `127.0.0.1:8765` only. Note:
- Initial administrator account setup (`/api/auth/setup`) must be performed from
  `127.0.0.1` (or over an SSH tunnel `ssh -L 8765:127.0.0.1:8765 <user>@<host>`)
  and requires a **12–128 character password**.
- Firmware pairing requires an explicit RFC1918 LAN IPv4 URL on port `8765`
  (for example `http://192.168.1.50:8765`); `.local` hostnames, HTTPS, and
  non-8765 ports are rejected by the device.

### CLI & test suite

```powershell
# Run the synthetic unit test suite (does not access USB hardware or network):
.\headend\.venv\Scripts\python.exe -m unittest discover -s headend -t . -q

# Validate installed Piper voice synthesis runtime:
.\headend\.venv\Scripts\python.exe -m tools.check_voice_runtime

# Inspect or transcribe an existing 16 kHz, 16-bit mono WAV (<= 15 seconds):
.\headend\.venv\Scripts\python.exe -m headend path\to\clip.wav
.\headend\.venv\Scripts\python.exe -m headend path\to\clip.wav --model-dir headend\models\stt\base.en
```
