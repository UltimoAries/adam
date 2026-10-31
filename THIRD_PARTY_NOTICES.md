# Third-party notices

Original ADAM code is licensed under the GNU General Public License v3.0 or
later (`GPL-3.0-or-later`); see [LICENSE](LICENSE). Included upstream material
retains the notices and terms supplied with that material.

| Material | Included license / provenance |
| --- | --- |
| Waveshare board samples and ST77916 LCD driver | [Apache-2.0](vendor/LICENSE); [source tree](vendor/waveshare-tree.json) at commit `8ead4a96bf3a278fc4ebd8ef4768657e17fa2880` (with local QSPI modifications noted in source) |
| Espressif ES7210 reference | Apache-2.0 SPDX header in [source](vendor/espressif/es7210-native.c) |
| micro_wake_word embedded runner | [GPL-3.0](vendor/wake/micro_wake_word/LICENSE.md) |
| optional-bare (`esphome/core/optional.h`) | MIT (Copyright 2017 Martin Moene; header in [source](vendor/wake/micro_wake_word/include/esphome/core/optional.h)) |
| micro_speech_features (TensorFlow Micro frontend) | [Apache-2.0](vendor/wake/micro_speech_features/LICENSE) (Copyright TensorFlow Authors, individual headers retained) |
| KissFFT (`vendor/wake/micro_speech_features/src/kiss_fft*`) | BSD-3-Clause (Copyright 2003–2010 Mark Borgerding; header in [kiss_fft.c](vendor/wake/micro_speech_features/src/kiss_fft.c)) |
| Hey Jarvis reference wake model | [Apache-2.0](vendor/wake/micro_wake_models/LICENSE); [pinned wake sources](vendor/wake/sources.json) |
| Embedded **Hey Adam** pilot wake model (`hey_adam-20261030-c60f0456`) | **Noncommercial personal/research use only** — trained with `microWakeWord` / `piper-sample-generator` using datasets that include noncommercial terms (`mchl914/fma_xsmall`, `agkphysics/AudioSet`, `MIT_environmental_impulse_responses`, `LibriTTS-R`); see [pilot notes](docs/HEY_ADAM_PILOT.md) and [manifest](firmware/main/wake_model.json) |
| Three.js 0.180.0 and OrbitControls | [MIT](headend/web/dist/vendor/three/LICENSE); [source URLs and SHA-256](headend/web/dist/vendor/three/provenance.json) |
| Wake training reference files (`mixednet.py`, `model_train_eval.py`) | [Apache-2.0](training/hey_adam/upstream/micro-wake-word-LICENSE) (Copyright Google Research Authors & Kevin Ahrendt); [source revisions](training/hey_adam/sources.json) |
| Piper sample generator references | [MIT](training/hey_adam/upstream/piper-sample-generator-LICENSE.md) |
| Basic English / Kristin voice (downloaded separately) | [Voice attribution and terms](headend/voice-licenses/basic-english.txt); [model manifest](headend/voice-models.json) |

## Additional notes

- **Pre-trained wake-word weights:** The firmware embeds `firmware/main/wake_model.tflite`
  (duplicated in `training/hey_adam/export/hey_adam.tflite`), alongside a locally
  synthesized chime (`firmware/main/listen_cue.pcm`). Because the upstream
  negative-feature recipe includes Free Music Archive (`fma_xsmall`) audio under
  Creative Commons NonCommercial licenses, commercial deployments must replace
  the embedded `.tflite` model with a cleanly licensed model (for example, the
  Apache-2.0 `hey_jarvis.tflite` preserved under `vendor/wake/micro_wake_models/`
  via `python tools/install_wake_model.py`).
- **External dependencies:** Installed Python packages and ESP-IDF managed
  components are not committed; they retain their respective upstream licenses,
  including the GPL-3.0 `piper-tts` runtime. Exact versions are pinned in
  `headend/requirements-lock.txt` and `firmware/dependencies.lock`. Larger speech
  recognition, voice, and conversation weights are downloaded separately at setup.
- **Vendor modifications:** `vendor/ESP-IDF/ESP32-S3-Touch-LCD-1.85C-Test/main/LCD_Driver/esp_lcd_st77916/esp_lcd_st77916.c`
  contains local QSPI axis-swap and transfer-error fixes; see its file header and
  [development history](docs/DEVELOPMENT_HISTORY.md).
