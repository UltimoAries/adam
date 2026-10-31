# Hey Adam wake-word model

## Current embedded model: `hey_adam-20261030-c60f0456`

Firmware v27 embeds the `hey_adam-20261030-c60f0456` microWakeWord v2 model
(first integrated in Firmware v18), stored in `firmware/main/wake_model.tflite`
with its matching `firmware/main/wake_model.json` manifest and generated
`firmware/main/wake_model_config.h`. Stock **Hey Jarvis** remains preserved under
`vendor/wake/` for reference and rollback.

- **Model bytes:** `60,968`
- **SHA-256:** `c60f04560e1dadad6ef907552c5a7b2af34171faadd79016c50319f51058d902`
- **Streaming interface:** `int8 [1, 3, 40]` input, `uint8 [1, 1]` output, 10 ms feature step
- **Operating settings:** probability cutoff `0.97`, sliding window `5`, tensor arena `65,536` bytes, development flag `1`

Static and on-device checks verified exact byte embedding, operator registration,
tensor shapes, `ready=1`, `feeding=1`, zero wake queue overflows and clean
head-end wake uploads while preserving NVS Wi-Fi, pairing, volume and brightness
settings. Controlled multi-speaker recall and multi-hour household false-accept
measurements remain separate from integration verification; the earlier
`hey_adam-pilot-v2` synthetic metrics below describe the prior checkpoint rather
than `hey_adam-20261030-c60f0456`.

## Earlier pilot checkpoint (`hey_adam-pilot-v2`)

Prior to v18, the firmware tested the `hey_adam-pilot-v2` MixedNet model trained
from the pinned microWakeWord revision `4665173cd35f1cff9a61e06fc427f124766c488e`
(9,500 initial steps plus 500 continuation steps). Tracked run parameters,
invocation metadata, model report and upstream ROC curves are preserved in
`training/hey_adam/pilot-v2/`.

| Item | Value |
| --- | --- |
| Architecture | Quantized streaming microWakeWord v2, MixedNet |
| Wake phrase | Hey Adam (US English, `AD-um`) |
| Earlier pilot ID | `hey_adam-pilot-v2` |
| Model bytes | `60,968` |
| Earlier pilot SHA-256 | `d3fe60876e2ceae56dcbbc7c13baa01c7616125422d12f67a90d9eccee1015bd` |
| Input / output | `int8 [1, 3, 40]` → `uint8 [1, 1]`, 10 ms step |
| Cutoff / window / arena | `0.97` / `5` outputs / `65,536` bytes |

### Earlier pilot evaluation notes

- Passed desktop tensor/dtype verification and 100 continuous streaming steps.
- At cutoff `0.97`, detected 152/200 held-out synthetic positives (76% recall)
  with 2/120 synthetic near-miss false accepts.
- Upstream background ROC at cutoff `0.99` reported 1.875 false accepts/hour and
  27% false rejection.
- Built cleanly with ESP-IDF 5.5.1, `esp-tflite-micro` 1.4.0 and `esp-nn` 1.4.1,
  leaving 56% of the 3 MiB factory application partition free.

Note that upstream microWakeWord training recipes include datasets with
noncommercial terms; review dataset licenses individually before any commercial
use.

## Building and verifying on hardware

`tools/idf.ps1` expects ESP-IDF 5.5.1 at `C:\Espressif\esp-idf` on Windows (or
activate your local ESP-IDF 5.5.1 environment directly and run
`idf.py -DIDF_TARGET=esp32s3 build` inside `firmware/`).

After flashing, verify wake runner health over USB:

```powershell
python tools/control.py wake status
python tools/control.py headend status
python tools/control.py resources
```

Confirm `model=hey_adam-20261030-c60f0456` (or your custom `--id`), `ready=1`,
`feeding=1`, active head-end polling, and zero queue/input errors.

## Rollback to stock Hey Jarvis and further evaluation

To switch a checkout back to the vendored stock **Hey Jarvis** model for A/B
comparison or rollback, run from the repository root:

```powershell
py -3.12 tools/install_wake_model.py vendor/wake/micro_wake_models/models/v2/hey_jarvis.json --id hey_jarvis-dev-v2 --development
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
```

For formal wake-word promotion, evaluate labelled attempts per speaker at 1, 2
and 3 metres alongside near-miss phrases and multi-hour background speech/TV
sessions, using the [training archive](TRAINING_ARCHIVE.md) to collect and review
real household recordings.
