# Third-party notices

Original ADAM code has no project-wide license selected yet. Included upstream
material retains the notices and terms supplied with that material.

| Material | Included license / provenance |
| --- | --- |
| Waveshare board samples and LCD driver | [Apache-2.0](vendor/LICENSE); [source tree](vendor/waveshare-tree.json) at commit `8ead4a96bf3a278fc4ebd8ef4768657e17fa2880` |
| Espressif ES7210 reference | Apache-2.0 SPDX header in [source](vendor/espressif/es7210-native.c) |
| micro_wake_word embedded runner | [GPLv3](vendor/wake/micro_wake_word/LICENSE.md) |
| micro_speech_features | [Apache-2.0](vendor/wake/micro_speech_features/LICENSE), with individual source notices retained |
| Hey Jarvis wake model | [Apache-2.0](vendor/wake/micro_wake_models/LICENSE); [pinned wake sources](vendor/wake/sources.json) |
| Three.js 0.180.0 and OrbitControls | [MIT](headend/web/dist/vendor/three/LICENSE); [source URLs and SHA-256](headend/web/dist/vendor/three/provenance.json) |
| Wake training reference files | [Apache-2.0](training/hey_adam/upstream/micro-wake-word-LICENSE); [source revisions](training/hey_adam/sources.json) |
| Piper sample generator references | [MIT](training/hey_adam/upstream/piper-sample-generator-LICENSE.md) |
| Basic English / Kristin voice (downloaded separately) | [Voice attribution and terms](headend/voice-licenses/basic-english.txt); [model manifest](headend/voice-models.json) |

Installed Python packages and ESP-IDF managed components are not committed.
They retain their respective upstream licenses, including the GPL Piper
runtime. Lock files identify dependency versions. Larger speech weights and
training datasets are downloaded separately and are not part of this source
upload. The firmware embeds the wake-model copy from `vendor/wake/` and a
locally generated chime; see their manifests in `firmware/main/`.

The Waveshare LCD driver includes local QSPI axis-swap and transfer-error fixes;
other vendor-specific adaptation is in the firmware. See the
[development history](docs/DEVELOPMENT_HISTORY.md) for the original bring-up notes.
