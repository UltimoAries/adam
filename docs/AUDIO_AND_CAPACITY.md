# Audio output and capacity

Measured on the connected Waveshare ESP32-S3-Touch-LCD-1.85C-BOX V2 on
October 28, 2026, using ESP-IDF 5.5.1. The original v4 baseline is retained
below; v5 added microphone capture and recorded-clip replay. The current v6
trial uses the vendor's native 32-bit I2S slots while keeping 16-bit clip files.

## Speaker output

Settings → Audio contains CHIME, TONE, MIC TEST and STOP. The left volume controls
now change actual output. Zero mutes; settings persist. A new sound replaces
an active sound. Playback has its own task, so eye drawing and touch continue.
MIC TEST records a short stereo clip, then REPLAY plays microphone 1.
General file playback, speech synthesis on the device and network audio
streaming are future work. The local web UI accepts WAV files and can play
them on this laptop; see [HEADEND_TEST_UI.md](HEADEND_TEST_UI.md).

The V2 output chain is ES8311 codec → NS4150B amplifier → speaker. The codec
responded at I2C 0x18 with chip ID 0x8311. I2C uses SDA 11/SCL 10, shared with
touch and the expander. I2S uses MCLK 2, BCLK 48, LRCK 38, DOUT 47; amplifier
enable is GPIO 15. This wiring is specific to this V2 board.

Current input/output share 16 kHz, 32-bit stereo I2S with six 160-frame
buffers per direction (7,680 bytes per direction, 60 ms). Clips use the upper
16 bits of each input word; playback shifts 16-bit values into 32-bit slots.
The worker runs on core 1 with
a 4,096-byte stack. Tone peaks are capped at 1/8 full scale before the volume
curve, DAC gain is 0 dB, and short fades reduce abrupt transitions. The amp
is disabled while idle and at volume zero. This deliberately conservative
test level is not a measurement of the speaker's maximum loudness.

Build, flash, codec readback, generated playback, UI routing and driver-error
checks passed. The user confirmed three clear ascending notes in person when
the chime was replayed at volume 20. Output level follows the user's saved setting.
See validation-v4.md (`artifacts/validation-v4.md`, local only).

## V5 capture measurements (16-bit I2S trial)

With the eye, connected Wi-Fi, active 15-second stereo recording and muted
tone processing, the eye ran at 17.9–18.0 fps. Combined CPU use was
31.2–31.5%; the microphone worker used about 2.2% of one core. No capture
overruns or playback errors were reported. Internal heap remained about
190.6 KiB free, with a 116 KiB largest block and 185.4 KiB observed minimum.
PSRAM had 6.55 MiB free after reserving a 960,000-byte maximum clip buffer.
The image is 909,808 bytes, leaving 2.13 MiB in the 3 MiB app partition.
Main/audio/mic stack minima were 2,480/2,776/3,200 bytes.

Both microphones captured a 440 Hz tone at the correct frequency without
clipping. Captured speech currently sounds garbled/robotic in box playback;
local STT works on a clean reference but has not recovered the microphone test
sentence. Buffer/timing success does not establish speech fidelity. Input
gain is adjustable, currently defaulting to 36 dB. The separate ADC3 hardware
echo reference is not captured in the current two-channel mode.

## Original v4 headroom baseline

The board has two 240 MHz CPU cores, 512 KiB of physical internal SRAM,
8 MiB PSRAM and 16 MiB flash. Internal heap below is what the running firmware
can allocate after static code/data and system reservations; it is not the
whole physical SRAM capacity.

| Resource | Observed with eye, connected Wi-Fi and active test-audio processing |
| --- | --- |
| CPU core 0 | 59.2–59.4% busy |
| CPU core 1 | 1.4% busy; audio task about 1.3% of one core |
| Both cores, combined | 30.3–30.4% busy, about 70% aggregate headroom |
| Eye frame rate | 18.2–18.3 fps; baseline without audio 18.3–18.4 fps |
| Internal heap free | 202,383 bytes, about 198 KiB, out of 338 KiB managed heap |
| Lowest internal heap observed | 195,067 bytes, about 190 KiB |
| Largest contiguous internal allocation available | 126,976 bytes, 124 KiB |
| DMA-capable internal heap free | About 190 KiB; a subset of internal RAM |
| PSRAM free | 7,839,488 bytes, 7.48 MiB out of 8 MiB |
| Firmware image | 903,184 bytes, 0.86 MiB |
| Current 3 MiB application partition | 2.14 MiB free, 71% |
| Flash outside current partitions | 12.94 MiB, not yet usable as files |
| Stack minimum remaining | Main 2,576 bytes; audio 2,792; Wi-Fi event task 3,648 |

The longer profile ran with volume zero: sample synthesis, I2S clocks, DMA
and playback scheduling remained active, while the speaker amplifier was off.
It measures processing cost, not acoustic performance or speaker power draw.
Wi-Fi was connected with light traffic; this is not a streaming/TLS stress test.
A Wi-Fi scan during playback completed without a reset or reported I2S queue
overflow/write error. Opening Settings reduced combined CPU use to about 17%.

Raw readings: audio-resource-profile.json (`artifacts/audio-resource-profile.json`, local only).
`resources` exposes these counters for future feature work. CPU estimates use
short two-second runtime windows and include scheduler/interrupt accounting
effects; use them as approximate load indicators, not hard timing guarantees.

## What can fit next

Additional menus, eye expressions, short sounds, saved presets and simple
network messages have substantial room at the measured baseline. Buffered
speech or music playback is a reasonable next step, but decoder, TLS and
network-buffer demands must be measured together with the animation. A laptop
or server can generate speech and richer AI responses while the board renders
the eye and plays received audio. That keeps heavy inference off the device.

The first limits to watch are:

1. **Display frame time.** A full frame currently takes about 23 ms to render
   and 30–31 ms to transfer. More complex graphics in the same main task will
   reduce frame rate. Partial redraws, cached static UI, or overlapping rendering
   with transfer would be more useful than simply adding buffers.
2. **Internal RAM and task stacks.** DMA and some system work need internal
   memory; free PSRAM cannot replace every allocation. Large audio/network
   buffers should use PSRAM where supported. Track the largest block and each
   task's stack, not just total free memory. The previous Wi-Fi reboot was an
   undersized event-task stack despite abundant free PSRAM.
3. **Flash layout and assets.** Current firmware has plenty of application
   space, but there is no file partition. Allocate a filesystem or use the
   board's TF/microSD slot before adding a sound library. OTA updates would
   also need space reserved for another firmware slot.

For scale, one 360×360 RGB565 frame is 259,200 bytes (253 KiB). One minute of
24 kHz, 16-bit mono PCM is 2.88 MB; 128 kbit/s compressed audio is about
0.96 MB/minute, excluding headers. Compression trades storage for decoding
work. None of those file formats or storage devices are enabled by this test.

## Voice and paging measurement, October 28, 2026

Firmware v10/v11 adds native microWakeWord, an independent Wi-Fi audio worker,
and bounded PCM paging. With the eye, Wi-Fi and local Hey Jarvis model active,
the measured load was about **50% across both cores**: core 0 about 63%, core 1
about 37%. Wake inference used about 32–33% of one core, capture about 3.3%,
and idle audio about 0.5%. The eye ran about **15–16 fps**. Listening replaces
the eye with a cheaper animated ear and pauses wake inference.

At that measured point about **132 KiB internal heap** and **6.49 MiB PSRAM**
were free. The largest internal block was 56 KiB, so contiguous allocation
headroom still matters. Stack minima after real Wi-Fi uploads were about
1.9 KiB for the network worker and 5.1 KiB for the wake worker. Microphone
read errors, RX overflows and wake queue overflows remained zero.

The v11 image with the current placeholder cue is **1,394,880 bytes**, leaving **56% of the 3 MiB app partition**
free. The stock model occupies 52,272 flash bytes; the current runner reserves
at least 64 KiB of PSRAM tensor arena plus its smaller state/feature buffers.
One 15-second received mono page uses at most 480,000 PSRAM bytes. The local
stereo diagnostic recording buffer remains 960,000 bytes.

These are measurements from short tests on one unit, not an overnight load or
false-trigger test. HTTPS, acoustic echo cancellation, live intercom and a
different trained wake model must each be measured when added. The implemented
transport and next stages are in [HEADEND_NETWORK.md](HEADEND_NETWORK.md).

## Pre-roll update, firmware v12

Immediate capture and two seconds of pre-roll now use **384,000 bytes (375 KiB)
of PSRAM**: two 64,000-byte rolling-history banks plus a 256,000-byte live
session spool. The former 16-block internal-RAM upload queue is removed.
Triggering swaps history banks without copying the complete two seconds;
normal operations copy at most a 20 ms packet while holding the buffer lock.

The v12 image with the final chime is **1,372,912 bytes**, leaving **1,772,816 bytes** in
the 3 MiB app partition. One idle sample with wake, eye and Wi-Fi active had
**140,375 bytes internal heap** and **6,417,212 bytes PSRAM** free. Combined CPU
was **50.5%**, capture used **3.6% of one core**, and wake used **33.3%**.
After a real upload, network-worker stack headroom was **2,520 bytes**.
Microphone read errors, RX overflows and input gaps were zero in these checks.

The hardware buffer regression check passed, and a real upload retained exactly
160,000 mono samples: two seconds before the trigger plus eight after it.
There is no voice activity endpoint or acoustic echo cancellation yet. The
brief trigger chime can appear in this preserved recording, especially
when a command overlaps it. These remain short single-unit measurements.

## Primary references

- [Waveshare board specifications and V2 hardware](https://docs.waveshare.com/ESP32-S3-Touch-LCD-1.85C)
- [Pinned manufacturer audio example](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.85C/tree/8ead4a96bf3a278fc4ebd8ef4768657e17fa2880/Arduino/examples/03_audio_out_no_tf)
- [ES8311 datasheet, including DAC gain register 0x32](https://files.waveshare.com/wiki/common/ES8311.DS.pdf)

Relevant vendor files are preserved under `vendor/`; `tools/fetch_audio_reference.py`
verifies each downloaded file against its pinned Git blob hash.
