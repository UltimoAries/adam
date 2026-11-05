# Type to ADAM

Sign in to ADAM from a phone or desktop browser. **Type to ADAM** is directly below
the resource readings on the home page. Enter a command and tap **Send command**.
The wake phrase is optional; no browser microphone permission is needed.

Examples:

- Turn on the kitchen lights.
- Set the heat to 65 degrees.
- Set the cooling to 71 degrees.
- What doors are open?
- Set a pasta timer for ten minutes.
- What time is it?
- Call Office ADAM.

Use the device names and aliases configured in Home Assistant. Typed commands
share the voice intent handlers and bounded device actions, including independent
heating and cooling targets. Conversation uses the configured GPU/Pi routing
when enabled; it does not gain arbitrary Home Assistant service access.

Replies appear on the page by default. Home Assistant controls can run with
**No speaker — reply here**, including when all speaker units are offline.
Choose **Speaker context** for timer commands, a unit's time/weather settings,
or an intercom caller. Timers still ring on that physical speaker, not on the
phone; an offline speaker cannot ring until connected. Calls connect the selected
speaker to the named destination and retain the intercom's permission and
availability checks. They do not use the phone microphone.

Enable **Also speak the reply** to play the answer on the selected online unit.
The same answer remains visible on the page. A playback failure does not retry
the underlying action. Speaker selection is remembered within the browser tab.
The last ten replies are shown until reload or sign-out.

## Request handling

`POST /api/commands/text` accepts `request_id` (32 lowercase hex characters),
`text` (1–500 characters), optional `unit_id`, and `speak_reply` (default false).
`GET /api/commands/text/{request_id}` reads its result. Both require the existing
administrator session and use the existing origin protection. Device bearer
credentials do not grant access to these browser routes.

The authenticated route creates an explicit `text` request and calls the shared
dispatcher. Audio uploads cannot choose that trigger. Touch/remote Listen,
bench recordings, and archive retranscriptions remain diagnostic-only.
Typed commands do not enter transcription, audio recording, or training capture.

Request IDs and payload hashes persist in SQLite to prevent repeat actions on
retries. Fetched completed results, including command/answer text, are also
stored there; the database remains private runtime data. The browser retains a
pending request in tab session storage across reloads. After a connection error,
**Check / retry safely** resubmits the same ID instead of creating a new action.
Reusing an ID with a different payload is rejected. If ADAM restarts before the
result is saved, it reports an uncertain result and never repeats that request;
check the device or timer state before issuing a new command.

The command box is separate from **Send a page**, which reads an announcement
aloud without interpreting it as a home-control instruction.

Status answers include expandable details and a check time. The browser sends
an optional random `context_id`, scoped by the server to the signed-in session,
to support 90-second status follow-ups. See [Status questions](STATUS_QUESTIONS.md)
for supported questions, freshness, and model-routing distinctions.

The same scoped context now supports weather day/city follow-ups and the last
three consecutive model exchanges. See [Follow-up conversation](FOLLOWUP_CONVERSATION.md).
Browser playback does not open the speaker microphone.
