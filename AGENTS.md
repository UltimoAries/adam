# AGENTS.md — ADAM Developer & Agent Guide

This document gives AI coding agents and developers an end-to-end architectural map, operational playbook, and set of safety/hygiene invariants for working on **ADAM** (**Automation Display and Mapping**).

---

## 1. System Topology

ADAM is a local-first voice assistant, intercom, and smart-home controller built from three cooperating tiers on a private LAN:

1. **Speaker / Display Units (`firmware/` — ESP-IDF v5.5.x on `esp32s3`, currently Firmware `v28`):**
   - **Target hardware:** **Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2** (`16 MB` flash, `8 MB` octal PSRAM, `360×360` round ST77916 QSPI LCD, CST816 capacitive touch, ES7210 dual-mic ADC, ES8311 DAC/amp).
   - **Physical orientation:** Operate with the **USB-C port pointing down**. On the rear/side enclosure buttons, the **upper button is `BOOT` (`GPIO0`)** and the **lower button is hardware `RESET`**.
   - **On-device responsibilities:** Continuous 16 kHz stereo capture over a shared full-duplex I2S bus, local on-device **Hey Adam** wake-word detection (`hey_adam-20261030-c60f0456` via `micro_wake_word` + TFLite Micro), 2-second rolling pre-roll ring buffer, quiet 120 ms trigger cue, chunked silence-ended HTTP audio upload, mono PCM page playback, local monotonic countdown timer bars, touch UI, and the animated sky/weather/LLM-ring home screen.

2. **Head-End Server (`headend/` — Python 3.12 + FastAPI on port `8765`):**
   - Runs either natively (`python -m headend.server --lan`) or headless in Docker on a **Raspberry Pi 5** (`deploy/pi5/`).
   - Persists paired units, admin sessions, Home Assistant inventory/tokens, query locations, conversation routing settings, and active countdown timers in SQLite (`headend/data/fleet.sqlite3`).
   - Runs local CPU INT8 `faster-whisper` (`base.en`) STT, persistent warm Piper ONNX TTS workers (`headend/voices.py`), Open-Meteo weather/geocoding caching, Home Assistant REST control/reads, and optional bounded training-archive capture (`headend/training_archive.py`).

3. **Optional LAN GPU Model Host (`deploy/model-host/`):**
   - Offloads STT to **Speaches** (`deepdml/faster-whisper-large-v3-turbo-ct2`, OpenAI-compatible `:8000/v1`) and conversational LLM replies to **Ollama** (`qwen3.5:9b` / `adam:latest`, `:11434`).
   - `headend/model_services.py` and `headend/conversation.py` enforce short per-request timeouts and 30-second failure cooldowns, automatically falling back to local Pi CPU STT (`base.en`) and local Pi Ollama (`qwen3.5:0.8b`) without dropping requests.

---

## 2. End-to-End Voice & Display Pipeline

### A. Capture & Upload (`firmware/main/headend.c`, `preroll.c`, `wake.cpp`)
1. `mic.c` feeds 10 ms stereo frames into `wake.cpp` and `headend_feed()` (which caches mono history while the speaker is silent).
2. On wake detection, `voice_buffer_begin_auto()` freezes up to `2000 ms` of pre-roll history, plays the quiet `listen_cue.pcm` chime, and opens `POST /api/units/{unit_id}/audio` with headers:
   - `Authorization: Bearer <device-token>`
   - `X-Session-ID: <32-hex>`
   - `X-Trigger: wake` (or `touch` for manual `Talk 8S` / remote `Listen`)
   - `X-Audio-Mode: silence-v1` (chunked `644`-byte records: 4-byte little-endian sequence + `640` bytes of 20 ms 16 kHz mono PCM, terminated by sequence `0xffffffff` + actual count + endpoint reason).

### B. Utterance Dispatch Order (`headend/fleet.py` → `Fleet.transcription_done`)
Only **completed wake sessions** (`entry['trigger'] == 'wake'` and `job['status'] == 'done'`) trigger automatic actions or spoken replies. Touch captures, remote Listen sessions, `/bench` uploads, and training-archive retranscriptions are diagnostic-only and **never** auto-dispatch.

Wake transcripts route through five deterministic stages before touching the LLM:

1. **Countdown Timers (`headend/timers.py` — `TimerService.submit`):**
   - Handles creating, extending, checking, stopping, and cancelling named timers (`5s`–`24h`, up to `8` concurrent per speaker).
   - Bare *"Hey Adam, stop"* (`stop_ringing_only`) **only** claims the utterance when a timer is actively within its 60-second `00:00` ringing window on that speaker.
   - Display and audio ringing stay scoped to the originating speaker (`reconcile_poll`), while named voice lookups/cancellations (*"How much time is left on the laundry timer?"*) work fleet-wide.
2. **Time, Date, World Clock & Calendar Math (`headend/queries.py` — `time_date_intent`):**
   - Answers local clock/date using the asking unit's configured IANA timezone (works even when weather is disabled), world clock via saved locations or Open-Meteo geocoding (*"What time is it in Tokyo?"*), weekday lookups, and days-until math.
3. **Explicit Outdoor Weather (`headend/queries.py` — `weather_intent`):**
   - Utterances mentioning `weather`, `forecast`, `rain`, `snow`, or `outside` route directly to Open-Meteo (`current`, `today`, or `tomorrow`, cached 10 minutes).
4. **Home Assistant Controls, Sensors & Summaries (`headend/home_assistant.py`, `ha_inventory.py`, `ha_devices.py`):**
   - Matches imported, enabled entities by exact normalized local name, optional HA friendly name, or alias, plus room-first sensor phrasing (*"What's the temperature in the bedroom?"*, *"How warm is the nursery?"*).
   - Handles read-only multi-entity summaries (`summary_intent`: `doors`, `windows`, `openings`, `locks`, `lights`, `leaks`).
   - Executes bounded service calls (`controls()` / `action_payload()`) with state readback confirmation and durable `(unit_id, session_id)` replay protection.
5. **Fallback City Temperature → Conversational LLM (`headend/queries.py` → `headend/conversation.py`):**
   - Ambiguous temperature questions unmatched in Home Assistant (*"What's the temperature in Seattle?"*) fall back to Open-Meteo; everything else routes to `ConversationService` (`adam:latest` on GPU → `qwen3.5:0.8b` on Pi, stripped of `<think>` tags and capped to speakable length).

### C. Reply Delivery & Home Panel Polling (`GET /api/units/{unit_id}/poll`)
- Spoken replies synthesize 16 kHz mono PCM via `VoiceService`, normalize peak amplitude in `Fleet.playback_pcm()` (**strictly `0.1s`–`15.0s`**, `3,200`–`480,000` bytes), and enqueue a targeted page (5-minute expiry).
- Each idle speaker polls `/api/units/{unit_id}/poll?firmware=v28&wake_model=...&busy=0|1[&dismissed=id1,id2]` every `1.5s` (`250 ms` for 30s after a wake capture):
  - Receives `panel` metadata: `unix_time`, `utc_offset`, `device_name`, `weather_state`/`temperature`/`weather_code`, `llm_provider` (`remote` → green perimeter ring, `local` → soft yellow ring), and up to 6 active per-unit `timers` (`id`, `label`, `duration_s`, `end_unix`).
  - `headend.c` converts `end_unix` into monotonic `esp_timer_get_time()` `deadline_us` so active timers keep counting down accurately offline.
  - When a page is offered (`{"type": "page", "id": ..., "bytes": ...}`), the unit fetches `/pages/{id}/audio`, acks `playing`, plays through `audio.c` (with 250 ms post-unmute silence priming so first syllables never clip), and acks `delivered`.

---

## 3. Codebase Map

| Directory / File | Responsibility |
| --- | --- |
| `headend/server.py` | FastAPI app factory, TrustedHost/Origin CSRF middleware, `/api/health`, `/bench` clip routes, static UI routes |
| `headend/fleet.py` | Auth/setup/login, unit pairing, `/poll`, `/audio` streaming receiver, `/api/pages/*`, 5-stage `transcription_done` router |
| `headend/state.py` | SQLite schema & thread-safe helpers for `units`, `users`, `logins`, and `pages` |
| `headend/timers.py` | Spoken/numeric duration parser, SQLite `timers` persistence, `/poll` reconciliation, `/api/timers` routes |
| `headend/queries.py` | Time/date/world-clock/calendar solver, Open-Meteo forecast/geocoding client, saved locations & per-unit timezones |
| `headend/ha_devices.py` | Bounded HA domain capabilities, curated sensor `default_import`, `format_entity_state`, `summary_intent`, `intent` |
| `headend/ha_inventory.py` | Persistent HA entity mapping by `(origin_url, entity_id)`, aliases, conflict detection |
| `headend/home_assistant.py` | Bounded `httpx` HA client (no redirects, no token echo, no POST retries), summaries, `/api/home-assistant/*` |
| `headend/conversation.py` | GPU/Pi Ollama streaming chat client, `<think>` stripping, background 5s model-readiness probe for the LCD ring |
| `headend/model_services.py` | Remote Speaches STT offload with automatic local `faster-whisper` fallback |
| `headend/voices.py` | Verified Piper `.tar.gz` voice package registry and persistent warm ONNX synthesis workers |
| `headend/training_archive.py` | Bounded WAV/transcript/wake-boundary archive and human review store |
| `headend/web/dist/` | Vanilla HTML/CSS/JS pages (`/`, `/queries`, `/home-assistant`, `/models`, `/voices`, `/training`, `/bench`, `/map`) |
| `firmware/main/main.c` | ST77916 QSPI LCD + TCA9554 bring-up, USB Serial/JTAG command parser, main render/tick loop |
| `firmware/main/space.c` | Illustrated day/night sky, WMO weather particles, green/blue ripple mist, LLM glow ring, countdown timer bars |
| `firmware/main/ui.c` | Touch hit-testing, hidden-until-tapped volume/brightness rails, Settings / Wi-Fi / Voice / Audio menus |
| `firmware/main/headend.c` | NVS pairing (`http://<RFC1918-IPv4>:8765`), poll/upload/page task, monotonic timer deadline sync & ringing chime tick |
| `firmware/main/audio.c`, `mic.c` | Shared 16 kHz 32-bit full-duplex I2S bus for ES8311 DAC/amp and ES7210 dual-mic ADC |
| `firmware/main/wake.cpp`, `preroll.c` | Embedded `micro_wake_word` runner (`wake_model.tflite`) and 2s pre-roll + silence endpoint state machine |
| `deploy/pi5/` | Headless ARM64 Docker Compose stack (`compose.yaml`, `compose.llm.yaml`, `compose.usb.yaml`) |
| `deploy/model-host/` | Windows GPU Ollama + Speaches setup and desktop controller (`adam_gpu_ctl.py`) |
| `tools/` | Build/flash wrapper (`idf.ps1`), serial tools (`control.py`, `snapshot.py`), Pi packager (`package_pi_headend.py`), migration & checks |

---

## 4. Local Setup, Build, Test & Deployment Playbook

### Where Local (Git-Ignored) Environment Details Live
Because this repository is public, **no private LAN IPs, hostnames, tokens, or SSH keys are stored in tracked files**. When operating in the maintainer's local workspace, inspect these git-ignored paths instead of hardcoding values:

- **Pi 5 LAN IP, hostname & container UID/GID:** `deploy/pi5/.env` (`ADAM_LAN_IP`, `ADAM_HOSTNAME`, `ADAM_UID`, `ADAM_GID`).
- **Pi 5 SSH key & pinned host key:** `headend/data/deployment/pi5_ed25519` and `artifacts/pi5-known-hosts` (`HostKeyAlias=pi5`).
  - *Windows OpenSSH note:* If `ssh.exe` rejects `pi5_ed25519` with `UNPROTECTED PRIVATE KEY FILE` (`bad permissions`), copy it to a temporary scratch path outside the repo, restrict ACLs with `icacls <temp_key> /inheritance:r /grant:r "$env:USERNAME:(R)"`, run the SSH/SCP command, and delete `<temp_key>` immediately afterward.
- **GPU model-host configuration:** `deploy/model-host/ollama.json` and runtime settings in `headend/data/fleet.sqlite3`.
- **Connected ESP32-S3 USB port discovery:** Look for `VID=0x303A, PID=0x1001` (`USB-Serial/JTAG`, typically `COM6` on Windows or `/dev/ttyACM0` on Linux):
  ```powershell
  .\headend\.venv\Scripts\python.exe -c "import serial.tools.list_ports as lp; [print(p.device, p.description, hex(p.vid or 0), hex(p.pid or 0)) for p in lp.comports()]"
  ```

### Running Tests (Offline & Deterministic)
Use the Python 3.12 virtualenv in `headend/.venv`:

```powershell
# Full head-end unit & integration suite (141 tests; uses FastAPI TestClient)
.\headend\.venv\Scripts\python.exe -m unittest discover -s headend -t . -q

# GPU model-host controller tests
.\headend\.venv\Scripts\python.exe tools/test_gpu_control.py

# Syntax checks after editing Python or Web UI JS
.\headend\.venv\Scripts\python.exe -c "import glob, py_compile; [py_compile.compile(f, doraise=True) for f in glob.glob('headend/*.py')]"
node --check headend/web/dist/fleet.js
```

### Building & Flashing Firmware
`tools/idf.ps1` activates ESP-IDF 5.5.x (`$env:IDF_PATH`, `%USERPROFILE%\.espressif`, or `C:\Espressif\esp-idf`) and automatically derives `IDF_TOOLS_PATH` to match:

```powershell
# Compile firmware (outputs firmware/build/display_probe.bin; must fit in 3 MiB factory app partition)
.\tools\idf.ps1 build

# Flash over USB-C (preserves NVS Wi-Fi & head-end pairing at 0x9000)
.\tools\idf.ps1 -p COM6 flash
```

### Live USB Serial Diagnostics & Framebuffer Snapshots
`tools/control.py` opens the serial port with `DTR=False, RTS=False` so commands never accidentally reset the ESP32-S3:

```powershell
.\headend\.venv\Scripts\python.exe tools/control.py status
.\headend\.venv\Scripts\python.exe tools/control.py headend status
.\headend\.venv\Scripts\python.exe tools/control.py ui status
.\headend\.venv\Scripts\python.exe tools/control.py timer preview 90 PASTA
.\headend\.venv\Scripts\python.exe tools/control.py timer clear
.\headend\.venv\Scripts\python.exe tools/snapshot.py artifacts/screen.png
```

### Deploying Head-End Updates to the Raspberry Pi 5
Package tracked/new runtime source cleanly (excluding `data/`, `models/`, `.env`, `test_*.py`, keys, and recordings) using `tools/package_pi_headend.py`:

```powershell
.\headend\.venv\Scripts\python.exe tools/package_pi_headend.py --output artifacts/adam-pi5-source.tar.gz
```

Copy `artifacts/adam-pi5-source.tar.gz` to the Pi (`<pi-user>@<ADAM_LAN_IP>`) and rebuild in `~/adam/deploy/pi5` (note: Docker on the Pi requires `sudo` if the user is not in the `docker` group):

```sh
tar -xzf ~/adam-pi5-source.tar.gz -C ~/adam
cd ~/adam/deploy/pi5
sudo docker compose -f compose.yaml -f compose.llm.yaml up -d --build
sudo docker compose ps
```

Verify readiness via `GET http://<ADAM_LAN_IP>:8765/api/health` (`"status": "ok"`, `"model_state": "ready"`). Note the Docker container runs unprivileged (`1000:1000`) with `read_only: true`, `HF_HUB_OFFLINE=1`, `headend/data` mounted read-write, and `headend/models` mounted read-only.

---

## 5. Critical Invariants & Gotchas (Read Before Modifying)

1. **Public Repository Hygiene:**
   - Never commit real household LAN IPs, Tailscale `100.x` addresses, personal town/city names, Home Assistant tokens, SSH keys, `.sqlite3` databases, or `.wav` recordings.
   - Use neutral documentation placeholders (`192.168.1.50`, `<headend-lan-ip>`, `Seattle, Washington`, `Portland, Oregon`, `Tokyo`).
2. **Firmware Head-End URL Validation (`firmware/main/headend.c` → `safe_url`):**
   - Firmware strictly requires `http://<RFC1918-IPv4>:8765`. Hostnames (`.local`), HTTPS, paths, and non-`8765` ports are rejected on-device.
3. **15-Second Spoken Reply Cap:**
   - Firmware rejects pages larger than `480,000` bytes (`15.0s` of 16 kHz 16-bit mono PCM), and `Fleet.playback_pcm()` enforces `1,600..240,000` samples. Keep spoken responses concise (`~195`–`320` characters max).
4. **360×360 Round Display & Touch Geometry (`firmware/main/ui.c`, `space.c`, `touch.c`):**
   - Curved volume/brightness rails occupy `x < 78` and `x >= 282` (`y = 85..275`), the bottom settings arrow occupies `x = 140..220, y = 297..359`, and the inner perimeter of the LLM edge glow ring is `r = 155` from `(180, 180)`. Keep center interactive cards within `x = 82..278, y <= 292`.
   - `touch.c` drops intermediate drag samples and `ui.c` acts once per discrete `event->pressed` edge (`90 ms` debounce). Use explicit tap/2-tap confirmation buttons (`[X]` → `[OK?]`) rather than swipe or drag gestures.
5. **Firmware Compiler Warnings Are Errors:**
   - ESP-IDF compiles `firmware/main/` with `-Wall -Werror=all` (including `-Werror=format-truncation=`). Size `snprintf` destination buffers generously when formatting `%u` / `%d` alongside bounded strings.
6. **Home Assistant Security & Safety (`headend/home_assistant.py`, `ha_devices.py`):**
   - Never expose arbitrary Home Assistant service calls to the browser or voice parser; every action must resolve through `controls()` and `action_payload()`.
   - Never echo `SecretStr` tokens in validation errors, logs, or HTTP responses, and **never** automatically retry a `POST /api/services/...` call after a timeout.
7. **Wake-Word Weight Licensing:**
   - Original ADAM code is `GPL-3.0-or-later`, while the pre-trained `Hey Adam` `.tflite` weights were trained with upstream datasets carrying **noncommercial** terms (see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). Preserve attribution and licensing notices when touching `vendor/` or `training/`.
