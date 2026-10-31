# Listening cue

When the ESP32-S3 accepts a wake trigger (**Hey Adam**, `hey_adam-20261030-c60f0456`,
or stock `Hey Jarvis` when rolled back from `vendor/wake/`) or starts a network
**Listen** session, microphone capture begins immediately and prepends up to two
seconds of rolling pre-roll history. The acknowledgement cue plays locally while
capture continues, followed by a 120 ms settling interval before Wi-Fi upload
starts; speech spoken immediately after the wake phrase is retained without pause.
Wake inference pauses during the cue and active capture session.

On Firmware v22–v27, listening renders translucent **green ripple mist** over the
live [illustrated weather sky](SPACE_PANEL.md) (replacing the retired early
animated eye/ear), and tapping the center of the screen cancels capture. Because
acoustic echo cancellation is not implemented, the preserved original recording
can include the wake phrase and faint chime bleed in the pre-roll window.

## Current embedded chime (Firmware v23+)

The embedded cue (`firmware/main/listen_cue.pcm` and `firmware/main/listen_cue.json`)
is a synthesized **120 ms two-note ascending chime** (880 Hz followed by
1174.66 Hz) stored as 16 kHz mono signed 16-bit PCM with smooth fade envelopes and
**`peak: 1000`**.

Earlier builds (v12–v22) used `peak: 10000`, which clipped microphone pre-roll
samples when a question began immediately over the chime. Firmware v23 reduced the
chime amplitude by **20 dB** (`peak: 1000`), eliminating pre-roll clipping while
keeping a clear audible confirmation on the enclosure speaker and preserving
microphone gain and reply volume.

Regenerate the default chime from the repository root with:

```powershell
python -m tools.make_trigger_chime
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
```

`tools.make_trigger_chime` writes the updated `firmware/main/listen_cue.pcm`,
`firmware/main/listen_cue.json`, and a local preview WAV under
`artifacts/recordings/trigger-chime.wav`.

## Replacing the chime with a local voice clip

Changing the default Piper voice on `/voices` does not alter the embedded
firmware cue. To embed a short spoken acknowledgement generated with a local
Piper voice instead of the two-note chime, synthesize a short WAV (under two
seconds), install it with provenance metadata, and rebuild/flash:

```powershell
.\headend\.venv\Scripts\python.exe -m tools.install_listen_cue yes.wav --text "Yes?" --source "Local Piper: profile-id@version" --attribution "Model attribution and license reference"
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
```

## Optional ElevenLabs candidate workflow

An optional standalone helper can audition short sound-effect cues via the
ElevenLabs API. Runtime wake detection, transcription, Home Assistant control,
weather queries, conversation and TTS paging remain completely local.

Launch the local studio window from the repository root:

```powershell
.\headend\.venv\Scripts\python.exe -m tools.elevenlabs_cue_studio
```

Enter the API key in that window; it is never saved to disk and remains only in
process memory. Each explicit **Generate** click requests a single one-second
Sound Effects clip with no automatic retries, saving candidates under local
`headend/data/cues/` (gitignored) and registering them in the `/bench` clip
library for preview. **Keep this cue** records the chosen file in
`headend/data/cues/selection.json` without flashing firmware. See the
[ElevenLabs Sound Effects API](https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert)
and check plan attribution terms before distributing any generated or downloaded
sound effect.

## Installing a reviewed audio file

```powershell
.\headend\.venv\Scripts\python.exe -m tools.install_listen_cue path\to\selected-cue.mp3 --text "Huh?" --source "Custom source" --attribution "Source URL or license notice"
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
```

`tools.install_listen_cue` converts local audio to 16 kHz mono PCM16, trims only
leading and trailing silence, normalizes peak amplitude, applies short boundary
fades, rejects clips longer than two seconds, and records the PCM SHA-256 and
attribution in `firmware/main/listen_cue.json`.

Unit tests in `headend/test_cue.py` verify sample-rate conversion, silent-input
rejection, single-request behavior, and credential scrubbing using mocked responses.
