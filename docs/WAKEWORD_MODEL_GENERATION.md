# WAKEWORD_MODEL_GENERATION

Quick start-to-finish guide for building **Hey Adam** on your RTX 4070 Super machine.
Assumes **Windows 11**. Training stays local; internet is used to download software,
models and datasets. The ESP32 can remain USB-connected to the current laptop.

**Status:** the CPU benchmark is tested. The GPU setup and full training workflow
below still need validation on your other machine. The benchmark produces timing
results, not a wake model. A usable Hey Adam model requires training and testing.

## 1. Install the GPU driver and WSL2

Install the current [NVIDIA Windows driver](https://www.nvidia.com/Download/index.aspx)
for the 4070 Super, then restart if requested.

In **PowerShell as Administrator**:

```powershell
wsl --install -d Ubuntu-22.04
wsl --update
```

Restart if requested and finish Ubuntu's first-run username/password setup.
In ordinary **PowerShell**, verify:

```powershell
nvidia-smi
wsl --list --verbose
```

The GPU should appear, and Ubuntu should show version **2**. Install the NVIDIA
driver on Windows; WSL uses that driver. [NVIDIA WSL guidance](https://docs.nvidia.com/cuda/wsl-user-guide/index.html)

## 2. Install Docker Desktop

Install [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/).
Enable **Settings → General → Use the WSL 2 based engine**, use Linux containers,
and leave Docker Desktop running. GPU passthrough on Windows requires its WSL2
backend. [Docker GPU instructions](https://docs.docker.com/desktop/features/gpu/)

Verify in **PowerShell**:

```powershell
docker version
```

Allow space for container images, generated samples and datasets. **50 GB free is
a planning allowance for an initial run**, not a bound on the upstream datasets.

## 3. Copy the training folder to the other machine

Copy this laptop's folder:

```text
C:\Work\AG_ESP32_SPEAKER\training\hey_adam
```

to this location on the GPU machine:

```text
C:\Work\HeyAdamTraining
```

It should contain `Dockerfile.benchmark-gpu`, `benchmark_cpu.py`, `upstream/`,
and `results/`. The head-end database, passwords and device pairing keys are not
needed. Keep the production head-end's Python environment on this laptop.

## 4. Verify GPU training works

In **PowerShell on the GPU machine**:

```powershell
Set-Location C:\Work\HeyAdamTraining
docker build -t adam-wake-benchmark-gpu:0.1 -f Dockerfile.benchmark-gpu .
$wakeTrainingPath = (Get-Location).Path
docker run --rm --gpus all --cpus 4 --memory 8g --network none --mount "type=bind,source=$wakeTrainingPath,target=/work" adam-wake-benchmark-gpu:0.1 --steps 50 --batch-size 128 --threads 4 --output results/gpu-benchmark-batch128.json
```

**Pass:** the report says `device: GPU`, identifies the NVIDIA GPU, and completes
all steps. It fails explicitly if TensorFlow cannot see a GPU. The laptop baseline
is **24.73 ms/step at batch 128**; compare the same settings. This measures model
computation only, excluding sample generation and data processing.

## 5. Create a persistent training environment

The benchmark image deliberately omits dataset tools. Start a separate named
container and install those tools there. In **PowerShell**, from the same folder:

```powershell
docker run -it --name adam-wake-training --gpus all --cpus 8 --memory 12g --shm-size 2g -p 127.0.0.1:8888:8888 --mount "type=bind,source=$wakeTrainingPath,target=/work" --entrypoint bash adam-wake-benchmark-gpu:0.1
```

The following commands run in the container's **Linux shell**, not PowerShell:

```bash
apt-get update
apt-get install -y build-essential libsndfile1 ffmpeg wget unzip espeak-ng
cd /work
printf 'tensorflow==2.18.0\nnumpy==2.0.2\n' > training-constraints.txt
python -m pip install -c training-constraints.txt 'git+https://github.com/whatsnowplaying/audio-metadata@d4ebb238e6a401bb1a5aaaac60c9e2b3cb30929f'
python -m pip install -c training-constraints.txt -e /opt/micro-wake-word jupyterlab ipykernel matplotlib
python -c "import tensorflow as tf; import microwakeword.audio.spectrograms; print(tf.config.list_physical_devices('GPU'))"
```

The editable source installation preserves the upstream `layers` directory; the
wheel we tested omitted it. The audio-metadata fork is the notebook's dependency
fix. **Resolve any installation/import errors before proceeding**: the complete
dataset-tool dependency combination has not yet been tested here.

## 6. Set up sample generation separately

Still in the **Linux shell**, create an isolated generator environment. This
avoids changing the trainer's TensorFlow dependencies or our head-end's Piper:

```bash
python -m venv /opt/generator-venv
/opt/generator-venv/bin/python -m pip install 'git+https://github.com/rhasspy/piper-sample-generator.git@2971426a55072f7d22fec416ca7800df8bd23207'
/opt/generator-venv/bin/python -c "import torch; print(torch.cuda.is_available()); assert torch.cuda.is_available(), 'Generator cannot see CUDA'"
mkdir -p models
wget -O models/en_US-libritts_r-medium.pt https://github.com/rhasspy/piper-sample-generator/releases/download/v2.0.0/en_US-libritts_r-medium.pt
/opt/generator-venv/bin/python -m piper_sample_generator 'Hey Adam.' --model models/en_US-libritts_r-medium.pt --max-samples 10 --batch-size 1 --max-speakers 100 --output-dir preview_samples
```

Play the files in `C:\Work\HeyAdamTraining\preview_samples` on Windows. Confirm
the pronunciation before scaling up. Then generate an initial development set:

```bash
/opt/generator-venv/bin/python -m piper_sample_generator 'Hey Adam.' --model models/en_US-libritts_r-medium.pt --max-samples 2000 --batch-size 16 --max-speakers 100 --output-dir generated_samples
```

These counts are starting settings, not a guarantee of accuracy. Reduce batch size
if GPU memory is exhausted. The generator automatically uses CUDA when available.
See the [generator documentation](https://github.com/rhasspy/piper-sample-generator).

## 7. Prepare data and train in the notebook

Copy the pinned notebook and open Jupyter, still in the **Linux shell**:

```bash
cp upstream/micro-wake-word-basic_training_notebook.ipynb hey_adam_training.ipynb
python -m pip freeze > results/trainer-environment.txt
/opt/generator-venv/bin/python -m pip freeze > results/generator-environment.txt
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser --allow-root --ServerApp.root_dir=/work
```

Open the printed `http://127.0.0.1:8888/...token=...` link in your browser. Keep
token authentication enabled. Open `hey_adam_training.ipynb` using its Python kernel.

Run the notebook in order with these adjustments:

1. **Skip its installation and sample-generation cells.** Steps 5–6 replace them;
   the notebook's old `generate_samples.py` command is incompatible with the
   current generator. Set `target_word = 'Hey Adam'` if referenced later.
2. Run the augmentation-data download, augmentation configuration, feature
   generation and negative-feature download cells. Keep `generated_samples`
   as the positive input directory and the **10 ms** feature step. Inspect
   dataset sizes and licenses before downloading; the supplied recipe includes
   data with noncommercial restrictions.
3. Keep recordings from the actual box in a separate held-out test set. Include
   ordinary speech and near-misses such as “Hey Alan,” “Hey Anna,” and “Adam.”
   Do not count augmented copies of training audio as independent test examples.
4. Run the cell that writes `training_parameters.yaml`. Start with the notebook's
   **10,000 steps / batch 128** and its MixedNet architecture. If memory is tight,
   lower the batch size and record that change.
5. Run the training/export cell. Keep `--test_tflite_streaming_quantized 1` enabled.
   Review false accepts and recall; successful training alone does not mean the
   wake word is ready for everyday use.
6. **Skip the final Google Colab download cell.** Your output is already in the
   Windows-mounted folder.

This notebook remains an experimental training scaffold. Dataset API or dependency
errors may need adaptation; the full GPU/data pipeline has not yet been exercised
on your machine. [Upstream training notebook](https://github.com/OHF-Voice/micro-wake-word/blob/4665173cd35f1cff9a61e06fc427f124766c488e/notebooks/basic_training_notebook.ipynb)

To return to this environment later, run `docker start -ai adam-wake-training`
from PowerShell if the container is stopped. The `/work` files persist in your
Windows folder; installed tools persist in the named container.

## 8. Export the device model and manifest

The notebook's expected exported model is:

```text
trained_models/wakeword/tflite_stream_state_internal_quant/stream_state_internal_quant.tflite
```

Copy it into a new `export/` folder as `hey_adam.tflite`. Beside it, save
`hey_adam.json`:

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

**Threshold, window and arena are starting values**, not measured settings for
your new model. Tune against validation results and verify allocation on the box.
Keep the config, training logs, dataset provenance and exported file hashes.

## 9. Bring it back to this laptop and flash

Copy `export/hey_adam.tflite` and `export/hey_adam.json` to
`C:\Work\AG_ESP32_SPEAKER\training\hey_adam\export` on the original laptop.

In **PowerShell on the original laptop**:

```powershell
Set-Location C:\Work\AG_ESP32_SPEAKER
$wakeBackup = 'backups\before-adam-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory -Path $wakeBackup
Copy-Item firmware\main\wake_model.tflite,firmware\main\wake_model.json,firmware\main\wake_model_config.h,firmware\build\display_probe.bin -Destination $wakeBackup
py -3.12 tools/install_wake_model.py training/hey_adam/export/hey_adam.json --id hey_adam-v1 --development
$env:CCACHE_DISABLE = '1'
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
.\headend\.venv\Scripts\python.exe tools/control.py wake status --wait 1
```

Confirm `ready=1` and `model=hey_adam-v1`. A successful load establishes format
compatibility; actual speech tests establish detection quality.

## 10. Test before making it the normal wake word

- Try 20 held-out “Hey Adam” attempts at about one metre; aim initially for at
  least 18 detections. Vary speed, angle and distance.
- Say “Hey Adam, what's the weather?” continuously. Confirm the first word,
  silence endpoint, response and animation still work.
- Test similar phrases and several hours of background speech/noise. Record
  false triggers per hour. Tune with validation data and reserve fresh test clips.
- Check `wake status` and `resources` for queue overflows and memory/CPU pressure.

Keep the saved Jarvis files for rollback. Completion means **Hey Adam works on
this box with acceptable misses and false triggers**, not merely that a TFLite
file was created.
