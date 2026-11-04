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
See [desktop controls](../deploy/model-host/README.md#desktop-control-window-behavior).

Current firmware displays a persistent glowing home-screen edge ring showing the
effective conversation route even while idle: green when the configured GPU model
is loaded and eligible, or soft yellow when the Pi fallback is ready instead
(v27 softened the inner/outer halo opacity by 25%). A separate five-second
background metadata check keeps device polls fast and makes no inference requests.
The ring honors failure cooldowns and hides when conversation is disabled,
unavailable, or disconnected. See [screen behavior](SPACE_PANEL.md).

Configure routing on the **Model services** page at `http://<headend-ip>:8765/models`
(or `/models` on localhost). Settings persist in the head-end database and apply
to new questions. The dashboard shows which provider answered and when it will
retry the preferred GPU host. The text preview does not play audio or create a
training archive recording.

The Model services page lists installed GPU models in separate dropdowns for
speech recognition (Speaches `/v1/models`) and conversation (Ollama `/api/tags`).
Use **Refresh available models** after installing a model or changing the GPU
computer address. Discovery does not download, load, select, or save a model.
Choose both models and use **Save processing settings** to apply them together.
Saved selections remain available when a service is offline; manual model entry
is available under the dropdown for advanced setups.

**Follow GPU Control selection (adam:latest)** retains the desktop utility's
model selection behavior. Selecting another Ollama name pins ADAM to that model
without changing the GPU utility's alias. A large or unloaded model can take
longer than the remote timeout and fall back to the Pi on its first request.
One **Where to run** preference covers transcription and conversation, using one
GPU computer address without a port. Advanced settings contain the service ports
(8000 for speech, 11434 for conversation), timeouts and optional speech API key.
Piper speaking voice always stays on the Pi. Local fallback model settings are
preserved when changing GPU models. Settings for both routes commit together.
The authenticated `/api/processing/settings` API supports this combined form;
the older per-service APIs remain available for existing integrations.

## Voice behavior

- A new completed wake interaction or authorized follow-up can generate one
  short spoken answer. Signed-in text commands use the same handlers, with
  optional speaker playback.
  Manual Listen and diagnostic retranscriptions remain recording/review actions.
- Weather questions go to the existing weather service. Matched home commands
  go to Home Assistant with its capability, expiry and replay checks.
  Polite prefixes such as "can you", "can we", and "could you please" are accepted.
- The conversational model receives its system instructions, current question,
  and up to three consecutive successful model exchanges from the same scoped
  conversation. It has no Home Assistant token, device inventory or action tools.
  Unmatched imperative device commands receive a naming/settings explanation.
- Answers are limited to 200 spoken characters and use the selected default
  speech route. Context expires after 90 seconds or an end phrase; unrelated
  topics replace actionable context. Live web search is not implemented. See
  [Follow-up conversation](FOLLOWUP_CONVERSATION.md) for listening windows,
  topic memory, and the paused experimental interruption investigation.
- Original recordings and transcripts continue to use the training archive.
  Its review labels remain independent of transcripts and model answers.

The preferred GPU request has a six-second total deadline, including the streamed
body. On failure, the Pi answers locally and skips the preferred server for
30 seconds before retrying on a later question. Transcription and conversation
share this cooldown, so a failed GPU request does not cause a second GPU timeout
in the same interaction. Local generation has a 20-second
deadline; if both models fail, ADAM gives a short unavailable response. The
bounded queue does not run transcription or home control inside the model worker.

## Home request preview

**Model services → Preview home requests** is an explicit, separate evaluation
path. It sends a bounded catalog of enabled device identities, names, and
controls to the configured model and validates a structured proposal. It never
executes actions, reads live HA states, speaks a reply, or changes live dialogue
context. There is no automatic model fallback for live home commands.

Previews allow 30 seconds per provider, up to 60 seconds with Pi fallback, and
use an 8,192-token context. Normal conversation keeps its saved timeouts and
context limits. General conversation can be disabled while using the explicit
preview. See [Home Assistant](HOME_ASSISTANT.md) for limits and the preview API.

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
Pi. A failure in either GPU service makes both routes prefer their Pi fallbacks
during the shared cooldown. The desktop control app disables automatic startup on
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
