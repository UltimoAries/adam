# Wake-word model generation (`Hey Adam`)

End-to-end guide for training and exporting a custom **Hey Adam** microWakeWord
model on a **Windows 11** workstation with a CUDA-capable NVIDIA GPU (for example
an RTX-series card) using WSL2 and Docker. Training runs locally; internet access
is used only to pull container packages, sample-generator checkpoints and
public training datasets.

**Current status:** Firmware v27 already embeds the pre-built
`hey_adam-20261030-c60f0456` model in `firmware/main/` (with stock `Hey Jarvis`
preserved under `vendor/wake/` for rollback; see [HEY_ADAM_PILOT.md](HEY_ADAM_PILOT.md)).
Follow this guide when training a new or fine-tuned replacement model.

## 1. Install the NVIDIA driver and WSL2

Install a current [NVIDIA Windows driver](https://www.nvidia.com/Download/index.aspx),
then restart if prompted.

In **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-22.04
wsl --update
```

Complete Ubuntu's initial user setup if newly installed, then verify in
**PowerShell**:

```powershell
nvidia-smi
wsl --list --verbose
```

Confirm your NVIDIA GPU is listed and `Ubuntu-22.04` runs on WSL version **2**.
WSL2 uses the Windows host NVIDIA driver directly; do not install a separate
Linux display driver inside WSL. See the
[NVIDIA CUDA on WSL guide](https://docs.nvidia.com/cuda/wsl-user-guide/index.html).

## 2. Install Docker Desktop

Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/),
enable **Settings → General → Use the WSL 2 based engine**, switch to Linux
containers, and verify GPU container support
([Docker GPU documentation](https://docs.docker.com/desktop/features/gpu/)):

```powershell
docker version
```

Allow at least **50 GB** of free disk space for container layers, synthetic
speech samples and training datasets.

## 3. Clone the repository

Clone the repository:

```powershell
git clone https://github.com/UltimoAries/adam.git C:\Work\adam
Set-Location C:\Work\adam\training\hey_adam
New-Item -ItemType Directory -Force results | Out-Null
```

For an existing checkout, create a clean branch from `main`:

```powershell
git switch main
git pull --ff-only origin main
git switch -c model-experiments
```

`training/hey_adam` contains `Dockerfile.benchmark-gpu`, `benchmark_cpu.py`,
`upstream/`, and `pilot-v2/`. Local `results/`, datasets and checkpoints are
gitignored. Keep training environments separate from `headend/.venv`.

## 4. Verify Docker GPU training throughput

In **PowerShell** on the GPU machine:

```powershell
Set-Location C:\Work\adam\training\hey_adam
New-Item -ItemType Directory -Force results | Out-Null
docker build -t adam-wake-benchmark-gpu:0.1 -f Dockerfile.benchmark-gpu .
$wakeTrainingPath = (Get-Location).Path
docker run --rm --gpus all --cpus 4 --memory 8g --network none --mount "type=bind,source=$wakeTrainingPath,target=/work" adam-wake-benchmark-gpu:0.1 --steps 50 --batch-size 128 --threads 4 --output results/gpu-benchmark-batch128.json
```

**Pass criteria:** the JSON report shows `device: GPU`, names the NVIDIA GPU, and
completes all 50 steps (the script exits with an error if CUDA is unavailable).
For comparison, the 4-thread CPU container baseline measured **24.73 ms/step** at
batch 128 for model compute only.

## 5. Create a persistent training container

The benchmark image omits full dataset utilities to stay compact. Launch a named
container from `training/hey_adam` to install the full training stack:

```powershell
docker run -it --name adam-wake-training --gpus all --cpus 8 --memory 12g --shm-size 2g -p 127.0.0.1:8888:8888 --mount "type=bind,source=$wakeTrainingPath,target=/work" --entrypoint bash adam-wake-benchmark-gpu:0.1
```

Run the following inside the container's **Linux bash shell**:

```bash
apt-get update
apt-get install -y build-essential libsndfile1 ffmpeg wget unzip espeak-ng
cd /work
printf 'tensorflow==2.18.0\nnumpy==2.0.2\n' > training-constraints.txt
python -m pip install -c training-constraints.txt 'git+https://github.com/whatsnowplaying/audio-metadata@d4ebb238e6a401bb1a5aaaac60c9e2b3cb30929f'
python -m pip install -c training-constraints.txt -e /opt/micro-wake-word jupyterlab ipykernel matplotlib
python -c "import tensorflow as tf; import microwakeword.audio.spectrograms; print(tf.config.list_physical_devices('GPU'))"
```

Editable installation from `/opt/micro-wake-word` retains `microwakeword.layers`
(omitted from the upstream wheel), and the pinned `audio-metadata` commit matches
the upstream notebook's dependency fix.

## 6. Set up isolated synthetic sample generation

Still inside the container's **Linux bash shell**, create a separate virtual
environment for `piper-sample-generator` so its PyTorch dependencies do not
conflict with TensorFlow:

```bash
python -m venv /opt/generator-venv
/opt/generator-venv/bin/python -m pip install 'git+https://github.com/rhasspy/piper-sample-generator.git@2971426a55072f7d22fec416ca7800df8bd23207'
/opt/generator-venv/bin/python -c "import torch; print(torch.cuda.is_available()); assert torch.cuda.is_available(), 'Generator cannot see CUDA'"
mkdir -p models
wget -O models/en_US-libritts_r-medium.pt https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt
/opt/generator-venv/bin/python -m piper_sample_generator 'Hey Adam.' --model models/en_US-libritts_r-medium.pt --max-samples 10 --batch-size 1 --max-speakers 100 --output-dir preview_samples
```

Listen to a few WAVs in `C:\Work\adam\training\hey_adam\preview_samples` to
confirm natural pronunciation, then generate the positive set:

```bash
/opt/generator-venv/bin/python -m piper_sample_generator 'Hey Adam.' --model models/en_US-libritts_r-medium.pt --max-samples 2000 --batch-size 16 --max-speakers 100 --output-dir generated_samples
```

Reduce `--batch-size` if VRAM is tight. See the
[piper-sample-generator documentation](https://github.com/rhasspy/piper-sample-generator).

## 7. Prepare features and train in Jupyter

Copy the pinned training notebook and start Jupyter Lab inside the container:

```bash
cp upstream/micro-wake-word-basic_training_notebook.ipynb hey_adam_training.ipynb
python -m pip freeze > results/trainer-environment.txt
/opt/generator-venv/bin/python -m pip freeze > results/generator-environment.txt
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --ServerApp.root_dir=/work
```

Open the printed `http://127.0.0.1:8888/...token=...` URL in your browser and run
`hey_adam_training.ipynb` with these adjustments:

1. **Skip the notebook's package-install and `generate_samples.py` cells** (steps
   5–6 above replace them). Set `target_word = 'Hey Adam'` if referenced.
2. Run the augmentation download, feature extraction and negative-dataset cells,
   keeping `generated_samples` as the positive directory and `10 ms` as the
   feature step. Review dataset licenses before redistribution (several upstream
   negative corpora carry noncommercial terms).
3. Keep real ESP32 recordings in a separate held-out evaluation split, including
   near-misses (“Hey Alan”, “Hey Anna”, standalone “Adam”).
4. Write `training_parameters.yaml` (starting with **10,000 steps / batch 128**
   and MixedNet) and run training/export with
   `--test_tflite_streaming_quantized 1`.
5. **Skip the Colab download cell** — outputs are already in your bind-mounted
   `training/hey_adam` directory.

Resume a stopped container later with `docker start -ai adam-wake-training`.

## 8. Package the exported `.tflite` and manifest

Copy the quantized streaming model from:

```text
trained_models/wakeword/tflite_stream_state_internal_quant/stream_state_internal_quant.tflite
```

into `training/hey_adam/export/hey_adam.tflite`, alongside
`training/hey_adam/export/hey_adam.json`:

```json
{
  "type": "micro",
  "wake_word": "Hey Adam",
  "author": "Local project",
  "model": "hey_adam.tflite",
  "trained_languages": ["en"],
  "version": 2,
  "micro": {
    "probability_cutoff": 0.97,
    "sliding_window_size": 5,
    "feature_step_size": 10,
    "tensor_arena_size": 65536,
    "minimum_esphome_version": "2024.7.0"
  }
}
```

Tune `probability_cutoff` and `sliding_window_size` against held-out validation
clips, and record the SHA-256 hashes and training parameters.

## 9. Install into firmware and flash

Copy `export/hey_adam.tflite` and `export/hey_adam.json` to
`training/hey_adam/export/` in the checkout connected to the ESP32-S3, then run
from the repository root (`C:\Work\adam`):

```powershell
Set-Location C:\Work\adam
$wakeBackup = 'backups\before-adam-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Force -Path $wakeBackup | Out-Null
Copy-Item firmware\main\wake_model.tflite,firmware\main\wake_model.json,firmware\main\wake_model_config.h -Destination $wakeBackup
if (Test-Path firmware\build\display_probe.bin) {
    Copy-Item firmware\build\display_probe.bin -Destination $wakeBackup
}
py -3.12 tools/install_wake_model.py training/hey_adam/export/hey_adam.json --id hey_adam-v1 --development
$env:CCACHE_DISABLE = '1'
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
.\headend\.venv\Scripts\python.exe tools/control.py wake status --wait 1
```

Confirm `ready=1` and `model=hey_adam-v1`.

## 10. Validate on hardware before promotion

- Test at least 20 held-out spoken “Hey Adam” attempts around 1 m (and additional
  trials at 2–3 m and varied angles), targeting >=90% recall before tuning.
- Speak continuous wake queries (“Hey Adam, what's the weather in Seattle?”) and
  verify pre-roll retention, the 1.2 s silence endpoint, reply playback and
  sky-panel mist transitions.
- Log multi-hour background speech/TV/music exposure to measure false accepts per
  hour, and check `wake status` and `resources` for zero overflows and healthy
  RAM/CPU margins.
