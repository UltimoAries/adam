# Local voice head-end

The local head-end runs at **http://127.0.0.1:8765**. Start it with
`./tools/start_headend.ps1 -Lan -OpenBrowser`. Sign in to manage units, receive
Wi-Fi audio/transcripts and send typed or recorded pages. See
[HEADEND_NETWORK.md](../docs/HEADEND_NETWORK.md) for the implemented design/protocol.
The USB/WAV diagnostic bench remains at `/bench`; its instructions are in
[HEADEND_TEST_UI.md](../docs/HEADEND_TEST_UI.md).

Version 0.6 adds silence-ended wake sessions and keeps the local speech models
warm. See [RESPONSE_LATENCY.md](../docs/RESPONSE_LATENCY.md) for measured improvements.
Weather answers were introduced in v0.5. Configure the source,
saved locations/aliases, temperature units and reply voice at `/queries`.
Speech stays local; Open-Meteo weather/location requests need internet access.
See [INFORMATION_QUERIES.md](../docs/INFORMATION_QUERIES.md) for the handler contract,
configuration and validation commands.

The broader plan is in [HEADEND_PLAN.md](../docs/HEADEND_PLAN.md). Wi-Fi streaming,
local English transcription, both paging methods and on-device Hey Jarvis
development wake are tested. Hey Adam training and home-device adapters remain
upcoming stages; the custom wake-word proof remains an acceptance gate.

## Implemented

- `session.py`: accepts ordered, bounded 16 kHz mono PCM chunks; rejects gaps,
  duplicates, invalid sample boundaries and utterances over 15 seconds.
  Touch and wake events share the same contract. Closing/cancelling releases
  the active buffer; raw audio is omitted from the object representation.
- `stt.py`: a provider interface and a CPU INT8 faster-whisper adapter. The
  adapter requires an explicit local model directory including the tokenizer,
  to avoid the upstream tokenizer-download fallback. Dependencies and base.en
  are installed in the isolated environment. The actual model transcribed a
  clean reference correctly. After the v8 ADC channel-rate correction, the
  operator confirmed clear captured speech and a correct transcript too.
- `__main__.py`: inspect a local PCM WAV or pass it through the adapter once a
  model is provisioned. No audio is recorded by these commands.
- `server.py`, `clips.py`, `device.py`: bounded jobs, local original-WAV storage,
  review/resampling, and serialized USB operations. `web/dist/` is the basic UI.
- `fleet.py`, `state.py`: authenticated device streams, account/device storage,
  named units, targeted expiring pages and explicit playback acknowledgements.
- `voices.py`, `voice_package.py`, `voice_worker.py`, `voice_api.py`: versioned
  Piper ONNX profiles, bounded local CPU synthesis, validated imports and the
  signed-in `/voices` library. Basic English / Kristin is included; typed pages
  use the persistent default or a per-page voice. See
  [VOICE_PACKAGES.md](../docs/VOICE_PACKAGES.md) for model requirements, packaging and
  training/export instructions. `speech.py`/`synthesize.ps1` remain legacy tools.
- `config.example.toml`: proposed configuration/requirements, including
  Hey Adam, local English recognition and listen-then-reply. The current CLI
  does not load this file, and a phrase string does not train a wake model.

## Checks and usage

From the project root, using the installed Python 3.12:

```powershell
headend/.venv/Scripts/python.exe -m unittest headend.test_server headend.test_session headend.test_fleet headend.test_cue headend.test_voices -v
headend/.venv/Scripts/python.exe -m tools.fetch_voice_model
headend/.venv/Scripts/python.exe -m tools.check_voice_runtime
py -3.12 tools/check_fleet_browser.py
py -3.12 -m headend --help

# An existing uncompressed 16 kHz, 16-bit mono clip, at most 15 seconds:
py -3.12 -m headend path\to\clip.wav

# Actual local transcription with the installed environment and model:
headend/.venv/Scripts/python.exe -m headend path\to\clip.wav --model-dir headend\models\stt\base.en
```

Thirty-four server/session/cue/voice tests passed on October 28, 2026. Unit tests use
synthetic samples and a fixture transcriber; actual recognition was checked
separately on the connected unit. Hey Jarvis is installed and hardware-tested;
the Hey Adam IDs used in contract tests are metadata, not a trained custom model.

A missing/incomplete model is an explicit error. Default CLI operation reports
audio format/duration without importing an STT library. The installed dependency
versions and model revision are recorded in `requirements-lock.txt` and
`stt-models.json`; the speech voice is pinned in `voice-models.json`.
Actual Piper import, preview and explicit-voice paging passed separately;
local STT read the generated phrase as "Hello, dinner is ready."
No placeholder transcript is returned by the running service.
Firmware v12 prepends two seconds of rolling audio and starts capture at the
trigger, including speech during the cue/upload setup. The head-end preserves
the prefix in recording and transcription input and shows its duration in the
dashboard. `python -m tools.check_preroll_device` verifies a real ten-second
upload and runs the target-side buffer regression check; it records room audio.

Without `-Lan`, the service binds to loopback. LAN mode exposes authenticated
device endpoints while retaining local-only inference. One worker serializes
transcription jobs outside the event loop. The current LAN transport uses HTTP;
HTTPS and device certificate trust remain before broader network access.

This debugging UI saves test uploads locally until deleted. Text jobs remain
in memory. Cloud inference and home-device actions are not connected.
