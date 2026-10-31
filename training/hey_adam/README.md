# Hey Adam: local training investigation

Current status: Firmware v27 embeds the `hey_adam-20261030-c60f0456`
microWakeWord model, with stock `Hey Jarvis` preserved under `vendor/wake/` for
reference and rollback. See [pilot validation and limits](../../docs/HEY_ADAM_PILOT.md)
and the [GPU-machine setup guide](../../docs/WAKEWORD_MODEL_GENERATION.md).
The benchmark results below describe the initial CPU feasibility investigation;
they do not measure the trained model's detection accuracy. Datasets and
checkpoints remain local, while `pilot-v2/` contains tracked metadata from the
earlier pilot training run.

## Measured local feasibility

Measured in an isolated Linux container on a standard x86-64 development machine
with Docker Desktop / WSL2 and no discrete GPU. The benchmark container is
deliberately capped at four CPU threads and an 8 GiB memory limit.

| Synthetic training benchmark | Batch 32 | Batch 128 |
| --- | ---: | ---: |
| Mean step | 13.86 ms | 24.73 ms |
| 95th percentile | 16.50 ms | 27.58 ms |
| Projected 10,000 steps, model compute only | 2.3 min | 4.1 min |

These are 50-step warmed measurements of a 26,049-parameter network, using
random features already in memory. They are **not full training-time estimates**
and say nothing about detection accuracy. Sample generation, augmentation,
feature extraction, disk access, validation, quantization and repeated experiments
are excluded. Dataset selection and validation are the substantial work.
Benchmark JSON outputs and package snapshots are written to local `results/`
(gitignored; created when running the benchmark).

For faster iteration on a CUDA-capable workstation (for example, an NVIDIA RTX
GPU), run the GPU benchmark below to compare throughput with the same model,
batch size and software. Higher GPU throughput permits faster experiments; it
does not automatically improve an identical model's accuracy or ESP32 runtime.

## Repeat CPU benchmark (PowerShell, workspace root)

```powershell
docker build -t adam-wake-benchmark:0.1 -f training/hey_adam/Dockerfile.benchmark training/hey_adam
$wakeBenchPath = (Resolve-Path training/hey_adam).Path
New-Item -ItemType Directory -Force "$wakeBenchPath\results" | Out-Null
docker run --rm --cpus 4 --memory 8g --network none --read-only --tmpfs /tmp:rw,size=256m --mount "type=bind,source=$wakeBenchPath,target=/work" adam-wake-benchmark:0.1 --steps 50 --batch-size 128 --threads 4 --output results/cpu-benchmark-batch128.json
```

## Matching GPU benchmark

On a GPU workstation with Docker Desktop (WSL2 backend), a compatible NVIDIA
driver, and working Docker GPU passthrough, run the matching container. Modern
TensorFlow CUDA training on Windows uses Linux/WSL2 rather than a native Windows
GPU package. See [TensorFlow installation](https://www.tensorflow.org/install/pip)
and [Microsoft's CUDA/WSL guide](https://learn.microsoft.com/en-us/windows/ai/directml/gpu-cuda-in-wsl).

From `training/hey_adam` in PowerShell:

```powershell
New-Item -ItemType Directory -Force results | Out-Null
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
   requests, silence endpoint, CPU, memory and home-screen rendering again after deployment.

`tools/install_wake_model.py` installs actual model bytes and their matching
config before rebuild/flash. A new phrase label alone is not a new detector.
Retain the vendored `Hey Jarvis` model under `vendor/wake/` as an explicit
development rollback when testing candidate replacements.

## Upstream findings

- Trainer: [OHF-Voice/micro-wake-word](https://github.com/OHF-Voice/micro-wake-word),
  revision `4665173cd35f1cff9a61e06fc427f124766c488e`.
- Generator: [rhasspy/piper-sample-generator](https://github.com/rhasspy/piper-sample-generator),
  revision `2971426a55072f7d22fec416ca7800df8bd23207`.
- Official model-tree inspection found no prebuilt Hey Adam artifact to install.
- The trainer notebook still refers to `generate_samples.py`; the current generator
  uses `python -m piper_sample_generator`. Its pinned Piper dependency differs from
  the head-end runtime. Port that stage explicitly rather than blindly running the notebook.
- The upstream wheel omitted the `microwakeword.layers` directory in the tested
  build. Installing the pinned source checkout in editable mode fixes the benchmark.
- `sources.json` records revisions and SHA-256 hashes for the small upstream
  reference files in `upstream/`, with their supplied license notices.

Upstream warns that the basic notebook is a starting point and an exported model
may still be unsuitable for everyday use. This project treats real-device evaluation
as a required stage rather than equating successful export with reliable detection.
