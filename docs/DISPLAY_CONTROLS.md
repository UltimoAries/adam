# Display and animated-eye controls

Firmware v6 boots into an animated blue eye with touch menus. It runs on the
ESP32 independently of the laptop, looking around and blinking periodically.
The eye needs no network or image assets. Volume, brightness, and Wi-Fi
credentials persist; drawing/orientation/gaze settings reset on reboot.
Commands are newline-terminated ASCII on COM6. See [TOUCH_UI.md](TOUCH_UI.md).

## Eye commands

```powershell
python tools/control.py eye auto
python tools/control.py look -70 0
python tools/control.py look 70 0
python tools/control.py look 0 -70
python tools/control.py look 0 0
python tools/control.py blink
python tools/control.py pupil 35
python tools/control.py brightness 50
python tools/control.py eye auto
python tools/control.py status
```

`look X Y` switches to manual gaze, with -100..100 for each axis. Negative X is
left and negative Y is up in the unrotated canvas. Motion eases into the target.
`eye hold` stops automatic changes, continuing toward the last gaze target;
manual `blink` still works. `eye auto` resumes automatic gaze and blinking.
Pupil radius is 12..50 pixels; the default is 20 for the smaller v3 eye.

The requested frame rate defaults to 30. Measured performance is about 19 fps
(22 ms rendering plus 30 ms transfer). `fps 5..30` sets the requested limit.
`status` reports actual frame count/rate, rendering and transfer time, gaze,
eyelid closure percentage, and free PSRAM. Its frame-rate value is the last
measured interval and may be stale while showing a static canvas.

## Drawing and calibration commands

| Command | Meaning |
| --- | --- |
| `red` or `fill RRGGBB` | Stop animation and fill the canvas |
| `brightness 0..100` | Backlight PWM; 0 is off |
| `pattern colors` | Red, green, blue, white, black, gray bars |
| `pattern grid` | 30-pixel grid, direction labels and center at 180,180 |
| `rotation 0/90/180/270` | Clockwise rotation; 90 moves TOP to the right |
| `mirror 0/1 0/1` | Panel X/Y mirror bits relative to rotation |
| `rect x y width height RRGGBB` | Filled rectangle |
| `line x1 y1 x2 y2 RRGGBB` | One-pixel line |
| `circle x y radius RRGGBB filled` | Outline if filled=0; solid if filled=1 |
| `text x y scale RRGGBB words` | Block text, scale 1..6 |
| `display on/off` | Panel enable; retains scene and brightness |
| `status`, `help` | Diagnostics and command list |
| `menu eye/settings` | Open a touch UI page and disable raw touch logging |
| `ui status` | Page, saved levels, Wi-Fi state/IP, UI press count |
| `ui tap x y` | Inject a logical-coordinate press into the active UI |
| `touch status` | Controller ID, last raw point, contact/error counts |
| `touch target x y` | Show a diagnostic touch target; x/y 20..339 |
| `touch off` | Disable raw contact logging |
| `snapshot` | Binary framebuffer; use tools/snapshot.py, not control.py |

## Audio and resource commands

| Command | Meaning |
| --- | --- |
| `volume 0..100` | Set and save real speaker volume; 0 mutes |
| `audio test` | Play a three-note chime |
| `audio tone HZ MS` | Play a tone, 100..4000 Hz for 100..30000 ms |
| `audio stop` | Stop active or queued playback |
| `audio status` | Codec readiness, playback state, buffer errors and task stack |
| `mic status` | Live input level, recorded frames, gain, clipping and capture errors |
| `mic record MS` | Record stereo input for 100..15000 ms, in 10-ms steps |
| `mic stop` / `mic clear` | Stop early / clear a completed clip |
| `mic gain DB` | Input gain 0..36 dB in 3-dB steps; default 36, not persisted |
| `mic play 1/2` | Replay a recorded microphone channel on the box |
| `mic download` | Binary stereo PCM; use tools/mic_capture.py or the local web UI |
| `resources` | Internal/DMA/PSRAM heap, CPU load by core/task, flash partition usage |
| `headend status` | Stream state, pre-roll/history duration, captured duration, bytes and input gaps |
| `headend listen` / `headend stop` | Start immediate eight-second Wi-Fi capture with up to two seconds of pre-roll / cancel |
| `headend selftest` | Run buffer wraparound, ordering, delayed-reader and gap checks without altering live audio |

CPU estimates use two-second FreeRTOS runtime windows; ignore values while
`valid=0`. Task percentages are relative to one core; `combined_busy` is the
average of both cores. Internal and DMA heap figures overlap and must not be
added together. Stack minimums are bytes remaining. Flash `unallocated` is
unused space beyond the current partitions, not a mounted filesystem.

```powershell
python tools/control.py volume 20
python tools/control.py audio test
python tools/control.py audio status
python tools/control.py resources
```

`tools/profile_resources.py` exercises live I2S playback at volume zero,
Audio-page hit regions, stop, input bounds, and a concurrent Wi-Fi scan. It
requires an existing Wi-Fi connection, saves measurements under `artifacts/`,
restores the original volume, and leaves the eye active. Run while nobody is
operating the device. It does not verify sound quality or physical touch.

`py -3.12 tools/mic_capture.py artifacts/recordings/test.wav` downloads the
completed clip, its separate mono channels, and a numeric analysis. Add
`--record 8` to start a fresh timed recording. `tools/check_microphone.py`
checks capture/playback concurrency, bounds, clip ownership and UI routing.
Its longer audio test is muted and restores the output volume afterward.
USB binary downloads briefly occupy the main task; use capture-time metrics
to assess animation performance. The local test UI is described in
[HEADEND_TEST_UI.md](HEADEND_TEST_UI.md).

## Drawing behavior

Coordinates are 0..359, with X increasing right and Y increasing down at
rotation 0. Width, height and radius are 1..360. Shapes are clipped to the
framebuffer; the physical circular panel hides its corners. Text supports
upper/lowercase letters, digits, spaces and printable ASCII punctuation.
Arbitrary bitmap upload is not implemented.

Drawing commands stop animation and paint onto the last rendered canvas.
Start with `fill` for a clean background. `eye auto` replaces the canvas with
the animated eye. Rotation and mirror settings apply to both the canvas and eye.
At rotation 0, `mirror 1 0` flips left/right; `mirror 0 1` flips top/bottom.
Combined settings follow the panel's MADCTL axes.

```powershell
python tools/control.py fill 080C14
python tools/control.py circle 180 180 70 00BBDC 1
python tools/control.py text 105 65 3 FFFFFF HELLO
python tools/control.py rotation 0
python tools/control.py mirror 0 0
python tools/control.py eye auto
```

## Implementation

- `firmware/main/main.c`: board startup, USB parser, orientation and DMA transfer.
- `firmware/main/graphics.c`: bounded canvas primitives and diagnostic patterns.
- `firmware/main/eye.c`: precomputed iris texture/background, eased gaze and blink.
- `firmware/main/ui.c`, `touch.c`, `preferences.c`: touch UI, input, saved settings and Wi-Fi.
- `firmware/main/audio.c`: ES8311/I2S speaker playback; `metrics.c`: live resource counters.
- `firmware/main/mic.c`: ES7210 capture, volatile clip buffer and input diagnostics.
- CPU 240 MHz; octal PSRAM 80 MHz; QSPI 40 MHz; RGB565 high byte first.
- Framebuffer and textures live in PSRAM; 12-row internal DMA buffer is reused
  only after transfer completion. USB is serviced between frames.
- Display on/off is implemented. Full panel sleep/wake and power-cycle tests
  are not part of the completed validation.

## Repeating checks

```powershell
python tools/serial_check.py
py -3.12 tools/bench.py colors
py -3.12 tools/bench.py brightness
py -3.12 tools/bench.py mapping
py -3.12 tools/bench.py eye
py -3.12 tools/analyze_bench.py
```

Camera tests require webcam access; Python 3.12 has OpenCV installed. Manual
exposure -6 worked for this setup, and scripts restore automatic exposure.
The color/brightness camera tests ran successfully. Camera mapping/eye checks
were blocked by an automatic-approval-review usage-limit failure, so specific
orientation and eye appearance checks used human feedback. See the validation
record for the distinction between visual checks and USB acknowledgements.
