# Household training archive

The training archive provides a local head-end collection and human-review
workflow for wake-word and speech recordings. Open **Training archive** on the
dashboard, or `/training` (for example <http://127.0.0.1:8765/training>), while
signed in as an administrator.

## What is collected

Every valid, completed Wi-Fi voice upload is archived before transcription:
automatic wake sessions and existing touch/remote **Listen** sessions. The WAV
contains the exact uploaded 16 kHz mono, 16-bit PCM, including the reported
pre-trigger audio and the complete command. It is saved before STT resampling,
normalization or volume boosting. Preview gain on the review page creates an
in-memory playback copy; it never changes the original.

The device still detects the wake phrase, buffers audio in RAM and uploads
interactions using the existing protocol. An attempt that never triggers does
not upload anything. Malformed, incomplete or disconnected streams do not enter
the completed-interaction archive. USB bench recordings and uploaded diagnostic
files are separate. Existing diagnostic clips are not retroactively imported.

Each interaction retains:

- Original WAV and SHA-256 of the complete WAV file; download and approval check
  the hash. Distinct interactions are retained even when their audio is identical.
- UTC timestamp of **head-end upload start**, device ID, device-name snapshot,
  room, session ID, firmware and wake model. The protocol does not supply an
  absolute device capture timestamp. Manual sessions use the last reported
  installed model when no trigger model is supplied.
- Actual total duration, reported pre-trigger duration, remaining capture
  duration, trigger (`wake` or `touch`, displayed as Automatic wake or Manual
  recording), endpoint mode and reason.
- Persistent transcript, transcription status/error, job ID and recognition
  details, including any gain used by STT. No-speech and cancellation are retained.
- Human review label, phrase boundaries in milliseconds from the original WAV
  start, chime check, notes, review time and revision.
- Optional speaker, distance and collection-session tags. Collection settings
  apply to future interactions; the review form edits an individual recording.

Nothing is automatically approved from a trigger or transcript. All new
interactions are **Unreviewed**. Recent transcripts also reappear in dashboard
history after a restart; the archive retains older interactions beyond that
dashboard's 100-session display limit.

## Review and difficult attempts

1. Choose a recording. Play the full original; optional +12/+24 dB preview gain
   helps with quiet microphone audio without changing the stored data.
2. For a positive, mark the complete spoken **“Hey Adam”** using the waveform,
   numeric start/end fields, or playhead buttons. Use **Play selection** to hear
   precisely that interval. Keep the command afterward in the original.
3. Check the chime. The gold waveform marker shows the pre-trigger boundary;
   it is **not** an exact chime annotation. Exclude the acknowledgement sound
   where possible. If it overlaps the phrase, record that explicitly so future
   export/evaluation can treat the example carefully.
4. Save one of the four labels:

| Label | Meaning |
| --- | --- |
| Positive | A person clearly said “Hey Adam”; requires phrase boundaries and a chime check. |
| Negative | No “Hey Adam”, including accidental triggers. |
| Discard | Incomplete, ambiguous or unsuitable audio. Retained, not deleted. |
| Unreviewed | Retained for later inspection. |

To collect difficult examples, expand **Collection settings & difficult
attempts**, choose a device, enter tags and press **Save tags & Listen 8 sec**
**before speaking**. Wait for the green listening display, then speak the wake
phrase and command. Refresh the list afterward. This uses the existing remote
Listen function; it pauses wake detection and does not execute the command.
It provides training material, not a measurement of automatic missed triggers.

Give a collection session a distinct tag, such as `family-evening-01`, and use
the same tag across devices recording that session. Change or clear it when
the session ends. Future dataset splitting must keep a collection session
together. Optional tags are not inferred as reliable speaker/session identity.

Reviews detect stale revisions and reject overwriting a review changed in
another tab. **Transcribe again** retries local STT without changing review
labels, dispatching Home Assistant commands or speaking a response. Unfinished
transcriptions after a restart are marked **interrupted** for this explicit
retry; they are not automatically replayed.

## Storage and limits

Default path: `headend/data/training_archive/` (under the configured head-end
state directory). It contains `archive.sqlite3` and `audio/<prefix>/<id>.wav`.
Settings and collection profiles are in this separate SQLite database.

Defaults, configurable on the review page:

| Setting | Default |
| --- | --- |
| Collection | Enabled |
| Archive budget | 10 GiB |
| Minimum free disk space | 1 GiB |
| Maximum interaction records | 100,000 |

These limits are independent of the diagnostic **48-clip / 64 MiB** library.
The archive never evicts originals. Accounting includes WAVs, database files,
and recovered/untracked partial files, with 64 KiB of metadata headroom per
new interaction. It is an application budget, not a filesystem quota; SQLite
updates, other applications and backups can also consume disk space.

When a whole WAV will not fit but metadata headroom remains, the archive retains
metadata and transcription with audio marked `not_saved`. If the record limit,
metadata allowance or free-space reserve is exhausted, no new archive row is
created. In either case normal voice processing continues from the uploaded
audio in memory. Warnings appear in the dashboard/review page and server log.
Pause collection, increase the limit, or make disk space available to resume.
No automatic deletion or destructive cleanup is provided.

Writes commit a pending metadata row, write and fsync a temporary WAV, then
atomically rename it before marking it ready. Startup reconciles interrupted
writes, including complete hash-matching temporary files. Partial/unusable
files are kept for investigation and counted against storage; missing audio
cannot be approved as positive or negative. A storage failure does not prevent
transcription or Home Assistant dispatch. These protections do not replace
backups or recover audio that was never saved.

For a consistent backup, stop the head-end when idle and copy the **entire**
`training_archive` directory, including the database and audio subdirectories.
Restore them together. Recordings and their database are local runtime data,
excluded from Git. Run one head-end process against a given state directory.

10,000 eight-second originals contain about 2.56 GB of PCM, or 2.38 GiB, plus
WAV headers, metadata and backups. Actual sessions may include additional
pre-trigger audio, so capacity depends on the measured total durations.

## Next deliverables: export, comparison training and promotion

This release collects and reviews recordings; it does **not** yet implement
dataset export or change pilot preprocessing. Do not feed the archive directory
directly to existing filename-based pilot preprocessing and assume labels or
splits will be respected.

The next export workflow should crop approved positives around their reviewed
phrase boundaries and retain approved negatives, with a manifest linking each
derived file to the original hash, interaction, review, tags and crop offsets.
Unreviewed/discarded examples must be excluded. Chime-overlap positives need
explicit handling. Missing collection-session tags need review before splitting.

Assign training/validation/test splits by collection session **before any
augmentation**. Preserve those assignments in the export and pilot
preprocessing path. Reserve fresh household sessions for evaluation. Combine
reviewed household training data with the larger synthetic dataset for local
comparison models; measure detection rate and accidental triggers. Each
candidate needs evaluation and a real device check before deliberate promotion.
Collection never retrains or replaces the installed model.

A later “save the missed attempt” feature would require firmware support for
a longer rolling RAM buffer and a requested upload; it is outside this release.

## Validation

```powershell
headend/.venv/Scripts/python.exe -m unittest discover -s headend -t . -q
py -3.12 tools/check_fleet_browser.py
```

Tests cover exact original samples versus boosted STT input, pre-trigger and
actual durations, restart persistence/recovery, full/unavailable storage,
continued command dispatch, review constraints, hash corruption, transcription
error/no-speech/cancellation/retry, authentication and manual-trigger semantics.
The isolated browser check exercises playback, phrase boundaries, explicit
approval, preview gain, saved tags/settings, original hashes and mobile layout.
Its synthetic fixture recordings are not put in the real archive.
