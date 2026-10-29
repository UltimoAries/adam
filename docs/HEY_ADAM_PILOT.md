# Hey Adam development model

This branch embeds the trained `hey_adam-pilot-v2` microWakeWord model in the
existing ESP32-S3 firmware. Detection remains local on the ESP32; the head-end
receives the existing wake-triggered upload with this model ID. Wi-Fi pairing,
the upload protocol, pre-roll, silence endpoint, chime and listening UI use the
existing implementation.

## Artifact and settings

| Item | Value |
| --- | --- |
| Model | Quantized streaming microWakeWord v2, MixedNet |
| Wake phrase | Hey Adam, US English, AD-um |
| Model ID | `hey_adam-pilot-v2` |
| Development flag | `1` |
| Model bytes | 60,968 |
| Model SHA-256 | `d3fe60876e2ceae56dcbbc7c13baa01c7616125422d12f67a90d9eccee1015bd` |
| Input | int8, `[1, 3, 40]` |
| Output | uint8, `[1, 1]` |
| Feature step | 10 ms |
| Probability cutoff | 0.97 |
| Sliding window | Five model outputs |
| Tensor arena | 65,536 bytes |

The matching bytes, installed manifest and generated header are under
`firmware/main/`. The files were generated using the existing
`tools/install_wake_model.py` with `--development`. The training run uses the
pinned microWakeWord revision `4665173cd35f1cff9a61e06fc427f124766c488e`.
The initial 9,500-step checkpoint was continued for 500 steps with a fresh Adam
optimizer. Training parameters, invocation, model report and upstream background
ROC are under `training/hey_adam/pilot-v2/`. Datasets/checkpoints remain local.

## Validation and limits

- Export passed desktop tensor/dtype checks and 100 continuous inference calls.
- At cutoff 0.97, 152/200 held-out synthetic positives detected (76% recall),
  with 2/120 near-miss false accepts.
- The upstream background ROC at cutoff 0.99 reported 1.875 false accepts/hour
  and 27% false rejection. It is a different operating point from this manifest.
- The model's 13 operators are all registered by the existing runner. The
  feature step, input shape and output type match its interface.
- Firmware built with ESP-IDF 5.5.1, esp-tflite-micro 1.4.0 and esp-nn 1.4.1.
  The 1,383,696-byte application fits the 3 MiB factory partition with 56% free.
- An app-only flash on the Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2 retained
  existing settings. Device status confirmed `ready=1`, `feeding=1`, the new
  model ID and head-end `READY`.
- An informal spoken weather-request test was operator-confirmed as working
  well. Follow-up counters showed five completed uploads and three delivered
  reply pages, zero head-end errors, zero queue overflows and zero input gaps.
  Those are cumulative counters, not a labelled recall test.

This is an experimental household prototype for noncommercial personal use.
Its training recipe includes datasets with noncommercial restrictions. The
short on-device test establishes a working integration; it does not establish
acceptable household recall or accidental-trigger rates at 1-3 metres.
Keep the development flag until controlled evaluation is complete.

## Build and check

Use the repository's ESP-IDF 5.5.1 build setup. Its Windows `tools/idf.ps1`
assumes the framework is installed at `C:\Espressif\esp-idf`; activate your
matching actual ESP-IDF installation if its path differs, then build from
`firmware/` with `idf.py -DIDF_TARGET=esp32s3 build`.

Before flashing, back up the existing application and verify the device's
partition layout. The tested update wrote only the factory application at
`0x10000`, preserving bootloader, partition table and NVS. Reconfirm offsets
for another device or partition layout.

After flashing, use `tools/control.py wake status`, `headend status`, and
`resources` with the correct port. Confirm model ID, ready status, successful
head-end polling and no queue/input errors. Then test normal continuous
requests and verify the head-end responds.

## Rollback and next evaluation

Keep an exact application backup before deployment. It is the quickest rollback
for an existing unit. The stock Jarvis model also remains vendored and can be
reinstalled from the repository root:

```powershell
py -3.12 tools/install_wake_model.py vendor/wake/micro_wake_models/models/v2/hey_jarvis.json --id hey_jarvis-dev-v2 --development
```

Rebuild and flash to activate it. For Hey Adam acceptance, collect labelled
attempts per household speaker at 1, 2 and 3 metres, near-misses, and several
hours of ordinary conversation/TV/noise. Report misses per condition and
accidental triggers per hour. Tune on separate development sessions and reserve
fresh sessions for final evaluation.
