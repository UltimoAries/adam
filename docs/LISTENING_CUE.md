# Listening cue and ear

Firmware v12 begins microphone capture at the accepted trigger, prepending up
to two seconds of rolling history. The acknowledgement plays while capture
continues. Playback finishes, then a 120 ms settling interval passes before
PCM is uploaded; this delay no longer drops speech. Wake inference is paused
during the cue and listening session. The original recording can include the
wake phrase and the brief chime: acoustic echo cancellation is not implemented.
The eye becomes an animated ear immediately during network listening or local diagnostic
recording. Its sound waves react to microphone level; tapping it stops capture.

The current embedded cue is an original **120 ms two-note chime**, at 880 Hz
then 1174.66 Hz, with smooth boundaries and a peak of 10,000. The initial
4,000-peak version was too quiet; the operator requested this 8 dB increase.
It plays locally from 16 kHz mono PCM16; no head-end synthesis
or intentional wait is needed before speaking. The wake phrase remains
**Hey Jarvis**. The operator confirmed pre-roll retains both Hey Jarvis and the
first command word in recording and transcript, then requested this chime in
place of the spoken Yes response. The previous Yes cue and its v12 firmware
are backed up under `backups/before-trigger-chime-*`; the older Windows Huh cue
is retained under `backups/before-yes-cue-*`.

Regenerate the chime with `python -m tools.make_trigger_chime`, then build/flash.
The corresponding preview is `artifacts/recordings/trigger-chime.wav`.

The cue's generation provenance, format, duration and PCM checksum are recorded in
`firmware/main/listen_cue.json`. Changing the default paging voice does not
automatically regenerate this embedded cue. To replace the chime with a local voice,
generate a short WAV with that profile, install it with the spoken text, then
build/flash. For example, after generating `yes.wav`:

```powershell
.\headend\.venv\Scripts\python.exe -m tools.install_listen_cue yes.wav --text "Yes?" --source "Local Piper: profile-id@version" --attribution "Model attribution and license reference"
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
```

## ElevenLabs candidate workflow

This optional asset-generation step is separate from the local voice system.
It sends only a sound-description prompt to ElevenLabs, once per explicit
Generate click. Runtime wake detection, transcription, paging and playback
continue to work locally after the audio file has been obtained.

Launch the local masked-key window from the project root:

```powershell
.\headend\.venv\Scripts\python.exe -m tools.elevenlabs_cue_studio
```

Enter the API key in that window, never in chat. It remains in process memory
and is not saved. Each click creates one one-second Sound Effects candidate
using the account's credits. There are no automatic retries. The default
prompt asks for one natural, playful, sleepy/exasperated character's “Huh?”
with no additional words, music, background sounds or robotic processing.

Generated candidates are saved under ignored `headend/data/cues/` and added
to the local Audio bench library for preview. **Keep this cue** records the
selected asset in `headend/data/cues/selection.json`; it does not flash firmware.
The implementation uses the [official Sound Effects endpoint](https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert).

Alternatively, select and download a clip from the
[ElevenLabs Huh collection](https://elevenlabs.io/sound-effects/huh).
That page states free-plan assets require ElevenLabs attribution. Keep the
source/attribution with the selected asset when importing it.

## Install a reviewed asset

```powershell
.\headend\.venv\Scripts\python.exe -m tools.install_listen_cue path\to\selected-huh.mp3 --text "Huh?" --source "ElevenLabs" --attribution "https://elevenlabs.io/sound-effects/huh"
.\tools\idf.ps1 build
.\tools\idf.ps1 -p COM6 flash
```

The importer accepts local audio, converts it to 16 kHz mono PCM, trims only
leading/trailing silence, normalizes peak level and fades the boundaries. It
does not stretch or pitch-shift ElevenLabs audio. Cues longer than two seconds
are rejected. The actual PCM bytes and provenance/hash metadata are stored in
`firmware/main/listen_cue.pcm` and `listen_cue.json`.

`tools/make_listen_cue.ps1` and `process_listen_cue.py` reproduce the Windows
placeholder. They are not used for the ElevenLabs import path.

Tests in `headend/test_cue.py` verify conversion, silent-input rejection,
one-request generation, and that credentials are omitted from stored metadata
and surfaced errors. They use a mocked provider response; an actual ElevenLabs
generation still needs an account/key or a selected downloaded file.
