# Optional Windows GPU Model Host

Offload conversation (Ollama) and speech recognition (Speaches / CUDA
`faster-whisper`) from a Raspberry Pi or lightweight head-end onto a Windows PC
with an NVIDIA GPU. Both services are optional and independent—whenever the GPU
PC is asleep, busy, or stopped, the head-end automatically falls back to its
local CPU models after a short timeout and retries the GPU host after a
30-second cooldown.

---

## Architecture & Directory Layout

All files live under a self-contained root (`C:\ADAM_VOICE` by default) without
modifying global user `PATH` or profile environment variables:

```text
C:\ADAM_VOICE\
  ADAM_GPU_Control.pyw         Desktop Tkinter Start/Stop & model selector
  bin\ollama\v0.40.13\         Standalone Windows Ollama archive (ollama.exe + CUDA libs)
  config\ollama.json           Ollama version, bind address, and selected model
  config\start_ollama.ps1      SYSTEM startup task entry point for Ollama
  config\warm_ollama.ps1       Loads and aliases the selected model into VRAM
  config\speech.json           Local Speaches base URL and Whisper model ID
  config\speaches.env          Private Bearer API key (never commit to Git)
  config\speaches.compose.yaml Pinned GPU Speaches container definition
  config\start_speech.ps1      Startup/logon task entry point for Speaches
  config\docker-public\        Isolated anonymous Docker config directory
  models\                      Ollama weights + models\speech\ Hugging Face cache
  data\                        Isolated Ollama home/tmp + data\speech\ cache
  logs\                        Rotated service and warm-up receipts
```

Neither service receives Home Assistant credentials or durable training-archive
copies: Ollama receives only the system prompt and current spoken question, and
Speaches receives only the utterance WAV to transcribe.

---

## Step-by-Step Setup

### Prerequisites

- Windows 10/11 (64-bit) with a recent NVIDIA driver (`nvidia-smi.exe` working).
- Python 3.11+ with Tkinter (`pythonw.exe`, included in the standard python.org
  Windows installer) for the desktop control window.
- Docker Desktop (WSL 2 Linux engine with NVIDIA GPU support) if using GPU
  speech recognition (`-SkipSpeech` omits speech setup if you only want Ollama).
- A reserved LAN IPv4 address on your GPU PC (for example `192.168.1.20`).

### 1. Create the installation root and configuration files

Open an **elevated PowerShell** window (`Run as administrator`) from the
repository root:

```powershell
$Root = 'C:\ADAM_VOICE'
New-Item -ItemType Directory -Path "$Root\bin\ollama\v0.40.13", "$Root\config" -Force | Out-Null

Copy-Item deploy\model-host\ollama.example.json "$Root\config\ollama.json"
Copy-Item deploy\model-host\speech.example.json "$Root\config\speech.json"
Copy-Item deploy\model-host\speaches.env.example "$Root\config\speaches.env"
```

Edit those three files under `C:\ADAM_VOICE\config\`:
- **`ollama.json`**: Set `"listen"` to `<gpu-host-lan-ip>:11434` (for example
  `"192.168.1.20:11434"`) and confirm `"version"` matches your extracted Ollama
  directory (`"v0.40.13"`).
- **`speech.json`** *(if using GPU STT)*: Set `"url"` to
  `"http://<gpu-host-lan-ip>:8000"` (**without** `/v1`—the local startup and
  health scripts query `/health` and `/api/ps` at the root URL).
- **`speaches.env`** *(if using GPU STT)*: Replace `API_KEY=` with a strong
  random secret:
  ```powershell
  $Bytes = New-Object byte[] 32
  [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($Bytes)
  "API_KEY=$([Convert]::ToBase64String($Bytes).TrimEnd('=').Replace('+','-').Replace('/','_'))" |
      Set-Content "$Root\config\speaches.env" -Encoding UTF8
  ```

### 2. Extract standalone Ollama

Download the official standalone `ollama-windows-amd64.zip` release archive from
[Ollama GitHub Releases](https://github.com/ollama/ollama/releases) and extract
it into `C:\ADAM_VOICE\bin\ollama\v0.40.13\` so `ollama.exe` sits directly at
`C:\ADAM_VOICE\bin\ollama\v0.40.13\ollama.exe`.

### 3. Install scheduled tasks and the desktop control shortcut

From elevated PowerShell:

```powershell
.\deploy\model-host\install_control.ps1 -InstallRoot C:\ADAM_VOICE
```

*(Pass `-SkipSpeech` if you only want Ollama conversation, or `-PythonPath` if
`pythonw.exe` is in a custom location.)*

`install_control.ps1` copies the runtime scripts and `ADAM_GPU_Control.pyw` into
`C:\ADAM_VOICE`, registers the `ADAM-Ollama` and `ADAM-Speech` scheduled tasks,
and creates the **ADAM GPU Control** shortcut on your Desktop.

### 4. Restrict inbound Windows Firewall rules to your head-end

Allow only your ADAM head-end IP (for example `192.168.1.50`) to reach ports
`11434` (Ollama) and `8000` (Speaches):

```powershell
New-NetFirewallRule -Name 'ADAM-Ollama-In' -DisplayName 'ADAM Ollama from head-end' `
    -Direction Inbound -Protocol TCP -LocalPort 11434 -RemoteAddress 192.168.1.50 -Action Allow

New-NetFirewallRule -Name 'ADAM-Speech-In' -DisplayName 'ADAM Speaches from head-end' `
    -Direction Inbound -Protocol TCP -LocalPort 8000 -RemoteAddress 192.168.1.50 -Action Allow
```

### 5. Start services and pull your preferred model

Open **ADAM GPU Control** from your Desktop (accept the administrator prompt so
it can manage the `SYSTEM` tasks):

1. Click **Start** next to **Conversation model** (`ADAM-Ollama`).
2. Enter `qwen3.5:9b` (or another Ollama tag that fits your VRAM) in the
   download box and click **Download another model**.
3. Select the downloaded model in the dropdown and click **Use selected model**.
   This updates the **`adam:latest`** Ollama alias, saves `preferred_model` in
   `config\ollama.json`, and warms the model in GPU memory (`keep_alive: -1`).
4. Click **Start** next to **Speech recognition** (`ADAM-Speech`) to pull and
   warm the pinned Speaches CUDA container (`deepdml/faster-whisper-large-v3-turbo-ct2`).

### 6. Connect the head-end (`/models`)

Open `http://<headend-ip>:8765/models` on your ADAM head-end:

- **Speech recognition:**
  - Mode: `Prefer remote, fall back locally`
  - Service base URL: `http://<gpu-host-lan-ip>:8000/v1` *(note the `/v1` suffix here)*
  - Model ID: `deepdml/faster-whisper-large-v3-turbo-ct2`
  - API key: the `API_KEY` from `C:\ADAM_VOICE\config\speaches.env`
- **Conversation:**
  - Enable **Answer general questions**
  - Preferred computer Ollama address: `http://<gpu-host-lan-ip>:11434`
  - Model: `adam:latest` *(follows whichever model you select in **ADAM GPU Control**)*
  - Local fallback Ollama address: `http://127.0.0.1:11434`, model `qwen3.5:0.8b`

---

## Desktop Control Window Behavior

- **Start / Stop:** Clicking **Stop** disables that scheduled task and stops the
  process/container so it stays off across reboots until **Start** is clicked
  again. Model weights are never deleted.
- **Speaker home-screen ring:** Firmware v27 displays a persistent glowing edge
  ring on the speaker's idle sky screen—**green** when the preferred GPU
  conversation model is loaded in VRAM, **yellow** when using the head-end's
  local fallback, and hidden when conversation is disabled or unavailable.
- **Closing the window:** Leaves running background services active.

See [CONVERSATION.md](../../docs/CONVERSATION.md) for routing details and
`python tools/test_gpu_control.py` for offline controller unit tests.
