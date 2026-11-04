# Home Assistant device inventory

Open **Home Assistant** in Adam, or `/home-assistant`. This page manages both
imported HA entities and the names of Adam speaker/display units.

## Connection and imports

1. Save the HA server origin (for example `http://homeassistant.local:8123` or
   `http://<ha-ip>:8123`) and a long-lived access token from **Profile >
   Security**. Leave the token blank on later saves to preserve it. Changing
   servers requires a new token. Tokens remain on the head-end and are never
   sent to ESP32 units or returned by the settings API.
2. Enable Home Assistant control and refresh the inventory. Discovery only reads
   states; it does not operate devices.
3. Choose **Main controls** or **All entities & sensors**, then **Import into Adam**.
   An imported entity immediately recognizes its default HA friendly name.
4. Alternatively enable **Automatically import main controls**. Refresh imports
   existing supported main controls plus curated household sensors (`sensor`
   device classes `temperature`, `humidity`, `aqi`/`pm25`/`pm10`,
   `carbon_dioxide`, `carbon_monoxide`, `volatile_organic_compounds`, `power`,
   `energy`, and `binary_sensor` device classes `door`, `garage_door`, `window`,
   `opening`, `lock`, `moisture`, `problem`, `smoke`, `gas`, `motion`,
   `occupancy`), and discovers new ones every minute while enabled. Explicitly
   removed devices stay removed. Diagnostic, battery and configuration entities
   (such as Kasa LED, auto-update/auto-off, emergency-heat, one-touch-locking,
   signal-strength and battery entities) are excluded from automatic import and
   remain available for manual opt-in.
5. Use each card's controls or **Try a voice command**. Both send real actions.
   **Read status** only reads state. Replies can play on an online Adam speaker.

The existing single selected light is migrated with its saved spoken name.
Automatic import starts off, preserving the prior scope until selected.

## Names and identity

**Manage name & aliases** changes Adam's local display/spoken name without
renaming anything in HA. Mapping uses **server origin + entity ID**, never the
friendly name. Aliases, import decisions and local names persist in SQLite across
refreshes and restarts. A blank Adam name follows HA's friendly name. The optional
**Also recognize the Home Assistant name** checkbox keeps that name usable after
an Adam rename. Each device can have eight additional aliases.

Duplicate spoken names are marked on cards. A command matching multiple imported,
enabled devices does not act; give them distinct names or aliases. Missing
entities retain their mapping and are disabled for commands until rediscovered.
If an entity ID changes in HA, import its new ID and deliberately transfer the
local name; Adam never guesses a replacement from a matching friendly name.
Changing HA server origins separates their inventories.

Entries represent **entities**, not physical device-registry groups: one physical
device can have a main light plus separate LED/settings/sensor entries. Entity IDs
are visible on every card. Adam speaker renames use the paired unit ID and retain
pairing, paging targets and display settings. Screen names update through polling.

## Supported controls

Controls are built from HA's entity domain and declared attributes/features, and
are checked again against the live entity before sending an action. Dimming/color
capabilities remain visible while lights are off.

| Entity | Controls in Adam |
| --- | --- |
| Light | On/off, brightness, RGB color, color temperature when Kelvin bounds are supplied |
| Switch / input boolean | On/off |
| Fan | On/off and percentage speed when declared |
| Cover | Open, close, stop, position when declared |
| Lock | Lock/unlock for locks that do not require a PIN |
| Climate | Target temperature, low/high range, available HVAC modes |
| Media player | On/off, play/pause/stop, volume when declared |
| Scene / script | Activate/run |
| Button / input button | Press |
| Number / input number | Value within the reported limits |
| Select / input select | One of its reported options |
| Sensors and other entities | Read-only status |

This does not expose arbitrary HA services, camera streams, PIN entry, alarms,
or every custom integration action. Unsupported entries remain visible as
read-only. Thermostat values use HA's configured temperature unit.

### Independent thermostat targets

Say **"Set the heat to 65"** or **"Set the cooling to 71"** when exactly one
enabled imported thermostat matches. With multiple thermostats, name the device:
**"Set thermostat heating to 65 degrees"** or **"Set cooling of the upstairs
thermostat to 71 degrees"**. These commands change only the named setpoint and
never switch HVAC mode. Room temperature does not choose a target.

In automatic `heat_cool` mode, a generic **"Set the thermostat to 65 degrees"**
asks you to specify heating or cooling without changing anything. In heat-only
or cool-only mode, that generic command still sets the single temperature.
The device card offers separate **Heating target** and **Cooling target** controls;
the generic target input is hidden in automatic mode.

Automatic mode stays enabled and the opposite limit stays unchanged. ADAM has
no minimum gap by default, and does not infer one from the current range width.
Heating cannot exceed cooling; equal limits may be sent, but the thermostat or
Home Assistant integration may enforce its own minimum separation.

An optional extra gap is available under **Manage device → Minimum heating/cooling
gap**. Zero or blank disables ADAM's minimum. A saved nonzero preference persists
through refreshes and restarts. Requests violating it explain the allowed limit
without moving either setpoint. Explicit low/high range commands remain available.

For example, with heating at 70° and cooling at 76°,
"set the heat to 65" yields heating at 65° and cooling still at 76°. Then
"set the cooling to 71" yields 65° / 71°. These values do not create a six-degree
minimum for subsequent commands.
ADAM sends both limits in one service call, without changing HVAC mode, and
reports the resulting targets only after readback confirms them. Failed calls
are never retried. Missing range data or a target that cannot preserve the
gap within the reported device limits produces an explanation without a command.

The separate [Cameras page](CAMERAS.md) accepts RTSP connections directly; it
does not import HA camera streams. Gesture rules use this same bounded device
capability/action mapping and can save an optional triggering snapshot for debugging.

## Spoken examples

Replace each example name with the device's Adam name, HA name or alias.

- "Hey Adam, turn on Living Room."
- "Is Bedroom Light on?" / "What's the status of Garage Door?"
- "What's the temperature in the bedroom?" / "How warm is the nursery?"
- "What's the humidity in the nursery?" / "Is Back Patio Door open?" / "Is Sink Leak wet?"
- "Are any doors or windows open?" / "Are all doors locked?" / "Are any lights on?" / "Are there any water leaks?"
- "Set Bedroom Light to 40 percent."
- "Set color of Bedroom Light to blue."
- "Set color temperature of Bedroom Light to 3000 kelvin."
- "Set Office Fan to 50 percent."
- "Open Garage Door." / "Close Garage Door."
- "Lock Front Door." / "Unlock Front Door."
- "Set Thermostat to 70 degrees."
- "Set temperature range of Thermostat to 65 and 72 degrees."
- "Set mode of Thermostat to heat."
- "Set volume of Television to 25 percent."
- "Activate Evening Scene." / "Run Bedtime."
- "Set option of Light Preset to Brightness preset 1."
- "Turn off the living room light and lock the front door."
- "Turn on the bedroom and back porch lights." / "Turn off Bedroom, close Garage Door and lock Front Door."

Matching is local and exact-name based. One utterance may hold up to four
device actions or status checks, joined by "and", "then" or commas, or sharing
one verb ("turn on A and B lights"). Compound commands fail closed: if any part
names an unknown or ambiguous device, has an unsupported value, conflicts with
another part on the same device, or contains a negation, **no** command is sent
and the reply names the problem. Actions run in spoken order and the replies are
combined into one short sentence. Pronouns, arbitrary service names and general
conversation are not handled. HA receives only the bounded service call and
entity target, not the transcript or microphone audio.

## Delivery and command behavior

Only successful new wake transcriptions trigger automatic commands. Touch/remote
Listen, saved-clip retranscription, dashboard polling, cancelled jobs and failed
recognition do not operate devices. Weather routing keeps precedence.

Up to four commands may be outstanding. Automatic commands expire after 30 seconds
or when the originating unit is offline. Settings/name/import changes invalidate
pending actions. Durable session claims prevent repeated execution after retries
or restart. An action is never automatically retried. Simple on/off, cover and
lock actions use bounded state readback and distinguish confirmation from a sent
but unconfirmed action. Parameter changes report **Sent**, rather than claiming
a physical change was confirmed. Failure to synthesize/play a reply never repeats
the action. A full diagnostic clip archive does not block live transcription.

## API and validation

### Natural phrasing and model interpretation preview

Device requests accept polite prefixes such as `Can we`, `Could you please`,
and `Would we`. Percentage follow-ups such as `Can we set it to 100%?` retain
the previous device only within the existing conversation scope and expiry.
Negated requests, ambiguous devices, unsupported controls and out-of-range
values still cannot operate a device. `Backyard` and `back yard` have identical
name matching; two devices with those equivalent names remain ambiguous.

`Shut the garage door` is an action synonym for `Close the garage door`,
including polite and compound requests. Both resolve the same exact enabled
device and its supported `close` control. `Is the garage door shut?` is a
read-only status question. The alias does not turn closing into locking,
interpret `shut off`/`shut down` as closing, or bypass negation and capability
checks. Unmatched `shut` commands do not fall through to conversational chat.

Within a compound request, a singular reference can use a uniquely named
device from an earlier clause: `Turn on the living room light and set it to
30%`. If several different devices have already been mentioned, name the
target explicitly. These references never borrow context from an earlier
conversation. Adjacent `on` and positive brightness requests for one light
are combined into one `turn_on` call with `brightness_pct`, so there is no
intermediate on command. Both clauses still count toward the four-clause
limit. Unsupported dimming, out-of-range values, `on` plus zero brightness,
opposing on/off actions, and conflicting brightness values reject the entire
plan before execution. The combined result retains the device identity for
the next spoken follow-up.

`Now which lights are on?` and `Did I leave the lights on?` use live Home
Assistant status. `Yeah, check` or `Check again` rechecks the recent status
topic. Without recent context, ADAM asks what to check. This does not replay
an earlier control command or infer a historical light state.

After a successful light summary, `Can we turn those off?`, `Turn them off`,
and `Switch off those lights` target the exact lights reported **on** in that
immediately preceding summary, within the same speaker/browser context and
90-second expiry. The group never expands to lights switched on afterward.
Up to four lights can be controlled; larger groups, unavailable members,
changed settings or missing targets require a fresh question or explicit
names. Singular references such as `that one` still require one clear target.
Every target is validated before the first command, then the existing state
readback, settings checks and durable replay protection apply. As with other
multi-device commands, a network failure after an earlier action can leave a
partially completed group; commands are never automatically retried.

The **Model services → Preview home requests** panel evaluates model-assisted
interpretation separately from live voice/text dispatch. Enter a request and,
optionally, select the previous device to test `it`/`that` references. The
preview sends only enabled/present device identities, spoken names and bounded
control metadata to the configured GPU/Pi model. It uses Ollama structured
output and validates returned identities, controls, value bounds and plan
shape. The model receives neither HA credentials nor live device states.

The preview **never executes a command, reads live HA states, speaks a reply,
or changes live dialogue context**. There is no execute flag or approval
button. A capability-valid proposal can still misunderstand intent; review
the displayed device/action/value before considering future automation.
General conversation may be disabled while using this explicit preview.
Existing model concurrency limits and GPU/Pi failure cooldown apply. Explicit
previews allow 30 seconds per model (up to 60 seconds with Pi fallback);
normal conversation keeps its saved, shorter timeouts.
The initial preview supports at most 40 devices, a 16,000-character input
catalog, and four proposed steps; oversized catalogs are rejected intact.
Thermostat validation uses the cached inventory, not a new live read.

Live voice and text commands continue to use deterministic routing. Enabling
automatic model-assisted execution is a separate future change. See
[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
for the JSON-schema transport contract.

All routes require the Adam administrator session and existing request-origin checks.

| Route | Purpose |
| --- | --- |
| GET/PUT `/api/home-assistant/settings` | Connection, enabled state and automatic imports; token is never returned |
| POST `/api/home-assistant/check` | Read-only connection check |
| GET `/api/home-assistant/inventory` | Cached inventory, names, capabilities, controls and conflicts |
| POST `/api/home-assistant/inventory/refresh` | Refresh all HA entities and reconcile import policy |
| PATCH `/api/home-assistant/inventory/{entity_id}` | Save local name, aliases, default-name recognition, import/enabled flags |
| POST `/api/home-assistant/inventory/{entity_id}/actions` | Execute validated `{action, value?}` for that exact entity |
| POST `/api/home-assistant/commands` | Execute `{text, unit_id?}` through the spoken-command resolver |
| POST `/api/home-assistant/intent-preview` | Preview `{text, context_entity_id?}`; always returns `executed: false`, with no device effects |
| GET `/api/home-assistant/entities` | Legacy switch/light discovery endpoint |

Validation on October 30, 2026: 100 head-end tests passed, including inventory
migration/persistence, duplicate names, origin separation, removals, capability
changes, numeric bounds and settings-change cancellation. The isolated Chrome
workflow passed import, alias control, rename persistence and desktop/mobile
layouts. Mock HA actions do not operate household devices. Live discovery uses
only HA reads; counts are in `artifacts/inventory-live-check.json`.

Firmware v19 primes the codec/amplifier with 250 ms of silence after unmute and
before consuming the first speech sample. It retains the 15-second speech limit,
stop behavior and delivery acknowledgments. Three acoustic 1.6-second tone trials
captured 1.58-1.60 seconds, versus 1.52 seconds on v18 (20 ms measurement windows).
The actual listening-cue display test passed. Artifacts are under
`artifacts/audio-start`; run `py -3.12 tools/check_audio_start.py --label NAME`
with the device idle. The test plays brief tones and restores volume/mic gain.

Run `python -m unittest discover -s headend -t . -q` with the head-end environment,
and `py -3.12 tools/check_fleet_browser.py` for isolated browser validation.

Implementation references: [HA REST API](https://developers.home-assistant.io/docs/api/rest/),
[light actions](https://www.home-assistant.io/integrations/light/),
[climate actions](https://www.home-assistant.io/integrations/climate/),
[cover features](https://developers.home-assistant.io/docs/core/entity/cover/),
and [fan features](https://developers.home-assistant.io/docs/core/entity/fan/).
