# Silence-ended questions and faster replies

Implemented October 28, 2026: firmware v13 and head-end v0.6.

Wake-triggered questions now finish after **1.2 seconds of quiet**, instead of
always recording eight seconds. The device retains its two-second pre-roll and
the quiet tail. A 600 ms pause inside a question is covered by the target-side
regression test; the operator also confirmed a half-second pause preserves the
whole question. The short chime and animated ear remain.

## Recording behavior

The on-device detector measures DC-corrected microphone energy in 10 ms blocks.
It estimates a noise floor from the quietest fifth of recent history, then looks
for sustained activity above that floor. This is an adaptive silence detector,
not a neural speech classifier or semantic understanding of a completed question.
TV, music or other nearby voices can prolong recording.

- The first 500 ms are excluded from endpoint decisions to avoid treating the
  chime as speech. Those samples remain in the uploaded audio.
- At least 30 ms of above-threshold activity resets the quiet timer.
- After speech, 1.2 seconds of quiet ends capture on a complete 20 ms packet.
- Minimum post-trigger capture is two seconds; no detected activity ends at
  five seconds. Continuous activity reaches a **13-second post-trigger cap**.
- Total retained audio is at most 15 seconds including two seconds of pre-roll.
- The diagnostic **Listen 8 sec** / touch Talk action remains fixed-length.
- Tapping the ear still cancels; cancellation does not transcribe a partial clip.

`preroll.c` contains the detector and buffer. `headend selftest` exercises both
fixed capture and adaptive capture, short pauses, quiet-tail retention, the
13-second limit, the no-activity timeout, history ordering, and gap invalidation.

## Variable-length upload

The existing device audio route accepts `X-Audio-Mode: silence-v1` for wake
sessions. `X-Audio-Frames` becomes an upper bound (at most 750). HTTP chunked
transfer carries the same sequential 644-byte records. A final 644-byte end record
contains little-endian `0xffffffff`, the actual packet count, a one-byte reason
(1 silence, 2 maximum duration, 3 no activity), and 635 zero bytes. The device
then writes the HTTP zero-length chunk terminator.

The server requires the valid end record, matching sequence count, at least two
post-trigger seconds, no trailing records, and a clean HTTP body completion.
Missing/invalid end records, packet gaps and disconnects abort without publishing
a partial recording. Old fixed-length clients remain supported. Displayed session
details include capture duration and endpoint reason.

## Local speech startup and delivery

Piper now keeps **one model** loaded in a separate process. File/package validation
still runs on each request; a changed package or voice causes a new model load.
The worker retains its existing memory, CPU-thread, 35-second request timeout,
200-character and 15-second speech-output bounds. A crashed or invalid worker is
discarded and a later request starts a fresh one. Shutdown closes the child.
This is a resource boundary, not a general-purpose security sandbox.

The head-end warms the selected reply voice and local recognizer during startup
using synthetic input; it does not record or play audio for warmup. Switching to
another voice can incur a first-use load. Device polling speeds up from 1.5 seconds
to 250 ms for 30 seconds after a question, reducing reply pickup delay without
raising normal idle polling traffic.

## Measurements and validation

The same weather sentence took **3,536 / 3,717 ms** to synthesize before the change.
With the model cached it took **405 / 387 ms**. Cold startup remains about 3.8 s,
now moved to head-end startup for the selected voice. These are synthesis timings,
not end-to-end response promises. Weather lookup, transcription and playback still
add time. Cached weather lookup measured under 1 ms; an uncached lookup took 896 ms.

The first real v13 weather requests recorded **4.58 s** and **2.66 s** after the
trigger, rather than eight seconds. Their full WAVs retained two seconds of
pre-roll, and both generated pages were acknowledged Delivered. The operator
confirmed faster response with the whole question intact, including a separate
half-second pause check.

46 automated tests passed. Actual Piper import, preview, selected-voice paging,
model removal, and speech recognition passed after the worker change. An explicit
worker-crash test confirmed restart and warm reuse. Firmware built/flashed with
verified hashes, and the on-device buffer/endpoint self-test passed.

Device idle measurement: about 51% combined CPU, mic 4% of one core, 6.25 MB free
PSRAM, 140 kB free internal RAM. Capture storage grew from 384,000 to 544,000 bytes
to permit longer questions. No input gaps or upload errors were observed in the
initial physical tests. Firmware image is 1,374,960 bytes, with 56% of its partition
free. The warm voice process consumes additional laptop RAM between requests.

Artifacts: `latency-before.json`, `latency-after-tts.json`,
`voice-worker-recovery.json`, `voice-runtime-check.json`, `silence-device-check.json`,
`build-silence-v13.log`, and `flash-silence-v13.log` under `artifacts/`.
The prior v12 image and key source files are in
`backups/before-silence-20261028-200130/`.
