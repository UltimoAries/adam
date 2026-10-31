# Local Voice Bench (`/bench`)

Sign in to the head-end at **http://127.0.0.1:8765** (or `http://<headend-ip>:8765`),
then open **Audio bench** (`/bench`).

`/bench` is the USB and WAV diagnostic workbench inside the unified head-end web
application, alongside the main `/` fleet and paging dashboard,
`/home-assistant` device inventory, `/queries` weather settings, `/voices` TTS
library, `/training` interaction archive, and `/models` speech/LLM routing
controls. See [HEADEND_NETWORK.md](HEADEND_NETWORK.md) for normal Wi-Fi
operation.

```powershell
./tools/start_headend.ps1 -Lan -OpenBrowser
./tools/stop_headend.ps1
```

While paired speakers stream over Wi-Fi on `/`, the **Record / Fetch / Replay**
device controls on `/bench` operate over a local USB Serial/JTAG connection
(default `COM6`; override with `VOICE_USB_PORT`).

## Using the diagnostic bench

1. **Record 8 sec** starts a local stereo diagnostic capture on a USB-connected
   box while its screen shows green listening mist, then fetches the completed
   clip automatically. **Stop** ends capture or box playback early. **Fetch
   latest clip** imports a diagnostic clip already recorded from **Settings →
   Audio → MIC TEST**.
2. Choose a clip and use the browser player to inspect it. Select original
   stereo, Microphone 1 or Microphone 2, with optional `0 / +12 / +24 / +30 dB`
   review gain for quiet recordings without altering the stored WAV.
3. **Replay latest on box** plays the device's current PSRAM diagnostic buffer
   using the selected channel. Uploaded library WAVs stay in the browser/server
   library rather than overwriting the device's diagnostic RAM buffer.
4. **Transcribe clip** runs English speech-to-text (`faster-whisper` `base.en`
   CPU INT8 locally, or the configured recognition route) after capture or
   upload completes. Optional input boost runs before VAD/recognition, and
   **Cancel** suppresses pending output.
5. **Upload a WAV** (or drag and drop): accepts 16-bit PCM mono/stereo WAVs at
   `8..48` kHz, up to 15 seconds and 4 MiB, preserving the uploaded bytes intact
   while converting the chosen channel to 16 kHz for STT.

Diagnostic clips are stored under `headend/data/clips/` with a **48-clip /
64 MiB** cap until explicitly deleted. Bench transcription jobs live in memory
and intentionally **never** trigger Home Assistant actions, weather pages, or
spoken LLM replies, keeping `/bench` safe for acoustic and recognition testing.

## ES7210 capture verification notes

Firmware v8 resolved an early bring-up bug where ES7210 register `0x08` was
written as `0x00`, zeroing 87.5% of frames in a repeating 16-frame pattern.
Preserving the two-channel rate field (`0x10`) eliminated all periodic sample
gaps across both digital loopback and acoustic 700 Hz tests, yielding clean
stereo playback and sub-second warm transcription (~485 ms on CPU `base.en`).

## Setup on a fresh checkout

```powershell
py -3.12 -m venv headend/.venv
headend/.venv/Scripts/python.exe -m pip install -r headend/requirements-lock.txt
headend/.venv/Scripts/python.exe tools/fetch_stt_model.py
./tools/start_headend.ps1 -OpenBrowser
```

`headend/stt-models.json` pins the local Whisper model revision. Normal head-end
startup runs in offline model mode and expects local model/tokenizer files to be
present. Set `VOICE_USB_PORT` if the speaker unit enumerates on a serial port
other than `COM6`.

## Bench API endpoints

All bench endpoints require an authenticated administrator session (interactive
OpenAPI `/docs` and `/redoc` routes are disabled in `headend/server.py`):

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | Server/model readiness and active transcription job state |
| `GET /api/clips` | List stored diagnostic clips and raw channel metrics |
| `POST /api/clips` | Upload raw WAV bytes (optional URL-encoded `X-Filename` header) |
| `GET /api/clips/{id}/audio` | Fetch original WAV (`channel=1\|2` and `gain_db=12\|24\|30` build a review copy) |
| `DELETE /api/clips/{id}` | Delete a stored diagnostic clip |
| `POST /api/clips/{id}/transcriptions` | Start `{ "channel": 1, "boost": true }` recognition job |
| `GET /api/jobs/{id}` | Poll job status, transcript, latency and source clip/channel |
| `POST /api/jobs/{id}/cancel` | Cancel/suppress a pending transcription job |
| `GET /api/device` | Read USB-connected microphone, playback and UI state |
| `POST /api/device/record` | Start `{ "seconds": 8 }` USB diagnostic capture |
| `POST /api/device/fetch` | Download the unit's completed RAM clip into `headend/data/clips/` |
| `POST /api/device/replay` | Replay `{ "channel": 1 }` from the unit's diagnostic RAM buffer |
| `POST /api/device/stop` | Stop active USB capture or playback |

## Verification

```powershell
headend/.venv/Scripts/python.exe -m unittest headend.test_server headend.test_session -v
```

Automated tests cover WAV validation, byte-exact storage, sample-rate conversion,
channel isolation, concurrency, job cancellation, upload bounds and USB device
route handling.
