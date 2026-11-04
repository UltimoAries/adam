# Status questions and follow-ups

Voice and Type to ADAM share read-only status handlers. Common home and model
status questions do not invoke the conversation LLM. Speech recognition and
spoken replies still use the configured speech services.

Try:

- “What lights are on right now?”
- “Did I leave the lights on?”
- “Could you tell me which lights are currently on?”
- “Which devices are unavailable?”
- “Which speakers are offline?”
- “Is Office ADAM online?”
- “Which brain are you using?”
- “What models are you serving?”
- “What models are loaded?”
- “Which recognition model are you using?”
- “Why are you using the Pi?”

Light, door/window, lock, and leak summaries retain their existing meaning.
Polite wrappers and current-time qualifiers such as “right now” no longer cause
otherwise supported summaries to fall through to the LLM. Controls still use
the existing bounded Home Assistant action handlers.
“Did I leave the lights on?” reports their current state, without inferring past
activity. Summaries containing exactly three names now list all three.

## Follow-ups

After a device or speaker status answer, try “Which one is unavailable?”, “Is it back online?”,
or “What about Bedroom?” (using an enabled device's name). ADAM keeps the
referenced entity IDs and states for 90 seconds, then refreshes live state for
the follow-up. It prefers previously unavailable devices when resolving an
unspecified “it”. If the reference is missing, expired, removed, or ambiguous,
ADAM asks for a name rather than inventing a device.
“Yeah, check” and “Check again” refresh the recent status topic. Without usable
context, ADAM asks what to check; these phrases do not repeat an earlier action.

Context is held in bounded memory, separately for each voice speaker or signed-in
browser tab and selected speaker. Unrelated commands clear that context. A
restart clears it. Browser requests may send a random 32-hex `context_id`; the
server scopes it to the authenticated session. Requests without it work but do
not establish browser follow-up context. Contextual controls are separately checked by `context_actions.py`; see
[Follow-up conversation](FOLLOWUP_CONVERSATION.md). Status lookups do not run
pronoun-based actions such as “turn them off”.
An explicit subsequent request to turn those lights off goes through the separate
control handler and can target up to four lights reported on in the immediately
preceding summary. It does not add lights switched on after that summary.

## Sources and limits

- Home status uses current Home Assistant states and ADAM's imported, enabled
  inventory. Missing or unknown states are reported explicitly; they are not
  interpreted as “off”. Unavailable names are included in summaries and details.
- Speaker availability comes from the existing 30-second heartbeat threshold.
  The web details include last-seen age. Offline does not establish whether a
  speaker lost power, Wi-Fi, or another connection.
- Model answers distinguish configured preference, last-used provider, and
  currently loaded conversation models on the preferred model host. Loaded-model
  checks only read Ollama metadata, use bounded timeouts, and cache results for
  ten seconds. They do not generate text or load/download weights. A failed
  probe is reported as unknown. `adam:latest` resolves to its matching model tag
  when the metadata gives one unambiguous alternative.
- The text box shows expandable details and the answer's check time; model
  metadata also reports its cache age. Spoken answers stay short.
- There is no general availability history yet. “Which device was offline?”
  can refer to recent status context, but ADAM cannot reconstruct earlier outages
  or infer their cause. Historical event tracking and LLM tool calling remain
  future work.

Implementation: `headend/status_queries.py`, the shared summary parser in
`ha_devices.py`, and authenticated tab context in `text_commands.py`.
Run `python -m unittest headend.test_status_queries -q` for targeted coverage.

Updated speakers can continue these questions during a short wake-free listening
window. Weather and general model topics have separate context handling; see
[Follow-up conversation](FOLLOWUP_CONVERSATION.md).

Outage explanations refresh the selected device and report known diagnostics.
They never infer a power or network cause from an unavailable state alone.
