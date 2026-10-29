# Hey Adam: local training investigation

Status, October 28, 2026: the existing ESP32 runner supports microWakeWord v2;
the pinned upstream MixedNet architecture builds and trains on synthetic input
in a local CPU container. **No Hey Adam detector has been trained or installed.**
The running device still uses Hey Jarvis. Neither the head-end environment nor
its firmware was changed by this investigation.

## Measured local feasibility

Laptop: i7-1270P, 12 cores / 16 threads, 31.7 GiB RAM, Intel Iris Xe, approximately
100 GiB free disk at inspection. Docker Desktop / WSL2 is already available.
The benchmark deliberately uses only four CPU threads and an 8 GiB memory limit.

| Synthetic training benchmark | Batch 32 | Batch 128 |
| --- | ---: | ---: |
| Mean step | 13.86 ms | 24.73 ms |
| 95th percentile | 16.50 ms | 27.58 ms |
| Projected 10,000 steps, model compute only | 2.3 min | 4.1 min |

These are 50-step warmed measurements of a 26,049-parameter network, using
random features already in memory. They are **not full training-time estimates**
and say nothing about detection accuracy. Sample generation, augmentation,
feature extraction, disk access, validation, quantization and repeated experiments
are excluded. Dataset selection and validation are the next substantial work.
Results and exact installed package versions are in `results/`.

The user also has an RTX 4070 Super machine. Its GPU has not been accessed or
benchmarked. Use the GPU benchmark below to measure the difference with the same
model, batch and software. More GPU throughput permits more experiments; it does
not automatically improve an identical model's accuracy or its ESP32 runtime.

## Repeat CPU benchmark (PowerShell, workspace root)

```powershell
docker build -t adam-wake-benchmark:0.1 -f training/hey_adam/Dockerfile.benchmark training/hey_adam
$wakeBenchPath = (Resolve-Path training/hey_adam).Path
docker run --rm --cpus 4 --memory 8g --network none --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$wakeBenchPath,target=/work" adam-wake-benchmark:0.1 --steps 50 --batch-size 128 --threads 4 --output results/cpu-benchmark-batch128.json
```

## Matching GPU benchmark (prepared, not yet run)

Copy this `hey_adam` directory to the GPU machine. On Windows, use Docker Desktop
with its WSL2 backend, a compatible NVIDIA driver, and working Docker GPU access.
The modern TensorFlow training path requires Linux/WSL2 for CUDA rather than a
native Windows GPU install. See [TensorFlow installation](https://www.tensorflow.org/install/pip)
and [Microsoft's CUDA/WSL guide](https://learn.microsoft.com/en-us/windows/ai/directml/gpu-cuda-in-wsl).

From the copied directory, in PowerShell:

```powershell
docker build -t adam-wake-benchmark-gpu:0.1 -f Dockerfile.benchmark-gpu .
$wakeBenchPath = (Get-Location).Path
docker run --rm --gpus all --cpus 4 --memory 8g --network none --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$wakeBenchPath,target=/work" adam-wake-benchmark-gpu:0.1 --steps 50 --batch-size 128 --threads 4 --output results/gpu-benchmark-batch128.json
```

The GPU script fails if TensorFlow cannot see a GPU, preventing a misleading CPU
fallback result. Divide CPU `mean_step_ms` by GPU `mean_step_ms` for model-compute
speedup only. GPU dependencies take more download/storage space than the CPU image.
Both benchmark images intentionally omit full dataset/training dependencies.

## Training and acceptance plan

1. **Separate environments.** Keep sample generation and full TensorFlow training
   isolated from the production head-end. Pin and record every source, dependency,
   data source and random seed. Do not install the trainer into `headend/.venv`.
2. **Sample-generation trial.** Generate and listen to a small, varied set of
   “Hey Adam” examples before scaling up. Use several speakers, speeds and
   pronunciations; one selected response voice is insufficient training diversity.
   Benchmark generation separately. Use 16 kHz mono PCM inputs for feature extraction.
3. **Real recordings.** Capture explicit samples from the box, at ordinary speaking
   distances and angles. Keep separate recording sessions for development and a
   held-out test set. A starting collection is 40 development positives and 20
   held-out positives, plus near-misses; synthetic data supplies additional variety.
   Those counts are a proposed pilot, not a claim that 60 clips guarantee quality.
4. **Negative data.** Include ordinary speech, room noise and similar phrases such
   as “Hey Alan,” “Hey Anna,” “Hey Evan,” and “Adam” without “Hey.” Track licenses
   individually: the upstream notebook's suggested datasets include mixed usage
   restrictions, so do not treat its dataset recipe as automatically distributable.
5. **Training and tuning.** Train the pinned streaming-compatible architecture,
   select thresholds using development/validation data, then export the quantized
   streaming `.tflite` and its matching microWakeWord v2 JSON manifest. Preprocessing
   must match the installed microphone runner's features. Benchmark a small real-data
   run before committing to full-scale sample generation or training.
6. **Acceptance.** First confirm loading and continuous inference on this ESP32.
   Target at least 18 of 20 held-out spoken attempts around one metre as a pilot
   gate, then test near-misses and hours of background speech/noise. Report false
   triggers per hour, not just training accuracy. Check pre-roll, continuous spoken
   requests, silence endpoint, CPU, memory and eye animation again after deployment.

The existing `tools/install_wake_model.py` installs actual model bytes and their
matching config before rebuild/flash. A new phrase label alone is not a new detector.
Retain the Jarvis model as an explicit development rollback until Adam passes.

## Upstream findings

- Trainer: [OHF-Voice/micro-wake-word](https://github.com/OHF-Voice/micro-wake-word),
  revision `4665173cd35f1cff9a61e06fc427f124766c488e`.
- Generator: [rhasspy/piper-sample-generator](https://github.com/rhasspy/piper-sample-generator),
  revision `2971426a55072f7d22fec416ca7800df8bd23207`.
- Current official model-tree inspection found no Hey Adam artifact to install.
- The trainer notebook still refers to `generate_samples.py`; the current generator
  uses `python -m piper_sample_generator`. Its pinned Piper dependency differs from
  the working head-end. Port that stage explicitly rather than blindly running the notebook.
- The upstream wheel omitted the `microwakeword.layers` directory in the tested
  build. Installing the pinned source checkout in editable mode fixes the benchmark.
- `sources.json` records revisions and SHA-256 hashes for the small upstream
  reference files in `upstream/`, with their supplied license notices.

Upstream warns that the basic notebook is a starting point and an exported model
may still be unsuitable for everyday use. This project treats real-device evaluation
as a required stage rather than equating successful export with reliable detection.
