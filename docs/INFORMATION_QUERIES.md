# Information queries and weather

Weather answers were added in head-end v0.5; v0.6 with firmware v13 now ends wake
recordings on silence and keeps speech models warm. Say
**“Hey Jarvis, what's the weather in Shelton, Washington?”**
in one sentence. After the recording ends and local transcription finishes,
the head-end synthesizes an answer and sends it to the unit that asked.
See [RESPONSE_LATENCY.md](RESPONSE_LATENCY.md) for timing and recording behavior.

Open [Information & sources](http://127.0.0.1:8765/queries), or use the link on
the dashboard. Sign in with the existing administrator account.

## Configuration

- Weather starts enabled, in Fahrenheit, with Shelton, Washington as the
  default (47.21509, -123.10071, verified using Open-Meteo geocoding).
- Save up to 16 locations using city search. Add spoken aliases such as
  `home` or `the cabin`, then **Save settings**. Aliases cannot identify two places.
- A question without a location uses the default. An unsaved city can be
  looked up by name; ambiguous results ask for a clearer location.
- Choose Fahrenheit/Celsius and either follow the voice-library default or
  select a dedicated reply voice. A removed override voice produces a visible
  error; it does not silently switch characters.
- **Try a question** previews text without playing sound. Select an online
  unit to generate and send a spoken test. Delivery is confirmed on the dashboard.
- Settings persist in `query_settings` in `headend/data/fleet.sqlite3`.

Examples: “What's the weather?”, “What's the temperature at home?”, “What's
tomorrow's forecast in Shelton?”, “Will it rain today in Olympia Washington?”
Current answers include conditions, temperature and feels-like temperature;
today/tomorrow answers include conditions, high/low and precipitation chance.
This is a small rule-based handler, not general question answering. Other
utterances remain in transcripts without an automatic reply. Requests for
unsupported forecast periods receive an explanation rather than current data
presented as the requested forecast.

## Source and privacy

The first provider is [Open-Meteo](https://open-meteo.com/en/docs), with its
[geocoding API](https://open-meteo.com/en/docs/geocoding-api). The free endpoint
needs no key and is intended for noncommercial use; review
[provider pricing and limits](https://open-meteo.com/en/pricing) before commercial
distribution. Weather data is attributed to Open-Meteo under CC BY 4.0 in the
settings page, answer preview and session history.

Audio, transcription and Piper speech synthesis remain local. City searches
and coordinates are sent over HTTPS to the weather provider; the provider also
sees the laptop's public IP address. Internet connectivity is required for
uncached weather. This integration does not send recordings or full transcripts
to a remote speech or language service.

Forecasts are cached in memory for ten minutes (64 coordinate/unit combinations).
Each answer retains retrieval time and the source's weather time. The handler
rejects incomplete or old data and reports unavailable service rather than
inventing weather. It does not fall back to expired cached weather. Current
values are model-based conditions, not a claim of a sensor measurement at the box.

## Processing and extension points

`headend/queries.py` contains typed settings, the weather intent parser,
`OpenMeteo` provider, `QueryService`, and authenticated administrator routes:

| Route | Purpose |
| --- | --- |
| GET/PUT `/api/queries/settings` | Read/save validated configuration |
| GET `/api/queries/locations?name=…` | Find candidate locations |
| POST `/api/queries/test` | Preview `{text}` or send `{text, unit_id}` |

Only the completion callback of a **new, successful wake session** submits an
automatic query. It runs independently of dashboard polling. Remote Listen,
touch recordings, uploaded clips, and bench retranscriptions do not speak
automatic replies. Failed/cancelled/no-speech recognition never runs the handler.
The original saved recording and transcript remain unchanged, including pre-roll.

Automatic replies use a separate single-worker executor, with four slots shared
with administrator tests. Slow HTTP/TTS work does not occupy the recognition
worker or device poll event loop. HTTP uses fixed HTTPS hosts, no redirects,
five-second per-operation timeouts and a 256 kB response bound. Questions older
than 90 seconds when their reply worker starts are discarded. Recognized answers
and errors appear alongside their transcript; page IDs connect them to actual
playback confirmation. Replies use the existing 15-second PCM delivery contract
and five-minute page expiry. The real included voice produced 6.8-second current
weather and 8.6-second tomorrow replies during validation. A slower custom voice
can exceed 15 seconds; this produces an error rather than silently truncating it.

Add future providers behind explicit adapters with validated settings and their
own bounded HTTP requests. Add future information handlers alongside the weather
intent; keep intent matching, lookup, answer text and playback separate. Expose
only provider IDs the server implements. Arbitrary user-supplied URLs, executable
templates, LLM tool calls and home-control actions are not enabled in this version.
This keeps future Home Assistant authorization separate from read-only weather.

## Validation

The suite includes weather routing, units, aliases, time periods, cache expiry,
provider failure, stale/missing values, persistent settings, authentication,
voice override, bounded queues, and wake-to-targeted-page behavior without browser
polling. Existing streaming, pre-roll, paging and voice tests also pass.

```powershell
.\headend\.venv\Scripts\python.exe -m unittest headend.test_session headend.test_server headend.test_fleet headend.test_cue headend.test_voices headend.test_queries -q
py -3.12 tools/check_fleet_browser.py
```

Browser checks use isolated data/fake weather, and cover saving/reloading settings,
search/alias selection, preview/send, mobile layout and the existing paging/voice
library. Live weather and actual Piper output are recorded in
`artifacts/weather-runtime-check.json`; desktop/mobile captures are
`artifacts/queries-desktop.png` and `artifacts/queries-mobile.png`.

On October 28, 2026, the operator asked the real box for Shelton weather using
Hey Jarvis and confirmed a clear spoken answer. The live delivery check is in
`artifacts/weather-device-check.json`. This verifies the complete on-device
wake, Wi-Fi audio, local transcription, live lookup, local voice and playback path.
