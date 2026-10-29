# Touch interface

Use the display with the USB cable pointing down.

| Control | Action |
| --- | --- |
| Left curved + / − | Change saved volume by 10, from 0 to 100 |
| Right curved + / − | Change backlight brightness by 10, from 10 to 100 |
| Top / bottom arrow | Cycle between Eye and Settings |
| Eye | Blink, or stop an active recording/playback |
| Settings → Play Eye | Return to the animated eye |
| Settings → Wi-Fi | Edit network name and password, connect, view status |
| Settings → Audio | Play a chime or 10-second tone; Stop ends playback |

Touch brightness has a 10% floor so the interface remains visible. The USB
brightness command can still set 0 for diagnostics. Levels save after one
second without further adjustment. One tap produces one step; holding or
dragging does not repeat. Volume controls speaker output, with a tapered gain
curve for finer adjustment at low levels; zero mutes the amplifier.

## Audio

Open Settings, then AUDIO. CHIME plays three ascending notes; TONE plays a
440 Hz test tone for ten seconds; STOP ends playback early. The left volume
rail works during playback. A new playback request replaces the current one.
The top arrow returns to Settings and the bottom arrow returns to the Eye.
Audio can continue while the eye animates or other menus are open. MIC TEST
opens a page with live microphone levels, RECORD 8S, REPLAY and CLEAR.
Recording returns to the eye with a countdown; tap the eye to stop early.
REPLAY plays microphone 1 from the completed clip and returns to the eye.
CLEAR releases the clip for reuse. There is one volatile stereo clip of at most
15 seconds; a new recording replaces it. Input gain defaults to 36 dB and can
be adjusted over USB. Clip replay scales its peak for a listening test; the
downloaded original is unchanged. A recording/Clear is refused while replay
owns the clip. The separate ADC3 echo reference is not captured in this mode.

The local test UI at `http://127.0.0.1:8765` can record/fetch over USB, play
the original or either channel on the laptop, and request local transcription.
The current mic recordings sound garbled in the box replay; clarity is under
investigation. Tone capture and buffer checks pass, which does not establish
speech quality. File upload to the board and Wi-Fi streaming are future work.
See [AUDIO_AND_CAPACITY.md](AUDIO_AND_CAPACITY.md) for measurements.

## Wi-Fi

Opening Wi-Fi automatically scans nearby 2.4 GHz networks in the background.
Tap the network-name field or the FOUND/SCAN button to open the list. Names are
sorted by signal strength, with duplicate names combined; each row shows
security type, signal bars, and RSSI. Tap a row to fill in its network name.
Use < / > to page through results and RESCAN to refresh. Scanning alone never
connects or saves a network. Up to 32 of the strongest access points are read
per scan; hidden names are omitted. Unsupported security modes are labeled.

TYPE on the Wi-Fi page or MANUAL in the list opens name entry for hidden
networks. Tap the password field to edit its value. The lower-left key cycles lowercase,
uppercase, numbers/symbols, and additional symbols. SPC inserts a space; DEL
removes the last character; OK accepts the edited field. The top arrow cancels
an edit or returns to Settings. Password characters are visible both while
typing and in the settings field, as requested for easier verification on the
small screen. Fields show the last 18 characters when the value is longer.

Enter the name of a 2.4 GHz network and its password, then tap CONNECT.
The name supports up to 32 ASCII bytes. Personal-network passwords support
8–63 printable ASCII characters; leave the password empty for an open network.
The firmware uses WPA2/WPA3 personal authentication for password-protected
networks. Enterprise Wi-Fi, Unicode keyboard entry, and captive-portal login
are not implemented.

CONNECT saves credentials in the device's NVS and attempts a connection.
Status changes to CONNECTING, CONNECTED (with the local IP), or JOIN FAILED.
Saved credentials are retried at startup. FORGET NETWORK asks for confirmation
before clearing the saved credentials. Password values do not appear in USB status
output, and raw touch-event logging is disabled in the UI. NVS is local flash
storage; flash encryption is not configured in this development firmware.

## USB and development

```powershell
python tools/control.py menu eye
python tools/control.py menu settings
python tools/control.py ui status
python tools/control.py touch status
py -3.12 tools/snapshot.py artifacts/screen.png
```

`ui tap X Y` injects a logical-coordinate tap into the same UI handler for
repeatable checks. It does not verify physical touch accuracy. `tools/ui_check.py`
checks limits, menu routing, keyboard pages, and cancel/keep actions, then
restores the initial levels. It neither connects nor modifies saved credentials.
Run this while no one is operating the screen. `tools/scan_check.py` exercises
three real scans, paging, selection, and manual entry without connecting.

`touch target X Y` replaces the UI with a target and enables raw down/up
diagnostics. `touch status` reports the last contact sample, press/release
counts, I2C errors, and queue drops. `menu eye` restores normal operation and
disables touch logging. Touch coordinates undo display rotation/mirroring
before UI hit testing; default orientation has no calibration transform.

Implementation: `ui.c` owns layout and page transitions, `touch.c` polls the
controller and queues contact events, `preferences.c` handles NVS and Wi-Fi
without waiting for network connection in the render loop. The application
partition is 3 MB; NVS retains the original 0x9000 offset and 24 KB size.

The system event task uses a 6144-byte configured stack to handle DHCP/IP
events with the native USB console. `ui status` includes `event_stack_min`,
the sampled minimum remaining event-task stack in bytes. The connection
reboot fix measured 3660 bytes remaining through startup, scans and reconnect.

The lower case button remains hardware RESET and the upper case button BOOT;
their electrical functions are unchanged.
