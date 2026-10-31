# Sky home screen

Firmware v27 renders an illustrated local-time sky and weather home screen in six
centered rows: **device name, weekday, month/date, time, weather condition, and
temperature**. Standard labels use a 21 px glyph height, time uses 35 px and
temperature uses 28 px, with the AM/PM marker and degree unit aligned to their
row baselines. Long device names wrap cleanly across up to two 14 px lines
(supporting names up to 24 ASCII characters).

Every text row uses solid white lettering framed by a complete two-pixel black
outline (including diagonals) for high contrast across daylight, night and
activity overlays. Glyphs are drawn after background weather particles and edge
glows so rain, snow and mist never obscure the text, accompanied by an outlined
weather icon beside the condition label.

## Time-of-day sky, weather particles and activity mist

The background follows the unit's configured local time continuously:

- **Daily cycle:** Deep navy night sky with twinkling stars and a crescent moon on
  clear nights, warming through dawn (~06:00) into a blue daytime sky with a
  smiling golden sun and drifting puffy clouds, followed by an amber sunset
  (~19:00) and dusk transition back to night. Sky gradients interpolate smoothly
  each second using ordered RGB565 dithering and precomputed cloud outlines in
  PSRAM, requiring no external bitmap files or extra framebuffers.
- **Live weather scenes:** Open-Meteo WMO condition codes adjust sky tint, cloud
  cover and precipitation: overcast and fog mute sky saturation and hide the
  sun/stars; drizzle adds sparse slow drops; rain and showers add faster angled
  streaks; heavy rain and storms increase streak density (without strobe
  flashes); snow drifts as white flakes. Saturated blue rain/drizzle drops with
  bright tips stand out clearly against gray overcast skies (v26+). When weather
  is disabled, unavailable or stale, the panel falls back cleanly to the
  time-of-day sky without precipitation.
- **Listening and playback mist:** Active microphone capture layers translucent
  **green ripple mist** over the live weather sky; speaker replies and pages
  layer **blue ripple mist** (v22+). At least 40% of the underlying sky, clouds
  and precipitation remains visible through peak mist intensity, and capture
  takes visual precedence over playback so the wake chime stays green throughout
  listening.

## Persistent LLM availability ring

Firmware v24–v27 draws a steady perimeter **LLM edge ring** around the round
360×360 display, including while idle:

- **Green:** The preferred LAN GPU conversation model is loaded and eligible to
  serve requests.
- **Soft yellow:** The GPU route is stopped, unreachable or in its 30-second
  failure cooldown, and the local Raspberry Pi fallback model is ready instead.
- **Hidden:** Conversation is disabled, neither model is ready, status is unknown,
  or the unit has been disconnected from the head-end for more than 20 seconds.

The ring tracks conversational LLM routing (`headend/conversation.py`) independently of
STT, Piper TTS and the green/blue activity mist. Firmware v25 established an
eight-pixel bright perimeter core with a fourteen-pixel inward glow using cached
perimeter spans (no extra framebuffer); Firmware v27 softened the inner and outer
halo opacity by 25% while keeping the eight-pixel core brightness and colors.

On the head-end, a background worker inspects Ollama model metadata every five
seconds without generating prompts or forcing model loads, matching loaded source
models and the `adam:latest` alias by digest so device polls read cached state
instantly.

## Touch behavior, location and freshness rules

- **Hidden-by-default controls:** Volume, brightness and navigation arrows start
  hidden. Tap anywhere once to reveal them for eight seconds without changing a
  level. Tapping the center during active capture or playback cancels it
  immediately even on the first touch; tapping the center while idle hides
  revealed controls immediately. See [TOUCH_UI.md](TOUCH_UI.md).
- **Device name & location:** Toggle **Show device name on the panel** and set
  **Home location** (for example **Seattle, Washington**, `47.6062, -122.3321`,
  `America/Los_Angeles`) plus Fahrenheit/Celsius on `/queries`. City search
  populates coordinates and IANA time zones automatically, with optional
  per-device location overrides. Weather attribution remains on the head-end UI
  rather than cluttering the watch-sized LCD.
- **Timekeeping & staleness:** Time-zone offsets (including daylight saving
  transitions) are computed per poll even if weather is disabled. Between polls,
  the ESP32 advances time via its monotonic clock and flags head-end connection
  loss in the footer after 20 seconds. Weather refreshes every ten minutes in the
  background (retrying after one minute on error); readings older than two hours
  or disconnected for 30 minutes are hidden rather than shown as current.

## USB preview and validation tools

`firmware/main/space.c` renders the complete panel in RGB565, paired with
`headend/panel.py` on the server. Preview any time of day or WMO weather code
over USB for 60 seconds without affecting the live clock or temperature text:

```powershell
python tools/control.py menu home
python tools/control.py sky 06 15
python tools/control.py sky weather 63
python tools/control.py sky live
python tools/control.py sky weather live
python tools/snapshot.py artifacts/space-idle.png
```

Automated panel and head-end checks (requires `Pillow` for `snapshot.py`-based
framebuffer assertions):

```powershell
python tools/ui_check.py
python tools/check_space_panel.py
python tools/check_wake_display.py
python tools/check_daylight_panel.py
python tools/check_weather_panel.py
.\headend\.venv\Scripts\python.exe -m unittest headend.test_panel -v
```

## Firmware evolution summary (v14–v27)

| Firmware | Milestone summary |
| --- | --- |
| **v14–v15** | Replaced the early bring-up animated eye/ear with the initial space/clock/weather home screen, hidden-until-touched controls, per-device names/time zones on `/queries`, and green capture precedence during the trigger chime. |
| **v16–v17** | Added continuous local-time day/dawn/dusk/night sky palettes, `sky HH MM` USB previews, and enlarged high-legibility date/weekday typography (~14.2–14.5 fps idle, 6.27 MiB free PSRAM). |
| **v18–v19** | Embedded the `hey_adam-20261030-c60f0456` wake model (~1.39 MiB app binary, 56% of the 3 MiB factory partition free, ~16 fps idle) and added 250 ms post-unmute amplifier silence priming so spoken replies never clip their opening syllable. |
| **v20–v21** | Introduced the six-row centered weather layout, WMO precipitation/cloud animations (`sky weather CODE`), illustrated sun/crescent moon/puffy clouds, and full two-pixel black outlines around white text. |
| **v22–v23** | Layered translucent green/blue ripple mist over the live weather sky (~10–12 fps during active mist blending) and lowered the 120 ms listening chime by 20 dB (`peak: 1000`) to eliminate microphone pre-roll clipping. |
| **v24–v27** | Added head-end `llm_provider` polling metadata and the persistent green/yellow LLM edge ring (v24), widened the core to 8 px with a 14 px inward glow (v25), switched rain/drizzle to high-contrast saturated blue drops (v26), and softened the inner/outer ring halo opacity by 25% (v27). |
