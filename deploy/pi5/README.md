# ADAM on Raspberry Pi 5 (Headless Docker)

Run the ADAM head-end headless on an 8 GB Raspberry Pi 5 (or any 64-bit ARM64 /
x86_64 Debian/Ubuntu host with Docker Engine and the Compose plugin).

## Prerequisites

- **Hardware:** Raspberry Pi 5 (8 GB recommended), active cooling, reliable 5 V /
  3 A+ USB-C power supply, and microSD or NVMe storage with at least 20 GiB free.
- **OS:** 64-bit Raspberry Pi OS (Debian Bookworm or Trixie). Headless servers can
  use `sudo systemctl set-default multi-user.target` once SSH and networking are verified.
- **Networking:** Reserve a static DHCP lease on your router for the Pi's IPv4
  address (for example `192.168.1.50`). Speaker firmware strictly requires an
  `http://<RFC1918-IPv4>:8765` head-end URL (hostnames such as `.local`, HTTPS,
  and non-8765 ports are rejected by the device).
- **Docker:** Install Docker Engine and the Compose plugin following the
  [official Debian instructions](https://docs.docker.com/engine/install/debian/).

Keep the training archive capped (10 GiB default cap) and ensure sufficient free
disk space. Archive limits pause recording collection without interrupting live
voice commands. Docker container logs rotate automatically at three 10 MB files.

---

## Option A: Fresh installation

1. **Clone the repository and create host runtime directories:**

   Because the container runs unprivileged (`1000:1000` by default) with a
   read-only root filesystem, create the bind-mounted directories first so Docker
   does not create them owned by `root`:

   ```sh
   git clone https://github.com/UltimoAries/adam.git ~/adam
   cd ~/adam
   mkdir -p headend/data headend/models/stt headend/models/voices headend/models/cache headend/models/llm headend/data/ollama
   chown -R "$(id -u):$(id -g)" headend/data headend/models
   ```

2. **Configure `deploy/pi5/.env`:**

   ```sh
   cd ~/adam/deploy/pi5
   cp .env.example .env
   ```

   Edit `.env` and set:
   - `ADAM_LAN_IP`: your Pi's reserved LAN IPv4 address (e.g., `192.168.1.50`).
   - `ADAM_HOSTNAME`: optional mDNS/display hostname (e.g., `adam.local`).
   - `ADAM_UID` / `ADAM_GID`: output of `id -u` and `id -g` on the Pi.

3. **Build the image and download local speech models:**

   Normal runtime disables implicit Hugging Face downloads (`HF_HUB_OFFLINE=1`)
   and mounts `headend/models` read-only. Provision the pinned CPU INT8
   `faster-whisper` (`base.en`) and starter Piper voice models once using a
   temporary writable mount:

   ```sh
   docker compose config --quiet
   docker compose build
   docker compose run --rm --no-deps \
     -e HOME=/tmp -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 \
     -v "$(pwd)/../../headend/models:/app/headend/models:rw" \
     adam sh -c "python -m tools.fetch_stt_model && python -m tools.fetch_voice_model"
   ```

4. **Start the head-end:**

   ```sh
   docker compose up -d
   docker compose ps
   docker compose logs --tail 80 adam
   ```

   Wait until `http://127.0.0.1:8765/api/health` reports `"status": "ok"` and
   `"model_state": "ready"`.

5. **Create the initial administrator account (via SSH tunnel):**

   For security, first-time account creation (`/api/auth/setup`) is restricted to
   loopback (`127.0.0.1`). From your desktop or laptop, forward port `8765` over SSH:

   ```sh
   ssh -L 8765:127.0.0.1:8765 <your-pi-user>@<pi-lan-ip>
   ```

   Open <http://127.0.0.1:8765> in your browser and create the administrator
   account (**password must be 12–128 characters**). Afterward, you can sign in
   directly from your LAN at `http://<pi-lan-ip>:8765`.

6. **Pair a new speaker unit over USB:**

   Configure 2.4 GHz Wi-Fi on the speaker's touch screen (`Settings -> Wi-Fi`),
   plug the speaker into the Pi by USB, confirm its serial device and `dialout`
   group ID in `.env` (`ls -l /dev/ttyACM*`, `getent group dialout`), and include
   `compose.usb.yaml` while pairing from the web dashboard (**Add a USB-connected unit**):

   ```sh
   docker compose -f compose.yaml -f compose.usb.yaml up -d
   ```

   Once paired over Wi-Fi, you can unplug USB and return to `docker compose up -d`.

---

## Option B: Migrate an existing installation

To move an existing head-end (accounts, Home Assistant credentials, paired units,
voices, and training archive) from another machine without re-creating state:

1. **Package source and export runtime data on the source machine:**

   Stop the source head-end while idle, then create a source tarball and a
   verified runtime backup ZIP:

   ```powershell
   .\tools\stop_headend.ps1
   py -3.12 tools/package_pi_headend.py --output artifacts/adam-pi5-source.tar.gz
   py -3.12 tools/migrate_headend.py export --output backups/adam-runtime-for-pi.zip
   ```

   > **Important:** The runtime ZIP contains password hashes, Home Assistant
   > tokens, device credentials, and voice recordings. Copy it privately over
   > SSH/SCP and never commit it to Git.

2. **Restore on the Pi:**

   Extract the source archive (or clone the repo) into `~/adam`, then restore:

   ```sh
   cd ~/adam
   python3 tools/migrate_headend.py restore /path/to/adam-runtime-for-pi.zip --destination headend
   ```

   `restore` verifies SHA-256 checksums for every file and refuses to overwrite
   existing `headend/data` or `headend/models` directories. Configure
   `deploy/pi5/.env`, then run `docker compose build && docker compose up -d`.

3. **Repoint an already-paired speaker without duplicating its unit ID:**

   Attach the speaker temporarily via USB and run `headend.reconnect_unit`:

   ```sh
   docker compose -f compose.yaml -f compose.usb.yaml run --rm adam python -m headend.reconnect_unit --list
   docker compose -f compose.yaml -f compose.usb.yaml run --rm adam python -m headend.reconnect_unit --unit <existing-unit-id> --url http://<pi-lan-ip>:8765
   ```

   Reconnection updates the head-end URL and rotates the device token in the Pi's
   database while preserving its unit ID, name, and history. To roll back to the
   previous host later, stop the Pi container, start the previous host, and run
   `reconnect_unit` against that host's database (changing only the URL is not
   sufficient because tokens are rotated on reconnect).

---

## Optional local LLM fallback (`compose.llm.yaml`)

Conversation can prefer a larger Ollama model on a LAN GPU machine (see
[deploy/model-host/README.md](../model-host/README.md)) and fall back to a small
CPU model (`qwen3.5:0.8b`) on the Pi:

```sh
mkdir -p ../../headend/models/llm ../../headend/data/ollama
chown -R "$(id -u):$(id -g)" ../../headend/models/llm ../../headend/data/ollama
docker compose -f compose.yaml -f compose.llm.yaml up -d
docker compose -f compose.yaml -f compose.llm.yaml exec ollama ollama pull qwen3.5:0.8b
```

The Pi's Ollama service binds strictly to `127.0.0.1:11434`. Enable and test
conversation routing at `http://<pi-lan-ip>:8765/models`. See
[CONVERSATION.md](../../docs/CONVERSATION.md) for timeouts, memory notes, and
idle home-ring status behavior.

---

## Verification

- Check host readiness (board, thermal/throttling flags, free disk, Docker) with
  `python3 tools/check_pi_host.py`.
- Smoke-test live speech synthesis and transcription inside the running service
  without polluting the training archive:
  ```sh
  docker compose exec -T adam python - < ../../tools/check_live_speech.py
  ```
- Verify SQLite integrity, recording SHA-256 hashes, and read-only Home Assistant
  reachability:
  ```sh
  docker compose exec -T adam python - < ../../tools/check_pi_runtime.py
  ```

References: [Raspberry Pi SSH](https://www.raspberrypi.com/documentation/computers/remote-access.html#ssh),
[CTranslate2 ARM64 support](https://opennmt.net/CTranslate2/hardware_support.html).
