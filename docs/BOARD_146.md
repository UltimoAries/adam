# Waveshare ESP32-S3-Touch-LCD-1.46

ADAM's separate `v31-146` firmware target supports the non-suffixed 1.46-inch
board. The original 1.85C BOX V2 remains the default build target (`v31-185`).
Do not flash either target onto the other hardware: GPIO15 is an amplifier
enable on the 1.85C V2 but a microphone clock on the 1.46.

## Hardware and shared features

Both boards have an ESP32-S3, 16 MB flash, and 8 MB octal PSRAM. The 1.46 has an
SPD2010 412x412 QSPI LCD and SPD2010 I2C touch, a PCM5101 DAC and speaker, and one
digital I2S microphone. Reference:
[Waveshare hardware documentation](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.46).

The target reuses the existing Hey Adam wake word, pre-roll, silence endpoint,
Wi-Fi setup, pairing, message paging, weather home screen, GPU/Pi status ring,
countdown timers, and head-end voice routing. Home Assistant, STT, conversation,
and TTS continue to run on the head-end and optional GPU host.

The UI renders in its established 360x360 coordinates and scales to 412x412.
Touch uses the inverse scaling, preserving existing hit regions and gestures.
Rotation uses software mapping because the SPD2010 driver cannot swap axes.
USB framebuffer snapshots remain 360x360 logical frames.

The microphone uses I2S1 (BCLK15, WS2, DIN39, right slot); the speaker uses I2S0
(BCLK48, WS38, DOUT47). Input starts at 12 dB digital gain with saturation before
conversion to PCM16. Stereo diagnostic recordings contain the single microphone
duplicated into both channels; these are not two independent microphones.
The shared-clock loopback diagnostic is unavailable on this board. Digital
volume is retained, alongside the physical analog volume control.
If speech is barely audible with the touchscreen at 100, raise the board's
physical volume knob gradually. The touchscreen cannot override analog
attenuation from that knob.
The 1.46 target applies 2.5x output gain (about +8 dB) before conversion to the
DAC's 32-bit slots. Normal head-end pages peak at 12000 PCM16 units, so this
brings their peaks to 30000 without digital clipping. Unexpected louder input
is saturated to PCM16 bounds. User volume and mute still apply; the original
1.85C target retains unity output gain. `audio status` reports `output_gain`.

Microphone range, acoustic quality, and speaker loudness require physical
comparison; identical application features do not imply identical acoustics.
Battery percentage, battery shutdown controls, IMU gestures, RTC persistence,
and SD-card playback are not implemented by this port. USB power is the initial
supported operating mode. No battery-runtime claim is made.

## Build and restore

```powershell
# Separate sdkconfig.146 and build146 outputs; original build stays separate.
.\tools\idf146.ps1 build
.\tools\idf146.ps1 -p <new-board-port> flash

# Original 1.85C BOX V2 only:
.\tools\idf.ps1 build
```

Before the first flash, read the board's complete original flash with esptool
and save its SHA-256 under ignored local artifacts. Keep backups private:
factory firmware may contain Wi-Fi credentials. Flashing writes the bootloader,
partition table, and application; it does not intentionally erase the NVS area.
An incompatible original NVS layout may still require separate provisioning.

Validate display, physical touch, USB diagnostics, mic recording/replay, wake
detection, Wi-Fi reconnection, targeted paging, and a harmless spoken timer or
time query before treating a new board as a replacement for an existing unit.

## Initial hardware validation

The port was built and flashed onto a non-suffixed 1.46 board after preserving
its full 16 MB factory flash. Both the original 1.85C and new 1.46 targets build
successfully. On the new hardware, validation covered touchscreen Wi-Fi setup,
pairing and reconnection after flashing, live microphone capture, local wake
detection, spoken clock replies, timer creation, targeted page playback, and
the pre-roll self-test. A microphone recording captured the expected 700 Hz
speaker test tone. The home display runs at approximately 14 FPS with the
current scaling path; the logical UI is not a native 412x412 redesign.

The initial optimized build booted with no audio write errors, microphone
overflows, or wake queue overflows. Playback acknowledgement confirms the
device consumed the audio, not its perceived loudness: physical listening and
range comparisons remain necessary. One earlier spoken reply was reported as
failed, while subsequent replies and the final targeted page completed; its
cause was not established. A timer was created through Pi STT fallback, with
an incorrect name from the transcript, and later clock requests transcribed
correctly through GPU STT. Home Assistant device actuation was not exercised
as part of this hardware check.

The first listening check found weak output even with both volume controls
raised. After the board-specific gain adjustment, the onboard microphone
measured approximately +7.2 dB at the 700 Hz test-tone fundamental and the user
confirmed spoken playback was louder and clear. This is a relative acoustic
check, not a calibrated sound-pressure or far-field comparison. Both firmware
targets were rebuilt after the audio change; only the 1.46 board was flashed.
After the audio diagnostics, one wake-buffer overflow was recorded, with no
increase on the follow-up check; wake processing remained active. Audio and
microphone I/O had no errors, and the pre-roll self-test passed while idle.

## Intercom

The v31 target includes experimental full-duplex drop-in with on-device echo
cancellation. In the mixed-board pilot, speech from this board to the 1.85 was
usable, but speech arriving from the 1.85 remained too quiet. Ordinary spoken
messages were louder. Treat this as a practically one-way proof of concept;
the underlying cause is unresolved, not a confirmed hardware restriction.
Further tuning is deferred in favor of validating two 1.85 units.

This board has separate capture/playback I2S clocks. If testing resumes, resolve
the receive level and test sustained calls and overlapping speech before
treating acoustic performance as stable.
See [Intercom](INTERCOM.md) for setup and validation.
