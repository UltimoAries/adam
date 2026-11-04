# Natural follow-up conversation

Firmware `v33-185` and `v33-146` support short listening windows and experimental
wake-word interruption of ADAM replies. Office is the live test target; Micro
remains on v31 until separately flashed. v32 speakers keep their eight-second
window and cannot interrupt replies.

Start with **Hey Adam**, then continue when the listening indicator appears.
New speakers wait eight seconds for a normal follow-up, twelve seconds when ADAM
asks for clarification, or five seconds after a device action. Silence closes the
window. Say **thanks**, **okay thanks**, **no, that's it**, **I'm good**, **that's
all**, **never mind**, or **stop listening** to end the exchange. Tap the center
to stop listening or playback. Chains are limited to six follow-ups and two minutes.

In **Model services**, toggle **Listen for a follow-up after spoken answers**.
**Allow Hey Adam to interrupt spoken replies (experimental)** controls the
additional interruption behavior on v33 speakers. Both default on for capable
firmware. The interruption option currently applies to replies that qualify for
follow-up listening; announcements and browser-requested speech remain excluded.

## Topic memory and actions

Memory lasts 90 seconds and stays separate per speaker or authenticated browser
session, tab, and selected speaker. It is held in memory and cleared by a restart
or an end phrase. An unrelated question replaces the prior actionable context.

- Weather: ask tomorrow's weather, then "What about Seattle?" or "What about
  today?". Day and city are carried forward. If the city is ambiguous, answer
  with an offered region, such as "Washington". A uniquely recognized region
  can resolve a slightly mistranscribed city name.
- Status: ask which lights are on, which one is unavailable, then why. Status
  is read again. ADAM reports evidence from Home Assistant or speaker check-ins,
  and does not invent a power, Wi-Fi, or battery failure. Expand Status details
  in the browser for timestamps and any selected numeric diagnostics.
- Controls: "Turn that one off" uses one clear, recently discussed device.
  Multiple candidates produce a question; answer with a device name. The
  resolved identity, current inventory permissions, and capabilities are checked
  again before the existing replay-protected action is sent.
  After a successful light summary, "Can we turn those off?" targets only the
  exact lights reported on, with a maximum of four. Missing/unavailable targets,
  changed settings, expired context, or larger groups require clarification.
  "Can we set that to 30%?" preserves a single recent device reference. See
  [Home Assistant](HOME_ASSISTANT.md) for compound commands and group limits.
- Corrections: "Actually, the bedroom" repeats the prior supported on/off or
  numeric action on that named device. It does not undo an already executed
  command; the reply says so. "Make that 68" changes the previous numeric setting
  and preserves heating versus cooling. Existing thermostat bounds still apply.
- General conversation: the last three consecutive successful model exchanges
  accompany the next model request. Replies are short and avoid repeated offers
  of further help. The model has no device-action authority.

Context authorizes plural controls only for the bounded light-summary case above.
It never authorizes arbitrary HA service calls or actions on removed/disabled
devices. An unconfirmed action does not seed
numeric corrections. Changing HA origin invalidates prior identities. Pending
selections expire with the conversation and do not survive settings changes.

## TV/noise handling and interruption

Room audio is not continuously streamed. The speaker uses ESP-SR speech detection
alongside its energy threshold before starting a follow-up, rather than treating
all loud audio as speech. Non-speech raises the estimated noise floor. A capture
ends after one second without qualifying speech, or at a hard eight-second
follow-up limit. A completed limit-ended follow-up is not dispatched: ADAM asks
for a fresh wake request instead of acting on a potentially truncated command.
TV dialogue and music vocals can still resemble human speech; this is not speaker
identification. The live TV/music case needs acoustic testing in the actual room.

Experimental interruption is intended to recognize **Hey Adam** during a reply
and start a new request after stopping playback. It is not reliable in the
current Office pilot; see the paused investigation below. A local AEC worker receives
the post-volume playback reference, caps the 1.85C microphone gain at 6 dB during
playback, and feeds cleaned audio to wake detection with compensating digital
gain. It restores normal microphone gain afterward. Reply detection uses ESP-SR
speech-recognition linear AEC, without the intercom filter's nonlinear
suppression. No interruption audio is sent until a wake actually starts a normal capture. Touch
cancellation, input gaps, and worker queue overflow disable the interrupt path.
Interruption stops speech, not a home command that already executed.

## Protocol and validation

v33 polls with `followup=1&followup_version=2`. Eligible offers contain a bounded
`followup_seconds` value (5, 8, or 12) and `interruptible`. v32 offers remain eight
seconds. A delivered page activates a same-speaker, single-use grant for the next
`X-Trigger: followup` upload with `X-Followup-Page`. Its server expiry is the
window plus four seconds for connection setup, bounded by the chain deadline.
An `interrupted` page acknowledgement is terminal, releases audio, and revokes its
follow-up grant; the interruption starts a fresh normal wake request.

Follow-up uploads preserve at most 300 ms of pre-roll and use the existing bounded
recording/archive policy, labeled separately from wake captures. Announcements,
timer ringing, diagnostic Listen, browser playback, and intercom audio do not
open follow-up windows. No-speech, failed, and limit-ended follow-up captures do
not dispatch actions.

Tests cover scope, ambiguity, stable identities, thermostat corrections,
diagnostics redaction, expiry, single-use grants, interrupted-page cleanup, and
partial-command rejection. `headend selftest` covers VAD gating, transient/noise
rejection, capture limits, and pre-roll on the actual device.

The earlier v32 Office pilot confirmed wake-free weather follow-ups. Firmware
v33 adds speech gating and experimental interruption;
Micro's intercom audio limitations remain documented separately in INTERCOM.md.

Rollout on 2026-11-02: Office is flashed to v33-185; both targets compile. The
240-test head-end suite passed, followed by 17 targeted status/control tests
covering the final mixed-summary ambiguity guard. Browser settings, on-device
self-tests, live read-only status explanations, and natural endings passed.
The user confirmed v33 follow-ups worked, but interruption missed. An explicitly
armed reply diagnostic confirmed that 678 cleaned blocks reached wake detection
without queue overflow, but neither attempted wake word triggered. The initial
FD filter strongly suppressed the recording; speech transcription recovered
the forecast from the microphone and no clear wake phrase from cleaned audio.
This does not establish whether suppression alone caused the miss. The revised
linear SR filter and lower playback microphone gain were then tested live.
Both revised targets compile, Office is flashed, and its capture/follow-up
self-test passes. The Pi deployment is healthy. Interruption is not yet
acoustically validated.

The subsequent SR-filter live test also missed interruption: 672 cleaned blocks
reached wake detection with no queue overflow, no AEC input drops, and a maximum
AEC processing time of 6.6 ms. The recorded microphone peaked below clipping;
speech transcription remained dominated by the reply rather than a clear user
wake phrase. Office's playback volume was 100%. A temporary 50% playback test
also failed from the user's perspective. Its watchdog restored 100% after the
monitored reply, and USB status independently confirmed the restoration.

## Investigation paused at user request — 2026-11-02

**Working:** normal wake requests and user-confirmed wake-free follow-ups.
**Unresolved:** reliably interrupting a spoken reply from normal speaking distance.
Do not describe interruption as working or ask for more tests until the user
resumes this investigation. No conclusion about a hardware limitation has been
established.

State at pause:

- Office has the revised v33-185 SR-linear AEC firmware. The 1.46 target builds,
  but Micro has not been flashed with these conversation changes.
- Office volume is restored to 100%. No call, reply AEC worker, or diagnostic
  recording is active; the one-reply diagnostic arm is consumed.
- The experimental interruption setting and working follow-up settings remain
  unchanged. No additional firmware or head-end deployment was performed during
  the pause handoff. These notes describe the state at the pause; subsequent
  head-end command improvements are recorded in [development history](DEVELOPMENT_HISTORY.md).
- Final USB status showed one cumulative interruption detection, despite the
  user's report that the test missed. Subsequent replies had already updated
  the live RMS counters. This counter alone does not identify which utterance
  triggered or prove a successful stop-and-answer sequence; preserve the
  discrepancy for correlation with the session/page log.
- The 50% diagnostic contains 86,016 frames (5.376 seconds). It was downloaded
  without further recording or transcription. Its acoustic content has not yet
  been analyzed. Do not associate the final live RMS counters with that file.

Local, git-ignored evidence (never commit household recordings or transcripts):

| Artifact directory | Contents |
| --- | --- |
| `artifacts/reply-interrupt/` | Initial FD filter capture: microphone, playback reference, cleaned mono WAVs |
| `artifacts/reply-interrupt-sr/` | SR-linear filter at 100% playback: same three channels |
| `artifacts/reply-interrupt-sr-volume50/` | Lower-volume capture plus `fleet-at-pause.json` with recent session/page metadata |
| `artifacts/reply-volume-trial.json` | Watchdog result: original volume 100 restored after monitored reply |

Temporary transcription scripts containing diagnostic audio were removed from
the Pi. The existing bench clip store was full, so no existing clips were
deleted; comparisons used direct diagnostic transcription without dispatching
home commands.
The initial FD diagnostic copied its input channels after the mutable DSP call;
the later SR diagnostics copy them before processing. Treat apparent clipping
in the first capture cautiously rather than assuming it proves ADC saturation.

### Next steps when resumed

1. **Correlate the existing evidence first.** Match the lower-volume recording,
   session timestamps, page `interrupted`/`delivered` acknowledgements, and the
   single cumulative detection. Determine whether it was late, false, or a real
   interruption whose subsequent request failed. Do not repeat the same test
   before reviewing the saved data.
2. **Separate acoustic pickup from wake recognition.** Analyze the 50% recording
   alongside the two earlier captures. Compare user speech before/after AEC,
   residual playback, clipping, and reference alignment. Earlier SR reference
   correlation showed roughly 419–422 samples of delay; that is a measurement,
   not proof of correct alignment across all playback transitions. If necessary,
   request one controlled recording with the same phrase during playback and
   during silence, capturing both microphone channels to compare pickup.
3. **Measure the detector rather than guess at its threshold.** Add diagnostic
   timestamps and wake probability/window traces, or replay saved PCM through
   an isolated detector with action dispatch disabled. Account for the 800 ms
   AEC settling gate and the model's startup feature windows. Keep recorded
   playback from executing HA actions. Do not lower the production wake cutoff
   without checking false triggers from the reply and TV audio.
4. **Change one proven bottleneck at a time.** Depending on those results, examine
   reference timing, gain/dynamic range, microphone selection, or playback
   ducking. Avoid repeatedly swapping AEC modes without a comparable recording.
   Do not change the intercom implementation as a side effect.
5. **Validate the complete interaction.** Success means stopping mid-reply,
   capturing the new request from its beginning, answering it once, and returning
   to normal follow-up/idle behavior. Check quiet and normal-TV conditions,
   multiple speaking distances/volumes, and false interruptions when nobody
   speaks. If reliable acoustic interruption remains impractical, discuss a
   touch-to-stop fallback while retaining the already working follow-ups.

### Explicit USB reply diagnostics

Run `tools/control.py --port COM6 reply record` while the speaker is idle, then
make one ordinary voice request and attempt interruption during its reply.
Only the next qualifying interruptible reply records; announcements do not
consume the arm. The capture is bounded to 15 seconds in RAM, with microphone,
post-volume playback reference, and unamplified AEC output channels. The revised
diagnostic copies inputs before the DSP call and publishes completed frames
under the worker lock. Normal operation does not record these channels.

Download with `tools/reply_diagnostic.py --port COM6 artifacts/reply-diagnostic`.
It writes three local mono WAVs without uploading them or dispatching commands.
Keep recordings in ignored artifacts; never commit household audio. Re-arming
replaces the capture and rebooting clears it. A downloaded capture is independent
of later live level counters, which reset on every monitored reply.
