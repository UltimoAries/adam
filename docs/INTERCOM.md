# Live intercom

The v31 firmware and head-end add an experimental, full-duplex room-to-room
intercom. It uses the Pi as a live audio relay; it does not require the GPU or
a SIP server. Acoustic performance must be checked on each board in its room.

## Current pilot result

Recorded November 2, 2026, with `v31-185` and `v31-146` installed and the Pi
head-end relaying calls. Both units retain normal paging and assistant features.

The intended deployment uses 1.85C BOX V2 units. The mixed 1.85/1.46 pilot
demonstrated call setup, simultaneous audio transport, and hangup. After gain
adjustments, the user reported no rising feedback and usable audio from the
1.46 to the 1.85. Speech from the 1.85 to the 1.46 remained too quiet, even
after adding post-cancellation speech gain; ordinary spoken messages on the
1.46 were louder.

Treat that mixed pair as a practically one-way proof of concept, not a
validated two-way intercom. The transport still sends both directions; no
one-way mode or automatic direction switching has been introduced. The cause
of the remaining level difference is unresolved, so this is not evidence of
a permanent 1.46 hardware limit. Further 1.46 tuning is deferred.

A second 1.85 unit is needed to validate the intended pairing. Check voice
level in both directions, overlapping speech, feedback, sustained calls, and
hangup before treating that configuration as ready for regular use. Begin with
each direction separately, then overlapping speech, then a sustained call.
Keep the current peak limit and overload cutoff enabled during those tests.
Controlled diagnostic recordings were kept locally and are excluded from Git;
normal calls are not recorded.

## Using it

1. Install the matching `v31-185` or `v31-146` firmware on both speakers.
2. On the dashboard, open **Intercom → Allow incoming drop-in** and enable the
   destinations you want to accept automatic calls. This defaults to off.
3. Say **“Hey Adam, call Office ADAM”**, replacing the example with the exact,
   unique speaker name. **“Drop in on Office ADAM”** also works. Alternatively,
   choose **From**, **To**, and **Drop in** on the dashboard.
4. Each speaker plays a connection cue and displays its peer. Once connected,
   both people can speak at once; neither has to hold a button.
5. Say **“Hey Adam”** during the call to hang up, tap **End Call** on either
   speaker, or end it from the dashboard. During calls the wake word ends the
   call instead of submitting a normal assistant request.

Turning off a speaker's drop-in permission ends its current call. Offline,
busy, and older-firmware destinations cannot be called. Names must match one
speaker exactly; ambiguous names never start a call.

Calls stop on connection loss, after 10 minutes, or after 90 seconds without
appreciable voice energy. Timers continue counting locally, but ringing and
normal page playback are deferred while the intercom is active. Pages retain
their usual expiry; a timer whose ringing window expires during a call will
not ring afterward.

## Audio and privacy

- Only the two authenticated participants can open the call's media sockets.
  Device bearer credentials stay in headers; browser cookies cannot access
  media. The signed-in dashboard controls calls but does not receive audio.
- Microphone audio is sent only during the explicitly established call, and
  stops on hangup or a three-second connection watchdog. No automatic reconnect
  resumes a call. Call audio is not transcribed, saved, or sent to training,
  Home Assistant, a GPU model, or a cloud provider.
- Media uses the existing trusted LAN model (unencrypted local WebSocket).
  Keep this endpoint on the private network; do not expose it to the internet.
- Both speakers run ESP-SR full-duplex acoustic echo cancellation. The reference
  is the actual post-volume, post-board-gain playback PCM with allowance for the
  I2S DMA delay. The original board shares audio clocks; the 1.46 has separate
  capture/playback buses and requires extra drift and echo testing.
- Calls cap microphone gain at 12 dB and restore the previous gain afterward.
  The original board's 36 dB distant-wake setting produced excessive speaker
  echo in the initial pilot. Outgoing audio has a 12,000-sample peak ceiling
  with a gradual gain recovery. The full-size board restores 24 dB of speech
  gain after echo cancellation, beginning after 1.5 seconds of filter settling
  and increasing gradually; the 1.46 keeps unity speech gain. This is fixed,
  board-specific compensation, not automatic normalization of room noise.
  Sustained high microphone-return energy during playback ends the call with
  an audio-overload status. This protection does not replace acoustic testing.

## Implementation

`headend/intercom.py` owns permissions, exact-name voice routing, call lifecycle,
and the relay. Permissions and voice-request replay guards persist in SQLite.
Calls and a bounded 30-entry status history are memory-only; restart ends calls.
Four independent two-speaker calls are allowed, with one call per speaker.

`GET /api/units/{id}/poll?intercom=1` advertises support. A free participant gets
`{"type":"call","id":"<32-hex>","peer_name":"..."}`. After the local cue,
it connects to `/api/units/{id}/calls/{call_id}/stream` with its normal bearer
header. JSON status messages report `connecting`, `active`, or `ended`.
Binary messages contain exactly 640 bytes of mono signed little-endian 16-bit
PCM: 320 samples / 20 ms at 16 kHz. Each participant sends 50 frames/second.
Media is relayed only once both participants connect. Each direction is about
32 kB/s, excluding transport overhead.

The relay bounds queues to 200 ms, discards stale audio, validates message
size/rate and call membership, and closes stalled connections. The production
WebSocket transport limits messages to 1 KiB and four queued messages.

`firmware/main/intercom.c` owns the DSP/network task; `audio.c` remains the sole
I2S writer and supplies continuous playback with a small jitter buffer. The mic
task only queues samples. Capture overrun terminates the call rather than
continuing with an invalid echo reference. Echo-cleaned local audio feeds the
existing wake detector for hands-free hangup. `headend status` includes packet,
capture-drop, AEC processing time, input/reference/echo-cleaned RMS counters,
and transmitted RMS/peak after speech gain and limiting.
`audio status` includes playback starvation and queue-reset counters. Wi-Fi
power saving is disabled during calls and restored afterward. The DAC finishes
warming before the media connection opens, to preserve the first syllable.

Participants may send `{"type":"end"}`, optionally with
`"reason":"audio_overload"`; other text commands are rejected. The overload
guard requires eight consecutive AEC frames with outgoing RMS above
6,000 after speech gain/limiting and playback-reference RMS above 100. It is a
conservative stop condition, not a general-purpose feedback classifier.

Admin endpoints: `GET /api/intercom`, `PUT /api/intercom/units/{id}` with
`allow_drop_in`, `POST /api/intercom/calls` with `caller` and `callee`, and
`POST /api/intercom/calls/{id}/end`. Existing authentication and origin checks
protect these controls.

## Validation

Run `python -m unittest headend.test_intercom -q`, then the full head-end suite.
Build both firmware targets. Before treating the feature as stable, test:

- Both directions, overlapping speech, ordinary room noise, and volume changes.
- Audible connection cues and the correct peer shown on both displays.
- Voice/touch/dashboard hangup, Wi-Fi loss, and power loss.
- Normal wake requests, pages, and timers after the call ends.
- Sustained calls between two 1.85 units, checking for echo or capture overruns.
- If 1.46 development resumes, resolve the quiet receive direction and test
  sustained calls for drift from its separate capture/playback clocks.

SIP registration, external phone numbers, group calls, and browser microphone
calling are not included.
