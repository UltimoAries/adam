# Voice latency tuning on the Pi

Firmware v29 ends a wake capture after 750 ms of quiet, rounded up to a complete
20 ms wire packet. The previous setting was 1200 ms. The minimum live capture
is now 1000 ms instead of 2000 ms; speech must still be detected after the cue
exclusion window. No-activity and maximum-duration limits are unchanged.

`CONFIG_ADAM_ENDPOINT_SILENCE_MS` in ESP-IDF menuconfig (ADAM voice capture)
accepts 600–1500 ms. Set 1200 and rebuild to restore the old pause tolerance.
The shorter timeout can end a command if someone pauses for roughly three
quarters of a second. Test natural pauses and long device names before reducing
it further. `headend status` reports the configured timeout; `headend selftest`
checks pause preservation, endpoint alignment, maximum capture, and no activity.

The speaker opens the HTTP connection while the listening cue plays and no
longer adds the extra 120 ms delay. Its `X-Recording-Async: 1` header requests
immediate acceptance after the complete, validated recording enters the STT
queue. Recognition and action dispatch run independently of recording writes.
The original mono recording is still archived without STT gain or resampling.

One recording worker and eight reserved sessions bound memory and disk work.
If that queue is full, the session reports a recording warning and recognition
continues. Shutdown drains recognition and recording work. Original recordings
are retained on recognition-queue rejection when storage capacity permits.
Older diagnostic clients still receive recording IDs synchronously. Async
clients obtain eventual `clip_id`, `archive_id`, and `recording_status` through
`GET /api/fleet`.

Home Assistant uses a bounded persistent HTTP connection pool. Requests still
use current credentials, validated device capabilities, replay protection,
state readback, and no service-call POST retries. Only completed wake sessions
can execute commands; partial audio and diagnostic uploads cannot.

For a Pi-only comparison, select **Pi only** on `/models`. The single preference
covers recognition and conversation; Piper speaking voice always stays local.
**Prefer GPU, automatically fall back to Pi** uses both services on one GPU host.
A failure in either service puts both on the Pi for the shared retry period,
avoiding a second GPU timeout during the same interaction. The first failure
can still take the configured timeout (defaults: 3 seconds STT, 6 conversation).
Local mode skips remote model probes and remote generation attempts.

## Measurements

Live sessions expose `timings_ms` relative to the server receiving the complete
utterance: STT submission, finalized transcript, HA result or information answer,
reply queued, recording saved, playback accepted, and delivery complete.
`queue_ms` and `inference_ms` describe recognizer work separately.
`trigger_to_*` adds the device's captured duration to each server interval; it
is an estimate, excluding final network transit. It uses no cross-device clock
subtraction. These detailed timings are live-session diagnostics, not durable
metrics after a head-end restart.

The fleet page displays transcript, HA result, reply-ready, and playback-accepted
times after capture. HA result means readback/answer completion, not a measured
physical actuation time. Playback acceptance precedes the speaker's audio
priming delay; delivery completion includes the whole spoken response.

Compare repeated matching commands with the same local models, and separate
capture time (which includes the user's speaking time and silence timeout) from
processing time. Test short commands, a 500 ms pause, timers, general questions,
and a real light state change. Record median and slow-tail results across at
least 20–30 utterances before claiming an end-to-end performance improvement.
