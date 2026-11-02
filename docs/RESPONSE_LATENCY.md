# Silence-ended questions and faster replies

Current firmware (`v30-185` and `v30-146`) ends wake-triggered questions after
**750 ms of quiet**, rounded up to a complete 20 ms packet, with a **one-second**
minimum post-trigger capture. Firmware v29 introduced these defaults; earlier
silence-ended builds used 1.2 seconds of quiet and a two-second minimum. The
head-end keeps its default Piper TTS voice warm in a persistent worker process.
See [VOICE_LATENCY.md](VOICE_LATENCY.md) for configurable pause tolerance,
asynchronous recording writes, shared GPU fallback, and timing diagnostics.

*(Visual note: Firmware v13 showed an animated ear during capture; Firmware
v22 and later render translucent green listening mist over the live weather sky — see
[SPACE_PANEL.md](SPACE_PANEL.md).)*

## On-device silence endpoint (`preroll.c`)

The detector measures DC-corrected microphone energy in 10 ms blocks, tracks a
noise floor from the quietest fifth of recent frames, and watches for sustained
above-threshold speech energy. Because this is an energy-based adaptive silence
detector rather than a semantic endpoint model, loud nearby TV or conversation
can extend capture up to the post-trigger cap:

- Excludes the first **500 ms** after trigger from endpoint decisions so the
  120 ms listening chime is never mistaken for speech (those samples remain in
  the uploaded waveform).
- Resets the quiet timer whenever at least **30 ms** of above-threshold activity
  is observed.
- Ends capture cleanly on a 20 ms packet boundary after **750 ms** of quiet
  following speech, retaining both the two-second pre-roll and the quiet tail
  (short mid-sentence pauses of ~500–600 ms keep the utterance intact).
- Enforces a **1-second** minimum post-trigger capture, a **5-second** no-activity
  timeout when nobody speaks, and a **13-second** post-trigger maximum (15 seconds
  total including two seconds of pre-roll).
- Keeps **Settings → Voice → Talk 8S** and dashboard **Listen 8 sec** as fixed
  eight-second diagnostic captures.
- Tapping the center of the screen cancels capture immediately without
  transcribing a partial clip.

`python tools/control.py headend selftest` exercises fixed and adaptive capture,
mid-question pauses, quiet-tail retention, the 13-second cap, no-activity
timeouts, ring-buffer wraparound and gap invalidation on hardware.

## Variable-length upload (`silence-v1`)

Wake uploads send `X-Audio-Mode: silence-v1` over HTTP chunked transfer, making
`X-Audio-Frames` an upper bound (at most 750 packets). Sequential 644-byte data
records (4-byte sequence number + 640 bytes PCM16) conclude with a 644-byte end
record containing little-endian `0xffffffff`, the exact transmitted packet count,
a 1-byte endpoint reason (`1` = silence, `2` = max duration, `3` = no activity),
and 635 zero padding bytes, followed by the HTTP zero-length chunk terminator.

The server requires a valid end record, matching packet count, at least one
post-trigger second, zero trailing bytes, and a clean HTTP stream completion;
truncated or out-of-order streams are discarded. Legacy fixed-length uploads
remain supported.

## Persistent warm speech startup and fast reply pickup

`headend/voices.py` and `headend/voice_worker.py` (`PiperRuntime`) keep **one
active Piper ONNX model** warm inside a dedicated worker subprocess:

- Package hashes and text/audio bounds (200 characters, 15 seconds, 35-second
  timeout, 2 CPU threads, process memory cap) are still enforced on every call.
- Switching voices loads the newly requested profile; a crashed or timed-out
  worker is discarded and automatically respawned on the next request.
- At head-end startup, both the default Piper reply voice and the local
  speech-to-text model warm up using synthetic input without recording or playing
  audio.
- After uploading a wake question, the ESP32 temporarily accelerates head-end
  polling from **1.5 s** to **250 ms** for 30 seconds so synthesized replies play
  promptly without increasing idle LAN traffic.

## Historical latency measurements

These measurements describe the earlier silence-ended implementation and its
original test hardware. They are not current Pi or 1.46-board benchmarks.

- **Piper TTS synthesis:** Cold worker + model load measured ~3.5–3.8 s for a
  typical weather sentence, dropping to **~385–405 ms** once warm in
  `PiperRuntime`.
- **Capture window:** Typical short spoken questions finish **2.6–4.6 s** after
  the wake word instead of waiting a fixed 8.0 s.
- **ESP32 memory footprint:** Expanding the live PSRAM spool for 13-second
  post-trigger questions raised pre-roll + spool allocations from 384,000 to
  **544,000 bytes** in PSRAM while leaving >6.2 MiB of PSRAM and ~140 KiB of
  internal SRAM free.
