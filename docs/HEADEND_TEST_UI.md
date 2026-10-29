# Local Voice Bench

Sign in at **http://127.0.0.1:8765**, then open **Audio bench** (`/bench`).
The main dashboard now provides Wi-Fi streaming and paging; see
[HEADEND_NETWORK.md](HEADEND_NETWORK.md).

```powershell
./tools/start_headend.ps1 -Lan -OpenBrowser
./tools/stop_headend.ps1
```

The server runs in a hidden process and stays available for testing. LAN mode
also serves authenticated Wi-Fi audio and paging. This diagnostic bench still
uses the USB connection on COM6 for its Record/Fetch/Replay controls.

## Use it

1. **Record 8 sec** starts a capture on the box. Speak while its eye animates.
   The page fetches the completed clip automatically. **Stop** ends capture or
   box playback early. **Fetch latest clip** imports a recording already made
   with the device's touch controls.
2. Choose a clip and use the browser audio player to hear it on the laptop.
   Select original stereo, Microphone 1 or Microphone 2. Review gain offers
   0/+12/+24/+30 dB for quiet recordings. Levels and clipping counts describe
   the raw original; the waveform scales for inspection.
3. **Replay latest on box** plays the box's current RAM recording, using the
   selected transcription microphone. This is independent of the selected
   library WAV; an uploaded library file is not sent back to the board.
4. **Transcribe clip** runs local English faster-whisper base.en with CPU INT8.
   Automatic transcription after capture/upload is enabled by default.
   Optional input boost precedes VAD/recognition. The text, input gain, and
   processing time are shown; an empty recognition result says “No speech
   detected.” Cancel suppresses the result while the worker finishes.
5. **Upload a WAV** or drag it into the upload area. Accepted files are 16-bit
   PCM, mono/stereo, 8–48 kHz, up to 15 seconds and 4 MiB. The STT path converts
   the selected channel to 16 kHz. The original upload remains byte-exact.

The library holds test clips under `headend/data/clips/`. This debugging UI
intentionally saves uploads until deleted. It has a 48-clip / 64 MiB limit;
Delete selected clip removes that clip from disk. Transcription jobs/results
are in memory and do not survive a server restart. Runtime data, model weights,
virtual environments, browser profiles and original test recordings are ignored
by version control. No audio or text is sent to a cloud inference service.

## Current diagnostic result

The library is seeded with a locally synthesized clean speech reference,
the 440 Hz loopback test and captured microphone clips. The reference was
transcribed correctly in about 0.5–0.6 seconds after the model was loaded;
the first web job took 0.92 seconds including model load. These are measurements
of a short test, not guaranteed response times.

Firmware v8 fixes the garbled microphone capture. Earlier recordings had
about 87.5% exact zeros in a repeating 16-frame pattern, already present at
the I2S input. The ADC initialization incorrectly wrote register 0x08 as 0x00;
preserving its two-channel rate field (0x10) removes those gaps. Changing
slot width or analog power settings alone had not resolved the problem.

Both digital loopback and speaker-to-microphone 700 Hz tests now pass on both
channels, with no periodic sample gaps, clipping, or capture overruns. The user
recorded a new eight-second speech clip and confirmed clear playback and a
correct local transcript. The measured follow-up transcription took 485 ms
with the model already loaded. Old faulty clips remain available for comparison.
See validation and reproduction (`artifacts/validation-microphone-v8.md`, local only).

Transcription runs **after a clip finishes**, not as a continuous live stream.
The asynchronous job API keeps the page responsive and provides a boundary
for future intent/API/LLM integration. Device actions and local LLM calls are
not connected in this test server.

## Setup on another checkout

```powershell
py -3.12 -m venv headend/.venv
headend/.venv/Scripts/python.exe -m pip install -r headend/requirements-lock.txt
headend/.venv/Scripts/python.exe tools/fetch_stt_model.py
./tools/start_headend.ps1 -OpenBrowser
```

`headend/stt-models.json` pins the downloaded model revision. Model provisioning
downloads public weights; it does not read recordings. Normal server startup
sets offline model operation and requires complete local model/tokenizer files.
Use `VOICE_USB_PORT` in the launch environment if the board moves from COM6.

## API boundary

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Server/model state and current transcription job |
| `GET /api/clips` | List stored test clips and raw-channel metrics |
| `POST /api/clips` | Raw WAV body; optional URL-encoded `X-Filename` header |
| `GET /api/clips/{id}/audio` | Original WAV; `channel=1/2` and `gain_db=12/24/30` create a review response |
| `DELETE /api/clips/{id}` | Remove a test clip |
| `POST /api/clips/{id}/transcriptions` | JSON `{ "channel": 1, "boost": true }`; returns a job ID |
| `GET /api/jobs/{id}` | Status, transcript, timing and source clip/channel |
| `POST /api/jobs/{id}/cancel` | Suppress a pending transcription result |
| `GET /api/device` | Current USB microphone/playback/UI state |
| `POST /api/device/record` | JSON `{ "seconds": 8 }` |
| `POST /api/device/fetch` | Download the board's completed clip into the library |
| `POST /api/device/replay` | JSON `{ "channel": 1 }`; replay current device RAM clip |
| `POST /api/device/stop` | Stop device capture/playback |

Interactive API documentation: http://127.0.0.1:8765/api/docs

## Verification

Twelve session/server tests passed, including upload integrity, WAV validation,
sample-rate conversion, channel separation, concurrent requests, cancellation,
upload limits and device route wiring. Live checks used the actual local model
and COM6 fetch. Chrome browser checks verified a visible transcript, decoded
audio playback, no script exceptions, desktop layout and mobile overflow.

```powershell
headend/.venv/Scripts/python.exe -m unittest headend.test_server headend.test_session -v
```

Evidence: `artifacts/headend-live-check.json`, `headend-browser-check.json`,
`headend-desktop.png`, `headend-mobile.png`, and `microphone-check-v5.json`.
