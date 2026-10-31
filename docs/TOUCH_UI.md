# Touch interface

Operate the Waveshare **ESP32-S3-Touch-LCD-1.85C-BOX V2** with the **USB-C port
pointing down**. On the enclosure side in this orientation:

- **Upper physical button:** `BOOT` (`GPIO0`) — hold while resetting to force ROM download mode if needed.
- **Lower physical button:** hardware `RESET`.

Firmware v27 boots into the [illustrated sky home screen](SPACE_PANEL.md).
Controls start hidden so the clock and weather scene remain unobstructed. Tap
anywhere once to reveal the curved volume, brightness and navigation controls;
they hide automatically after eight seconds of inactivity. That first tap never
changes a level or opens a menu (except tapping the center during active capture
or playback, which stops it immediately).

## Screen map

| Control / page | Action |
| --- | --- |
| Left curved `+` / `−` | Adjust saved speaker volume by 10 (`0` to `100`; `0` mutes the amplifier) |
| Right curved `+` / `−` | Adjust saved backlight brightness by 10 (`10` to `100`) |
| Top / bottom arrow | Switch between Home and Settings (or step back from submenus) |
| Center tap | Hide visible controls when idle, or cancel active listening/playback immediately |
| **Settings → Home** | Return to the live sky home screen |
| **Settings → Voice** | Toggle on-device **Hey Adam** wake detection (mute/enable), start a **Talk 8S** Wi-Fi capture, or view head-end pairing/poll status |
| **Settings → Wi-Fi** | Scan 2.4 GHz networks, enter SSID/password, connect, or forget saved credentials |
| **Settings → Audio** | Play a test chime or 10-second tone, stop playback, or open **MIC TEST** |

Touch brightness enforces a 10% minimum so the screen cannot be accidentally
blanked by touch (`brightness 0` remains available over USB for diagnostics).
Volume and brightness persist to NVS one second after the last adjustment. Each
tap steps once; holding or dragging does not auto-repeat. Volume uses a tapered
gain curve for finer control at low listening levels.

## Voice and head-end

Open **Settings → Voice** to manage network voice features on the unit:

- **Wake word toggle (mute / enable):** Enables or mutes local on-device
  **Hey Adam** (`hey_adam-20261030-c60f0456`) detection without clearing Wi-Fi
  or pairing. Muting pauses wake-triggered listening while leaving announcements
  and touch controls available; the setting persists across reboots.
- **Talk 8S:** Starts an immediate eight-second Wi-Fi microphone upload to the
  paired head-end (prepended with up to two seconds of rolling history) and
  returns to Home with green listening mist. Tap the center of the screen or
  **Stop** to cancel early.
- **Head-end endpoint rules:** Units pair over USB from the head-end dashboard
  (or via `headend config` over serial) using an explicit private LAN origin of
  the form **`http://<RFC1918-IPv4>:8765`** (for example
  `http://192.168.1.50:8765`). Use a static DHCP reservation or static LAN IPv4
  address for the Raspberry Pi head-end so paired speakers keep reaching it
  across router restarts. See [HEADEND_NETWORK.md](HEADEND_NETWORK.md).

## Audio and diagnostic microphone test

Open **Settings → Audio**:

- **CHIME** plays three ascending test notes; **TONE** plays a 440 Hz sine wave
  for ten seconds; **STOP** ends playback immediately.
- **MIC TEST** opens local hardware diagnostics showing live stereo ES7210 levels,
  **RECORD 8S**, **REPLAY** and **CLEAR**.
  - **RECORD 8S** captures clean 16 kHz stereo PCM into PSRAM while showing green
    mist on Home; tap the center to stop early.
  - **REPLAY** plays recorded channel 1 back through the speaker (scaled for
    audibility without modifying the raw buffer).
  - **CLEAR** frees the volatile diagnostic buffer (up to 15 seconds; replaced on
    the next diagnostic recording).

For browser-based waveform inspection, channel selection and USB clip download,
open `/bench` on a local head-end connected over USB (see
[HEADEND_TEST_UI.md](HEADEND_TEST_UI.md)). Normal wake questions, weather
answers, Home Assistant commands, conversation replies and multi-unit paging all
stream over Wi-Fi without a USB connection once paired.

## Wi-Fi rules and setup

Opening **Settings → Wi-Fi** automatically scans nearby **2.4 GHz** networks in
the background:

- **Supported networks:** **2.4 GHz (802.11b/g/n)** using **WPA2/WPA3 Personal**
  (or open networks with an empty password).
- **Character and length limits:** Network SSIDs support **1–32 printable ASCII
  bytes**. Passwords support **8–63 printable ASCII characters**.
- **Unsupported modes:** 5 GHz bands, WPA2/WPA3-Enterprise (802.1X), captive
  portals, hidden-SSID auto-discovery (use **TYPE** / **MANUAL** for hidden
  2.4 GHz SSIDs), and non-ASCII Unicode characters are not supported.

Tap the network-name field or **FOUND/SCAN** to browse discovered access points
sorted by RSSI, with `<` / `>` paging and **RESCAN**. Selecting a row populates
the SSID without connecting until you tap **CONNECT**. In the on-screen keyboard,
the lower-left mode key cycles lowercase, uppercase, digits/symbols and extended
ASCII punctuation; `SPC` inserts a space, `DEL` removes one character, `OK`
accepts the edit, and the top arrow cancels. Characters remain visible on-screen
to simplify entry on the 1.85-inch round LCD, while USB serial status output
redacts saved passwords.

**CONNECT** stores credentials in local NVS flash and joins asynchronously
(`CONNECTING` → `CONNECTED` with the assigned IPv4 address, or `JOIN FAILED`).
Saved Wi-Fi credentials reconnect automatically on boot; **FORGET NETWORK**
prompts for confirmation before erasing them.

## USB UI diagnostics

```powershell
python tools/control.py menu home
python tools/control.py menu settings
python tools/control.py ui status
python tools/control.py touch status
python tools/snapshot.py artifacts/screen.png
```

`ui tap X Y` injects logical coordinates into the UI handler for automated
regression checks (`tools/ui_check.py` and `tools/scan_check.py`). `touch target X Y`
displays a calibration crosshair and enables raw CST816 contact logging until
`menu home` or `touch off` is run.

Implementation modules: `firmware/main/ui.c` (screen layout and hit testing),
`touch.c` (CST816 polling and orientation unmapping), and `preferences.c` (NVS
persistence and asynchronous Wi-Fi management with a 6,144-byte system event task
stack). The factory application partition is 3 MiB, keeping NVS at `0x9000` (24 KiB).
