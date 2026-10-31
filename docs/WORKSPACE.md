# Workspace layout and local state

Run commands from the repository root. Source directories keep stable paths so
the head-end service, ESP-IDF firmware build, deployment scripts, and helper
tools work from a fresh checkout.

## Tracked in Git

- `LICENSE` and `THIRD_PARTY_NOTICES.md`: repository license and third-party attributions.
- `firmware/`: ESP-IDF 5.5.1 application source, partition table, `sdkconfig.defaults`,
  and embedded wake/chime assets (`hey_adam-20261030-c60f0456` and the 120 ms listening chime).
- `headend/`: FastAPI head-end server, speech/query/Home Assistant/LLM modules,
  unit tests, lock files, schemas, and bundled dashboard assets (`headend/web/dist`).
- `deploy/`: deployment configurations for Raspberry Pi (`deploy/pi5/README.md`)
  and the optional Windows GPU model host (`deploy/model-host/README.md`).
- `tools/`: developer, provisioning, and hardware-validation scripts (see [`tools/README.md`](../tools/README.md)).
- `training/hey_adam/`: reproducible microWakeWord benchmark containers, pinned
  upstream references, and pilot run metadata.
- `vendor/`: pinned Waveshare V2 hardware references and stock `Hey Jarvis`
  rollback wake-word files with their upstream licenses (`.gitattributes` preserves exact bytes).
- `docs/` and `artifacts/README.md`: architecture, operation, and historical bring-up guides.

## Local only (gitignored)

- `headend/data/`: accounts, password hashes, paired device keys, pages, settings,
  and the local `training_archive/` database/audio.
- `headend/recordings/` and `artifacts/*` (except `artifacts/README.md`): captured
  audio, transcripts, screenshots, serial logs, and local test outputs.
- `headend/models/`: downloaded STT, Piper TTS, and local Ollama model weights.
- `headend/.venv/`, `headend/.browser-test/`: Python virtual environments and browser test profiles.
- `firmware/build/`, `firmware/managed_components/`, `firmware/sdkconfig`:
  generated ESP-IDF build outputs (`firmware/sdkconfig.defaults` remains tracked).
- `backups/`: local firmware rollback images and pre-flash snapshots.
- `training/hey_adam/results/`, `export/`, `models/`, datasets, and checkpoints.
- `.env*`, local secrets, tokens, and per-machine editor settings.

A fresh clone creates its own administrator account and downloads pinned model
weights using the setup commands in [`README.md`](../README.md) and [`deploy/pi5/README.md`](../deploy/pi5/README.md).
Diagnostic reports mentioned in historical notes are local test outputs rather
than distributed repository files.

## Before committing changes

Use `git status --short` and `git diff --cached --stat` to verify staged files.
Never force-add excluded recordings, SQLite databases, logs, credentials, IP-specific
environment files, or model caches. Keep runtime secrets and household settings in
the ignored local directories, and back up `headend/data/` separately from Git.
