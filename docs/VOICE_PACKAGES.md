# Local voice packages, version 1

The head-end's **Voices** page at `/voices` (for example
<http://127.0.0.1:8765/voices>) imports, previews and selects local speech
voices. Typed pages, weather replies, Home Assistant confirmations and
conversational answers use the selected default voice; the paging form and
weather settings can override it. Recorded pages retain the recording's own
audio. The firmware's short 120 ms listening chime is a separate embedded asset
(see [LISTENING_CUE.md](LISTENING_CUE.md)); selecting a TTS voice does not
replace that chime.

## Supported model family

Version 1 accepts **Piper VITS ONNX models with espeak phonemization** and their
matching Piper JSON configuration. The runtime uses `piper-tts` with ONNX
Runtime's CPU execution provider. English profiles are supported: `en-US`,
`en-GB`, `en-AU`, `en-CA`. An `.onnx` extension alone does not establish
compatibility; the importer verifies the Piper graph interface and synthesizes a
live probe sentence before accepting an archive.

The starter voice is **Basic English / Kristin**, `basic-english@1.0.0`.
Its 60.6 MiB model and matching configuration are pinned to repository revision
`5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`; hashes and provenance are recorded in
[`headend/voice-models.json`](../headend/voice-models.json). The included model card
identifies LibriVox public-domain source recordings, retaining the upstream MIT
notice and dataset provenance alongside Piper's GPL-3.0 runtime notice.
See the [pinned model source](https://huggingface.co/rhasspy/piper-voices/tree/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/kristin/medium)
and [Piper project](https://github.com/OHF-Voice/piper1-gpl).

MP3/WAV reference clips, PyTorch checkpoints, XTTS/Qwen/Chatterbox weights and
arbitrary ONNX graphs cannot be imported as Piper voice packages.

## Install the starter voice

From the workspace root, with Python 3.12 installed:

```powershell
py -3.12 -m venv headend/.venv
./headend/.venv/Scripts/python.exe -m pip install -r headend/requirements-lock.txt
./headend/.venv/Scripts/python.exe -m tools.fetch_voice_model
./tools/start_headend.ps1 -Lan -OpenBrowser
```

The fetch step downloads only pinned model files and verifies their SHA-256
hashes. Once installed, preview and speech synthesis require no cloud service or
API key, and no voice model is downloaded automatically during a speech request.
English speech-to-text bootstrap instructions are in
[HEADEND_TEST_UI.md](HEADEND_TEST_UI.md).

## ZIP layout and manifest

Use a flat ZIP with these **case-sensitive root filenames**, without a parent
folder:

```text
voice.json          # UTF-8 manifest, schema_version 1
model.onnx          # Exported, self-contained Piper inference model
model.onnx.json     # Configuration produced by that model's training run
LICENSE.txt         # Full license terms and required notices, UTF-8
MODEL_CARD.md       # Optional provenance, training/evaluation notes
README.md           # Optional usage notes
```

The **Download manifest template** link on `/voices` provides a starter manifest,
and [`headend/voice-package.schema.json`](../headend/voice-package.schema.json)
defines the formal JSON Schema. Replace placeholder hashes with exact lowercase
SHA-256 digests (`tools.package_voice` computes these automatically):

```json
{
  "schema_version": 1,
  "id": "adam-character",
  "version": "1.0.0",
  "name": "Adam character",
  "description": "A playful English announcement voice.",
  "author": "Your name",
  "engine": "piper-onnx",
  "language": "en-US",
  "model": {"file": "model.onnx", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX_DIGITS"},
  "config": {"file": "model.onnx.json", "sha256": "REPLACE_WITH_64_LOWERCASE_HEX_DIGITS"},
  "license": {
    "name": "Your actual model license",
    "file": "LICENSE.txt",
    "attribution": "Required model, dataset and checkpoint attribution",
    "source_url": "https://example.org/your-model-card"
  },
  "settings": {
    "speaker_id": 0,
    "length_scale": 1.0,
    "noise_scale": 0.667,
    "noise_w_scale": 0.8
  }
}
```

Keep `id` stable across updates (`3..48` lowercase letters, digits, underscores
or hyphens, starting with a letter) and increment `major.minor.patch` whenever
weights, config or synthesis settings change. Installed `id@version` packages are
immutable, and unknown manifest keys are rejected.

`speaker_id` must exist in the model (`0` for single-speaker exports). Larger
`length_scale` values slow speech (`0.5..2.0`); `noise_scale` and `noise_w_scale`
(`0.0..1.0`) adjust synthesis variation. Supported native sample rates are
`16000`, `22050`, `24000`, `44100` and `48000` Hz, with standard Piper inputs
(`input`, `input_lengths`, `scales`, optional `sid`) and a single float audio
output.

## Package an existing compatible model

Use the head-end virtual environment so packaging runs against the exact runtime
dependencies:

```powershell
./headend/.venv/Scripts/python.exe -m tools.package_voice `
  --model C:/Voices/adam.onnx `
  --config C:/Voices/adam.onnx.json `
  --id adam-character --version 1.0.0 --name "Adam character" `
  --author "Your name" --language en-US `
  --license-file C:/Voices/LICENSE.txt --license-name "Actual model license" `
  --attribution "Required attribution for your voice and training sources" `
  --model-card C:/Voices/MODEL_CARD.md `
  --probe --output C:/Voices/adam-character-1.0.0.zip
```

`--config` defaults to `<model>.json`; `--model-card` and `--probe` are optional
(server import always runs a live probe). In `/voices`, choose **Choose a voice
ZIP → Import & validate → Generate preview → Play → Use as default**. Switch to
another default before deleting an imported voice; the starter voice is
protected.

## Training a custom Piper voice

Record clean, single-speaker lossless audio with explicit speaker consent and
licensing that covers the intended model. Follow the official
[Piper training and ONNX export guide](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/TRAINING.md)
in a **separate training environment** using UTF-8 pipe-delimited metadata:

```text
take0001.wav|Dinner is ready.
take0002.wav|Huh? Oh, right. I'm listening.
```

Fine-tune or train a medium English espeak model (typically 22,050 Hz), export
the selected checkpoint, and pair it with its generated JSON config:

```sh
python3 -m piper.train.export_onnx --checkpoint selected.ckpt --output-file adam.onnx
```

## Persistent worker runtime and resource boundaries

- **Archive & library bounds:** ZIP up to 160 MiB; ONNX model up to 150 MiB;
  manifest up to 32 KiB; config and docs up to 64 KiB each; up to 16 installed
  profiles and 1 GiB of custom voice packages. Imports require administrator
  authentication.
- **Format safety:** Nested paths, duplicate entries, symlinks, encrypted ZIP
  entries, scripts, external ONNX tensor files, local ONNX functions and custom
  operator domains are rejected.
- **Persistent warm subprocess (`PiperRuntime`):** `headend/voices.py` and
  `headend/voice_worker.py` run synthesis inside a persistent warm worker
  subprocess (`PiperRuntime`) that keeps the active ONNX voice loaded in memory
  between requests. The head-end warms the selected default voice at startup,
  reducing typical reply synthesis from ~3.7 s cold to ~0.4 s warm (see
  [RESPONSE_LATENCY.md](RESPONSE_LATENCY.md)). Package checksums and limits are
  still verified on each call; switching voices loads the newly requested model,
  and a crashed or timed-out worker is automatically replaced on the next
  request.
- **Resource isolation:** The worker enforces a 35-second per-request timeout,
  two CPU threads, and a process memory cap (1.5 GiB commit limit on Windows;
  3 GiB address-space limit on Linux). Treat this as operational resource
  isolation rather than a hostile-code sandbox, and only import models from
  trusted sources.
- **Output contract:** Audio is resampled on the head-end host to 16 kHz mono
  PCM16, bounded to 200 input characters and 15 seconds of speech, and queued
  only after synthesis completes cleanly.

Back up `headend/data/voices/` for imported packages and default selection, and
`headend/models/voices/` for the pinned starter model. Voice weights live on the
head-end host and consume zero ESP32 flash or PSRAM.
