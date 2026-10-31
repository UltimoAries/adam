# Information queries, time, calendar and weather

ADAM answers wake-triggered time, world-clock, calendar, countdown-timer and
weather questions locally using Piper speech synthesis and Open-Meteo forecasts.
With Firmware v28 (using silence-ended wake capture and warm speech workers
introduced in firmware v13 / head-end v0.6), say:

**“Hey Adam, what's the weather in Seattle, Washington?”**

in a single sentence. Once capture ends on silence and transcription completes,
the head-end fetches the forecast, synthesizes a spoken reply, and queues it for
the speaker unit that asked. See [RESPONSE_LATENCY.md](RESPONSE_LATENCY.md) for
endpoint timing and warm-worker behavior.

Open **Information & sources** at `/queries` (for example
`http://127.0.0.1:8765/queries` or `http://<headend-ip>:8765/queries`) while
signed in as an administrator.

## Configuration

- Configure **Home location** and temperature units (Fahrenheit or Celsius), for
  example **Seattle, Washington** (`47.6062, -122.3321`, `America/Los_Angeles`,
  verified via Open-Meteo geocoding).
- Save up to 16 locations using city search. Add spoken aliases such as
  `home` or `the cabin`, then click **Save settings**. Aliases must be unique.
- Per-device location overrides map individual speaker units to saved places,
  while `default_location` supplies **Home** for all other units and for the
  home-screen weather panel (see [SPACE_PANEL.md](SPACE_PANEL.md)).
- A question without an explicit place uses the asking unit's configured location
  (or **Home**). An unsaved city is looked up by name; ambiguous matches ask for
  a more specific location.
- Choose Fahrenheit/Celsius and either follow the voice-library default or
  select a dedicated reply voice. Removing an override voice surfaces a clear
  configuration error rather than silently switching voices.
- **Try a question** previews the text response without playing audio; selecting
  an online unit synthesizes and delivers a spoken test reply.
- Settings persist in `query_settings` inside `headend/data/fleet.sqlite3`.

Examples:
- **Weather:** “What's the weather?”, “What's the temperature at home?”, “What's
  tomorrow's forecast in Seattle?”, “Will it rain today in Portland, Oregon?”
- **Time, world clock & calendar:** “What time is it?”, “What time is it in
  Tokyo?”, “What's today's date?”, “What day is tomorrow?”, “How many days until
  Christmas?”, “What day of the week is July 4, 2027?”
- **Named countdown timers (`headend/timers.py`):** “Set a 10 minute timer for
  pasta”, “Set a 45 second tea timer”, “How much time is left on the pasta
  timer?”, “Add 2 minutes to the pasta timer”, “Cancel the pasta timer”, “Stop”
  (while ringing).

Local time, date and calendar math use each speaker unit's configured IANA
timezone and continue to work even when Open-Meteo weather queries are disabled.
World-clock questions resolve saved locations first and fall back to Open-Meteo
city geocoding to obtain the target city's IANA timezone. Indoor room temperature
questions (such as “What's the temperature in the bedroom?”) check imported Home
Assistant temperature sensors first before falling back to Open-Meteo outdoor
weather.

Current weather answers report conditions, temperature and apparent temperature;
today/tomorrow answers report conditions, high/low temperatures and precipitation
probability. Requests for unsupported forecast horizons receive a clear
explanation rather than current conditions presented as a future forecast.
Unmatched general questions pass through to Home Assistant or optional
[local conversation](CONVERSATION.md) when enabled.

## Source and privacy

Forecasts and city lookups use [Open-Meteo](https://open-meteo.com/en/docs) and
its [geocoding API](https://open-meteo.com/en/docs/geocoding-api). The free tier
requires no API key and is licensed for noncommercial use; review
[provider pricing and terms](https://open-meteo.com/en/pricing) before commercial
deployment. Weather data is attributed to Open-Meteo under CC BY 4.0 on the
settings page, answer preview and session history.

Microphone capture, wake detection, speech recognition and Piper speech synthesis
all run locally. Only city search queries and latitude/longitude coordinates are
sent over HTTPS to Open-Meteo, which sees the head-end host's public IP address.
No audio recordings or raw voice transcripts are sent to external services.

Forecasts are cached in memory for ten minutes (up to 64 coordinate/unit pairs).
Each answer records both retrieval time and observation time. Incomplete or expired
responses return an unavailable message rather than stale or fabricated values.
Reported conditions reflect Open-Meteo model data for the configured coordinates,
not an on-device environmental sensor.

## Processing and extension points

`headend/queries.py` implements typed settings, weather intent parsing, the
`OpenMeteo` client, `QueryService`, and administrator API routes:

| Route | Purpose |
| --- | --- |
| GET/PUT `/api/queries/settings` | Read/save validated configuration |
| GET `/api/queries/locations?name=…` | Find candidate locations |
| POST `/api/queries/test` | Preview `{text}` or send `{text, unit_id}` |

Only the completion callback of a **new, successful wake session** triggers an
automatic spoken query reply. Remote **Listen**, on-device diagnostic recordings,
uploaded `/bench` clips and archive retranscriptions never trigger automatic
spoken answers. Original recordings and transcripts remain unmodified.

Automatic replies run on a dedicated worker with bounded capacity (four slots
shared with administrator test queries) so external HTTPS calls and TTS synthesis
never block speech recognition or device polling. Outbound HTTPS calls use fixed
hosts, no redirects, five-second per-operation timeouts and a 256 kB response cap.
Requests older than 90 seconds when worker execution starts are dropped. Spoken
replies use the standard 15-second PCM page limit and five-minute expiry.

Additional information providers can be added behind explicit adapters with
validated configuration and bounded HTTP clients, keeping read-only information
queries separate from Home Assistant device authorization.

## Validation

Automated tests cover weather intent routing, unit conversion, location aliases,
forecast periods, cache expiry, provider errors, stale/incomplete payloads,
persistent settings, administrator authentication, voice overrides, bounded
queues and wake-to-page delivery.

```powershell
.\headend\.venv\Scripts\python.exe -m unittest headend.test_session headend.test_server headend.test_fleet headend.test_cue headend.test_voices headend.test_queries -q
py -3.12 tools/check_fleet_browser.py
```

Isolated browser tests verify settings persistence, geocoding search and alias
assignment, text preview, targeted unit playback and mobile layout without external
network dependencies. End-to-end hardware tests verified continuous spoken wake
queries through Wi-Fi upload, local STT, Open-Meteo lookup, local Piper synthesis
and confirmed speaker playback.
