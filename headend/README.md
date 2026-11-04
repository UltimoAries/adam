# ADAM Head-End

The Python head-end serves the browser dashboard, authenticates ESP32-S3 speaker
units over the local network, transcribes incoming audio, routes spoken commands
and questions, synthesizes spoken replies, and stores the optional training
archive.

- **Dashboard & fleet management (`/`)**: Pair units over USB, monitor online
  status, review incoming audio sessions, and send typed or recorded pages.
  The **Type to ADAM** box shares voice command handlers and keeps short context
  isolated by signed-in session, browser tab, and selected speaker. See
  [TEXT_COMMANDS.md](../docs/TEXT_COMMANDS.md).
  The Monitor Map includes host CPU/memory and ADAM data-drive usage; authenticated
  `/api/system/resources` readings are cached for five seconds. CPU is averaged
  across host cores between samples; memory excludes reclaimable Linux cache.
  Storage covers the filesystem holding `headend/data`, not every attached drive.
  Linux and Windows hosts are supported; unavailable metrics show as unavailable.
  The 3D map also supports persistent light-to-room assignments, live room
  illumination, and unavailable-state indicators. See [MAP_LIGHTS.md](../docs/MAP_LIGHTS.md).
- **Model services (`/models`)**: One GPU/Pi preference and installed-model
  dropdowns select speech recognition (Speaches / faster-whisper) and general
  conversation (Ollama) on one GPU computer. Both routes share a failure cooldown
  and fall back to local `base.en` and configured local Ollama. Piper TTS stays
  local. See [CONVERSATION.md](../docs/CONVERSATION.md).
  **Preview home requests** evaluates model interpretations without executing
  actions or reading live Home Assistant states. This is separate from live
  command routing.
- **Timers (`/` and voice)**: Create, extend, check, and cancel named countdowns
  with per-speaker display/ringing and fleet-wide lookup by name.
- **Cameras (`/cameras`)**: Private RTSP settings, seven gesture choices, bounded
  Home Assistant action rules, and optional event snapshots/debugging metadata.
  Inference runs in an optional Pi sidecar. See [CAMERAS.md](../docs/CAMERAS.md).
- **Home Assistant (`/home-assistant`)**: Persistent local names and aliases,
  optional automatic entity import, capability-based controls, and spoken
  commands, including polite wording, bounded contextual light groups, and
  combined on/brightness requests. "Shut" and "close" resolve the same supported
  control. See [HOME_ASSISTANT.md](../docs/HOME_ASSISTANT.md).
- **Follow-ups and status**: Capable speakers open short listening windows after
  qualifying replies. Device references, weather topics, and recent model
  exchanges expire after 90 seconds. Status questions use live service data.
  Reply interruption remains experimental and its investigation is paused. See
  [FOLLOWUP_CONVERSATION.md](../docs/FOLLOWUP_CONVERSATION.md) and
  [STATUS_QUESTIONS.md](../docs/STATUS_QUESTIONS.md).
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
- `system_monitor.py`: Cached host CPU/memory counters and data-filesystem capacity
  without external commands or blocking sampling. In the Pi Docker deployment,
  CPU/memory describe the host, not just the ADAM container or GPU computer.
- `map_lights.py`: HA-origin-scoped room placements and cached read-only light
  status for the sample map. Assigning or removing a light never operates it.
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
- `dialogue.py`, `context_actions.py`: Scoped topic memory, single-use delivered
  reply grants, and bounded contextual controls through existing HA validation.
- `status_queries.py`: Read-only Home Assistant, speaker, and model status with
  short follow-up context and evidence-based explanations.
- `intent_preview.py`: Structured model proposals checked against the enabled
  inventory, without action execution or live state reads.
- `text_commands.py`: Authenticated browser commands, tab context, optional
  speaker replies, and durable request replay protection.
- `queries.py`, `panel.py`: Weather intent parser, cached Open-Meteo lookups,
  and non-blocking home-screen metadata snapshots.
- `conversation.py`: Short spoken answers via preferred LAN Ollama with bounded
  loopback Ollama fallback and asynchronous loaded-model status probing.
- `processing.py`, `model_catalog.py`: Combined GPU/Pi settings, shared failure
  cooldown, and discovery of installed speech/conversation models.
- `timers.py`: Persistent countdowns, voice duration parsing, and speaker poll
  reconciliation.
- `cameras.py`, `camera_worker.py`: Camera registry, sustained gesture gating,
  bounded action dispatch, private event images, and separate RTSP inference worker.
- `recording_work.py`, `latency.py`: Bounded asynchronous recording persistence
  and live voice-pipeline timing measurements; see [VOICE_LATENCY.md](../docs/VOICE_LATENCY.md).
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
