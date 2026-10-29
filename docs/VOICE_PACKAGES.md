# Local voice packages, version 1

The head-end's **Voices** page at <http://127.0.0.1:8765/voices> imports,
previews and selects local speech voices. Typed pages use the selected default;
the paging form can override it for one message. Recorded pages retain the
recording's voice. The firmware's short listening cue is a separate static
asset for now; selecting a TTS voice does not replace that cue.

## Supported model family

Version 1 accepts **Piper VITS ONNX models with espeak phonemization** and their
matching Piper JSON configuration. The installed runtime is `piper-tts==1.8.0`
with ONNX Runtime's CPU provider. English profiles are supported: `en-US`,
`en-GB`, `en-AU`, `en-CA`. An ONNX extension alone does not establish compatibility.
The importer verifies the Piper interface and generates a real test sentence.

The initial voice is **Basic English / Kristin**, `basic-english@1.0.0`.
Its 60.6 MiB model and matching configuration are pinned to repository revision
`5041a7a49ae5b3a0f30e6837769eb03fdbb231bd`; hashes/provenance are in
[`headend/voice-models.json`](../headend/voice-models.json). The included model card
identifies LibriVox public-domain source recordings. The package retains the
repository's MIT notice and dataset provenance. Piper's runtime is GPL-3.0;
runtime, model, dataset and checkpoint licenses are separate matters.
See the [pinned model source](https://huggingface.co/rhasspy/piper-voices/tree/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/en/en_US/kristin/medium)
and [Piper project](https://github.com/OHF-Voice/piper1-gpl).

This is a working general-purpose starter voice. It is not a newly trained
cartoon character. MP3/WAV reference clips, PyTorch checkpoints, Qwen/XTTS/
Chatterbox models and arbitrary ONNX graphs cannot be imported as Piper voices.
Those model families would need their own engine adapter and package contract.

## Install the starter voice

From the workspace root, with Python 3.12 installed:

```powershell
py -3.12 -m venv headend/.venv
./headend/.venv/Scripts/python.exe -m pip install -r headend/requirements-lock.txt
./headend/.venv/Scripts/python.exe -m tools.fetch_voice_model
./tools/start_headend.ps1 -Lan -OpenBrowser
```

The fetch step downloads only pinned model files and verifies their hashes.
Once installed, preview and paging need no cloud service or API key. No voice
model is downloaded automatically during a speech request. English recognition
has its own model/bootstrap instructions in [HEADEND_TEST_UI.md](HEADEND_TEST_UI.md).

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

The **Download manifest template** link on the Voices page gives the current
format. The formal schema is [`headend/voice-package.schema.json`](../headend/voice-package.schema.json).
Example values below are descriptive; the zero hashes must be replaced by
the SHA-256 of the exact file bytes. The packaging command does this for you.

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

Keep `id` stable across updates: 3–48 lowercase letters/digits/underscore/hyphen,
starting with a letter. Increment the numeric `major.minor.patch` version for
changed weights, configuration or settings. Identity is `id@version`; installed
versions cannot be overwritten. A lowercase prerelease suffix is supported.
Unknown manifest fields are rejected so mistakes do not silently alter behavior.

`speaker_id` must exist in the model; use zero for a single speaker. Larger
`length_scale` slows speech (allowed 0.5–2). Noise settings range from 0–1 and
control variation, not a reliable emotion/style selector. A model must learn the
desired voice character; these sliders cannot supply an acting performance.

The configuration must preserve the export's phoneme map, symbol count, espeak
voice, language and speaker information. Supported model sample rates are
16,000, 22,050, 24,000, 44,100 and 48,000 Hz. The worker checks the standard
Piper inputs (`input`, `input_lengths`, `scales`, optional `sid`) and one float
audio output. Models must embed all weights and use standard ONNX operators.

## Package an existing compatible model

Use the head-end environment so validation uses the same dependencies:

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

`--config` defaults to the model filename plus `.json`. `--model-card` and
`--probe` are optional; import always runs a probe regardless. The tool computes
checksums, validates metadata/configuration, uses canonical filenames, and
refuses to overwrite an existing output ZIP. It does not train or convert an
incompatible model.

In the signed-in Voices page: **Choose a voice ZIP → Import & validate →
Generate preview → Play → Use as default**. Import leaves the current default
selected. A rejected or timed-out probe installs nothing. Switch back to the
included voice to undo a selection. Default selection persists across restarts.
Choose another default before removing an imported voice; the included voice
is protected. Already queued pages keep their generated audio.

## Creating our character voice

Use recordings made for this project, with the speaker's permission and terms
that cover the intended voice model. Record a consistent character, microphone
position and room. Keep clean lossless masters and accurate transcripts; avoid
music, overlapping speakers, clipping, echo and heavy processing. Split by
utterance, remove unusable takes, and reserve recordings/text for evaluation.
Start with a short pilot to agree on the character before a larger session;
a few entertaining clips are useful references, not a promise of a robust TTS
model. Evaluate names, numbers, short acknowledgements and unfamiliar sentences.

For the first custom model, follow the official
[Piper training and ONNX export guide](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/TRAINING.md)
in a **separate training environment**. Its default single-speaker input is
UTF-8, pipe-delimited metadata with audio filenames relative to an audio folder:

```text
take0001.wav|Dinner is ready.
take0002.wav|Huh? Oh, right. I'm listening.
```

Train/fine-tune a medium English model with espeak phonemes, normally at
22,050 Hz. Retain the config written by training. Piper recommends starting
from a compatible medium checkpoint; check its license as well. The training
guide documents the separate PyTorch dependencies and GPU requirements.
Training is not installed or launched by the head-end.

Export the selected checkpoint in that training environment:

```sh
python3 -m piper.train.export_onnx --checkpoint selected.ckpt --output-file adam.onnx
```

Copy the matching training config to `adam.onnx.json`; then use the packaging
command above. Keep the dataset, source checkpoint, training recipe/revision,
evaluation results and consent/license notes with your model card. Test held-out
phrases on laptop and box before activating a new version. Native expression
quality may warrant a different engine later; the voice identity/version and
paging interface can remain separate from that engine's implementation.

## Resource and trust boundaries

- ZIP: at most 160 MiB; model: 150 MiB. Manifest: 32 KiB; config and each
  document: 64 KiB. Library: 16 profiles including the starter, 1 GiB of custom
  package contents. Imports require administrator login.
- No nested paths, duplicate names, symlinks, encrypted entries, scripts,
  external ONNX tensors, local ONNX functions or custom operator domains.
  SHA-256 detects mismatched bytes; it does not prove a publisher's identity.
- Inference runs in a separate process with a 35-second deadline, two CPU
  threads and a Windows 1.5 GiB process commit limit (Linux address-space limit
  3 GiB). One import or synthesis runs at a time; device networking continues.
  This is resource isolation, **not a full security sandbox**. Install models
  from sources you trust and keep native inference dependencies updated.
- Speech is converted on the laptop to mono PCM16 at 16 kHz, at most 15 seconds
  and 200 input characters. The ESP32 receives the existing paging format; it
  stores no Piper weights. Generated speech is queued only after synthesis
  succeeds, so invalid output cannot become a page.
- Starter preview measured 3.7 seconds end-to-end for 1.88 seconds of speech
  in the API check (an earlier cold run took about seven seconds), including
  worker startup/model validation. Preview
  reports actual elapsed time. This initial implementation favors bounded,
  isolated jobs over low-latency conversational speech; a persistent worker
  and longer response chunking are future improvements.

Back up `headend/data/voices/` for imported profiles/default selection and
`headend/models/voices/` for the starter. Voice files live on the laptop, so
additional profiles consume laptop disk, not device flash or PSRAM.
