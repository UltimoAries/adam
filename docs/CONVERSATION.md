# Local conversation with a preferred GPU computer

ADAM's Raspberry Pi head-end answers general voice questions through a larger
Ollama model on a preferred LAN GPU host (`http://<gpu-host-ip>:11434`, for
example `http://192.168.1.20:11434`). If that server is unreachable or exceeds
its deadline, the same question falls back to a small local model on the Pi.
Typical configurations pair `qwen3.5:9b` on an NVIDIA RTX-class GPU host with
`qwen3.5:0.8b` on the Pi, addressed through the stable alias `adam:latest`.
Use the **ADAM GPU Control** desktop shortcut on the Windows GPU host to select
the underlying installed model, start/stop each service, or view CPU/GPU load and
VRAM use; its saved selection survives service and system restarts.
See [desktop controls](../deploy/model-host/README.md#desktop-control-window).

Firmware v25–v27 displays a persistent glowing home-screen edge ring showing the
effective conversation route even while idle: green when the configured GPU model
is loaded and eligible, or soft yellow when the Pi fallback is ready instead
(v27 softens the inner/outer halo opacity by 25%). A separate five-second
background metadata check keeps device polls fast and makes no inference requests.
The ring honors failure cooldowns and hides when conversation is disabled,
unavailable, or disconnected. See [screen behavior](SPACE_PANEL.md).

Configure routing on the **Model services** page at `http://<headend-ip>:8765/models`
(or `/models` on localhost). Settings persist in the head-end database and apply
to new questions. The dashboard shows which provider answered and when it will
retry the preferred GPU host. The text preview does not play audio or create a
training archive recording.

## Voice behavior

- A new completed wake interaction can generate one short spoken answer.
  Manual Listen and diagnostic retranscriptions remain recording/review actions.
- Weather questions go to the existing weather service. Matched home commands
  go to Home Assistant with its capability, expiry and replay checks.
  Polite prefixes such as "can you" and "could you please" are accepted.
- The conversational model receives only its system instructions and the current
  question. It has no Home Assistant token, device inventory or action tools.
  Unmatched imperative device commands receive a naming/settings explanation.
- Answers are limited to 200 spoken characters and use the selected default
  speech route. Each question is independent; conversation history and live web
  search are not implemented in this version.
- Original recordings and transcripts continue to use the training archive.
  Its review labels remain independent of transcripts and model answers.

The preferred GPU request has a six-second total deadline, including the streamed
body. On failure, the Pi answers locally and skips the preferred server for
30 seconds before retrying on a later question. Local generation has a 20-second
deadline; if both models fail, ADAM gives a short unavailable response. The
bounded queue does not run transcription or home control inside the model worker.

## Hosts and storage

The Windows GPU host runs Ollama 0.40.13 natively under `C:\ADAM_VOICE`:
versioned binaries, models, scripts, configuration, temporary files and logs.
The `ADAM-Ollama` startup task runs as `SYSTEM`, restarts on process failure, and
warms the selected model after startup. Its API allows the Pi and authorized LAN
hosts through a scoped Windows Firewall rule. The runtime archive is verified
against the official release SHA-256. See
[Windows deployment details](../deploy/model-host/README.md).

On the Raspberry Pi, add `compose.llm.yaml` to the normal Compose stack and pull
the local fallback model (see [Raspberry Pi deployment](../deploy/pi5/README.md)):

```sh
cd <repo-root>/deploy/pi5
docker compose -f compose.yaml -f compose.llm.yaml up -d
docker compose -f compose.yaml -f compose.llm.yaml exec ollama ollama pull qwen3.5:0.8b
```

The optional Ollama container binds only to `127.0.0.1:11434`, stores model files
in `headend/models/llm` and runtime state in `headend/data/ollama`, and rotates
Docker logs. Requests use two CPU threads and a 1,024-token context; the GPU host
uses a 4,096-token context. Cloud model routing is disabled on both hosts.

On Raspberry Pi kernels without cgroup memory accounting enabled, the 3 GiB
Compose memory limit is not enforced by the kernel; do not rely on it to guard
against oversized models. The `qwen3.5:0.8b` fallback model runs with several GiB
of headroom on an 8 GB Pi 5 without undervoltage or thermal throttling. The
10 GiB training archive cap and free-disk reserve remain active.

## Validation and operation

The GPU host can also serve preferred Whisper `large-v3-turbo` recognition via
Speaches, with CPU `base.en` on the Pi as fallback. Piper TTS replies stay on the
Pi. Recognition and conversation routes are independent, so either GPU service
can be stopped separately. The desktop control app disables automatic startup on
**Stop** and restores it on **Start**; closing the window leaves running services
untouched.

Firmware **v23+** reduces the listening chime by 20 dB (`peak: 1000`). Earlier
recordings with the louder chime showed clipped samples around the trigger; v23+
recordings eliminated chime clipping and transcribed continuous wake questions
such as *"Hey Adam, why is the sky blue?"* cleanly while preserving microphone
gain, spoken-reply volume and the `hey_adam-20261030-c60f0456` wake model.

Desktop Stop/Start transitions were verified for both GPU services while the Pi
continued conversation and transcription via local fallbacks. Retranscription
checks verified stored archive WAV hashes without executing Home Assistant
commands or altering review labels (`tools/check_speech_routing.py` exercises
archive retranscription through the authenticated review endpoint and cleans up
its temporary session when finished).

Head-end unit tests cover provider failure and cooldown recovery, total request
deadlines, saved settings, manual/wake separation, one targeted spoken page per
session, Home Assistant priority, malformed/tool responses, and bounded speech
output. Live failover tests measured ~0.5–1.7 seconds on a warm GPU host, ~3.9
seconds on a warm Pi fallback (~11 seconds cold), and automatic recovery once the
GPU service returned.

To verify live conversation routing and archive integrity on a deployed head-end:

```sh
python3 tools/check_conversation_runtime.py --expect-provider remote
```

Disabling conversation on `/models` leaves deterministic weather queries and Home
Assistant voice commands active.
