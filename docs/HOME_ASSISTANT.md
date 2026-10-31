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
read-only. Thermostat values use HA's configured temperature unit. In heat/cool
mode use the low/high range rather than a single temperature.

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

Matching is local, with one explicit named target/action per command. Pronouns,
multiple actions, negated commands, arbitrary service names and general
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
