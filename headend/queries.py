"""Small, explicit information handlers. No remote speech, LLM, or device actions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import math
import re
import threading
import time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


def normalized(text):
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()


# Speech recognition often omits the comma between a US city and state.
US_STATES = dict(zip(
    'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split(),
    ('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|District of Columbia').split('|')))

_WEEKDAYS = {
    'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
    'friday': 4, 'saturday': 5, 'sunday': 6,
}
_MONTHS = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
    'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
    'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
}
_FIXED_HOLIDAYS = {
    'new year': (1, 1, "New Year's Day"),
    'new years': (1, 1, "New Year's Day"),
    'new years day': (1, 1, "New Year's Day"),
    'valentine': (2, 14, "Valentine's Day"),
    'valentines': (2, 14, "Valentine's Day"),
    'valentines day': (2, 14, "Valentine's Day"),
    'halloween': (10, 31, 'Halloween'),
    'christmas': (12, 25, 'Christmas'),
    'christmas day': (12, 25, 'Christmas'),
    'christmas eve': (12, 24, 'Christmas Eve'),
    'new years eve': (12, 31, "New Year's Eve"),
}


def search_name(name):
    if ',' not in name:
        for code, state in sorted(US_STATES.items(), key=lambda pair:len(pair[1]), reverse=True):
            for suffix in (state, code):
                if name.casefold().endswith(' '+suffix.casefold()):
                    return name[:-len(suffix)].rstrip()+', '+state
    return name


class Location(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=60)
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    timezone: str = Field(default='', max_length=64)

    @model_validator(mode='after')
    def labels(self):
        if not normalized(self.name) or any(not normalized(a) or len(a)>60 for a in self.aliases):
            raise ValueError('Use nonempty location names and aliases of up to 60 characters')
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError('Use a recognized location time zone') from exc
        return self


class QuerySettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    provider: Literal['open-meteo'] = 'open-meteo'
    units: Literal['fahrenheit', 'celsius'] = 'fahrenheit'
    voice_id: str | None = Field(default=None, min_length=5, max_length=90)
    default_location: str = 'Seattle, Washington'
    device_locations: dict[str, str] = Field(default_factory=dict, max_length=32)
    panel_show_device_name: bool = True
    locations: list[Location] = Field(default_factory=lambda: [Location(
        name='Seattle, Washington', latitude=47.6062, longitude=-122.3321,
        aliases=['Seattle', 'Seattle WA', 'home'], timezone='America/Los_Angeles')], min_length=1, max_length=16)

    @model_validator(mode='after')
    def locations_valid(self):
        if self.default_location not in [p.name for p in self.locations]:
            raise ValueError('Choose a saved location as the default')
        if any(not re.fullmatch(r'[a-f0-9]{16}', unit) or name not in [p.name for p in self.locations]
               for unit, name in self.device_locations.items()):
            raise ValueError('Choose a saved location for each device')
        used = set()
        for place in self.locations:
            keys = {normalized(s) for s in [place.name, *place.aliases]}
            if used & keys:
                raise ValueError('Location names and aliases must identify only one saved place')
            used.update(keys)
        return self


class QueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    unit_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')


class WeatherUnavailable(Exception):
    pass


def _parse_calendar_target(raw: str, today: date) -> tuple[date, str] | None:
    key = normalized(raw)
    if key in _FIXED_HOLIDAYS:
        month, day_num, label = _FIXED_HOLIDAYS[key]
        target = date(today.year, month, day_num)
        if target < today:
            target = date(today.year + 1, month, day_num)
        return target, label
    if key in _WEEKDAYS:
        target_wd = _WEEKDAYS[key]
        delta = (target_wd - today.weekday()) % 7
        if delta == 0:
            delta = 7
        target = today + timedelta(days=delta)
        return target, target.strftime('%A')
    m = re.fullmatch(
        r'([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s+(\d{4}))?',
        key,
    )
    if m and m.group(1) in _MONTHS:
        month = _MONTHS[m.group(1)]
        day_num = int(m.group(2))
        explicit_year = int(m.group(3)) if m.group(3) else None
        if explicit_year is not None:
            if not 1900 <= explicit_year <= 2200:
                raise ValueError('Choose a year between 1900 and 2200.')
            target = date(explicit_year, month, day_num)
            return target, f"{target.strftime('%B')} {target.day}, {target.year}"
        for yr in (today.year, today.year + 1, today.year + 4):
            try:
                candidate = date(yr, month, day_num)
            except ValueError:
                continue
            if candidate >= today:
                return candidate, f"{candidate.strftime('%B')} {candidate.day}"
        raise ValueError('That calendar date is not valid.')
    return None


def time_date_intent(text: str) -> dict | None:
    """Match local/world clock, current date, and calendar day-count questions."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        return None
    cleaned = text.replace('\u2019', "'").strip()
    cleaned = re.sub(r'^\s*(?:(?:hey|okay|ok)[ ,]+)?(?:jarvis|adam)\b[\s,.:!?-]*', '', cleaned, flags=re.I)
    cleaned = re.sub(r'^\s*(?:please[ ,]+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?(?:tell\s+me\s+)?', '', cleaned, flags=re.I)
    cleaned = cleaned.strip().rstrip('.!?').strip()
    if not cleaned:
        return None
    lower = normalized(cleaned)
    if re.search(r'\b(?:timer|timers|left|remaining|weather|forecast|temperature|rain|snow)\b', lower):
        return None

    # Local or world clock
    time_patterns = (
        r'(?:what time is it|what is the (?:current |local )?time|whats the (?:current |local )?time|what s the (?:current |local )?time|current time|time)(?: (?:right now|now))?(?: in (.+))?',
        r'what is the time in (.+)',
    )
    for pat in time_patterns:
        m = re.fullmatch(pat, lower)
        if m:
            raw_place = ''
            if m.lastindex and m.group(1):
                # Recover original casing/punctuation for place search
                loc_match = re.search(r'\bin\s+(.+)$', cleaned, re.I)
                raw_place = (loc_match.group(1) if loc_match else m.group(1)).strip(' ,.!?')
                raw_place = re.sub(r'\b(?:right now|now|please)\b', '', raw_place, flags=re.I).strip(' ,.!?')
            if normalized(raw_place) in ('', 'here', 'home', 'my location'):
                raw_place = ''
            return dict(kind='time', place=raw_place)

    # Current or tomorrow's date / day of week
    if re.fullmatch(
        r'(?:what day is (?:it|today)|what is (?:today|todays|today s) date|whats (?:today|todays|today s) date|'
        r'what s (?:today|todays|today s) date|what is the date(?: today)?|whats the date(?: today)?|'
        r'what s the date(?: today)?|what day of the week is (?:it|today)|todays date|today s date)',
        lower,
    ):
        return dict(kind='date', offset_days=0)

    if re.fullmatch(
        r'(?:what day is tomorrow|what is (?:tomorrows|tomorrow s) date|whats (?:tomorrows|tomorrow s) date|'
        r'what s (?:tomorrows|tomorrow s) date|what is the date tomorrow)',
        lower,
    ):
        return dict(kind='date', offset_days=1)

    # Days until <target>
    m = re.fullmatch(r'(?:how many )?days until (.+)', lower)
    if m:
        return dict(kind='days_until', target=m.group(1).strip())

    # Day of week for a calendar date ("what day of the week is november 15 2026")
    m = re.fullmatch(r'what day(?: of the week)? (?:is|was|will be) (.+)', lower)
    if m and m.group(1) not in ('it', 'today', 'tomorrow'):
        return dict(kind='weekday_of', target=m.group(1).strip())

    return None


def weather_intent(text):
    """Conservative routing; unsupported questions must not become current weather."""
    text = text.replace('\u2019', "'").strip()
    text = re.sub(r'^\s*(?:hey|okay|ok)[ ,]+(?:jarvis|adam)[\s,.!?]*', '', text, flags=re.I)
    if not re.search(r'\b(weather|temperature|forecast|rain|raining|snow|snowing)\b', text, re.I):
        return None
    if not re.match(r"^(?:what\b|what's\b|whats\b|how\b|is\b|will\b|do\b|does\b|tell\b|give\b|weather\b|temperature\b|forecast\b)", text, re.I):
        return None
    if re.search(r'\b(yesterday|was|last|ago|week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|tonight|hour|month|year)\b', text, re.I):
        return dict(unsupported=True)
    day = 'tomorrow' if re.search(r'\btomorrow\b', text, re.I) else 'today' if re.search(r'\b(today|forecast|will)\b', text, re.I) else 'current'
    # Remove time phrases before extracting a place: "forecast for tomorrow
    # in Seattle" must not resolve a city called "in Seattle".
    text = re.sub(r'\b(?:for\s+)?(?:today|tomorrow|right now|now)\b', '', text, flags=re.I)
    match = re.search(r'\b(?:in|at|for)\s+(.+)', text, re.I)
    place = match.group(1) if match else ''
    place = re.sub(r'\b(?:today|tomorrow|right now|now|please)\b', '', place, flags=re.I).strip(' ,.!?')
    return dict(day=day, place=place)


CONDITIONS = {0:'clear', 1:'mostly clear', 2:'partly cloudy', 3:'overcast',
    45:'foggy', 48:'foggy', 51:'light drizzle', 53:'drizzle', 55:'heavy drizzle',
    56:'freezing drizzle', 57:'freezing drizzle', 61:'light rain', 63:'rain', 65:'heavy rain',
    66:'freezing rain', 67:'freezing rain', 71:'light snow', 73:'snow', 75:'heavy snow',
    77:'snow grains', 80:'rain showers', 81:'rain showers', 82:'heavy showers',
    85:'snow showers', 86:'heavy snow showers', 95:'thunderstorms', 96:'thunderstorms with hail', 99:'thunderstorms with hail'}


class OpenMeteo:
    """Fixed HTTPS endpoints, bounded reads and a ten-minute in-memory forecast cache."""
    source = 'Open-Meteo'
    source_url = 'https://open-meteo.com/'

    def __init__(self, fetch=None, clock=time.time):
        self.fetch = fetch or self._fetch
        self.clock = clock
        self.cache = {}
        self.lock = threading.Lock()

    @staticmethod
    def _fetch(url, params):
        try:
            with httpx.Client(timeout=5, follow_redirects=False) as client:
                with client.stream('GET', url, params=params) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw)>256000:
                            raise WeatherUnavailable('Weather response exceeded its limit')
                    return json.loads(raw)
        except (httpx.HTTPError, ValueError) as exc:
            raise WeatherUnavailable('Weather service is unavailable. Please try again shortly.') from exc

    def search(self, name):
        data = self.fetch('https://geocoding-api.open-meteo.com/v1/search',
                          dict(name=search_name(name), count=10, language='en', format='json'))
        places = []
        try:
            for row in data.get('results', [])[:10]:
                label = ', '.join(dict.fromkeys(x for x in [row['name'], row.get('admin1'), row.get('country_code')] if x))
                places.append(Location(name=label[:60], latitude=row['latitude'], longitude=row['longitude'], timezone=row.get('timezone', '')))
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherUnavailable('Location search returned incomplete data') from exc
        return places

    def forecast(self, location, units):
        key = (location.latitude, location.longitude, units)
        with self.lock:
            cached = self.cache.get(key)
            if cached and self.clock()-cached[0]<600:
                return cached[1], cached[0]
        data = self.fetch('https://api.open-meteo.com/v1/forecast', dict(
            latitude=location.latitude, longitude=location.longitude, timezone='auto', forecast_days=2,
            temperature_unit=units, current='temperature_2m,apparent_temperature,weather_code',
            daily='weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max'))
        now = self.clock()
        with self.lock:
            if len(self.cache)>=64:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (now, data)
        return data, now


class QueryService:
    def __init__(self, state, voices, speech, playback_pcm, provider=None):
        self.state, self.voices, self.speech = state, voices, speech
        self.playback_pcm = playback_pcm
        self.provider = provider or OpenMeteo()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='information-reply')
        self.slots = threading.BoundedSemaphore(4)
        self.closed = False
        with state.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS query_settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')

    def settings(self):
        with self.state.db() as db:
            row = db.execute('SELECT data FROM query_settings WHERE id=1').fetchone()
        return QuerySettings.model_validate_json(row[0]) if row else QuerySettings()

    def save(self, options):
        options = QuerySettings.model_validate(options.model_dump())
        if options.voice_id:
            self.voices.registry.resolve(options.voice_id)
        with self.state.db() as db:
            db.execute('INSERT OR REPLACE INTO query_settings VALUES(1,?)', (options.model_dump_json(),))
        return options

    @staticmethod
    def default_place(settings, unit_id=None):
        name = settings.device_locations.get(unit_id, settings.default_location)
        return next(p for p in settings.locations if p.name == name)

    def resolve(self, name, settings, unit_id=None):
        if not name or normalized(name) in ('home', 'here', 'outside', 'my location'):
            return self.default_place(settings, unit_id)
        key = normalized(name)
        for place in settings.locations:
            if key in {normalized(s) for s in [place.name, *place.aliases]}:
                return place
        city_matches = [p for p in settings.locations if normalized(p.name.split(',')[0]) == key]
        if len(city_matches) == 1:
            return city_matches[0]
        places = self.provider.search(name)
        # Search is prefix-based; a city and its airport are not two cities.
        city = normalized(search_name(name).split(',')[0])
        exact = [p for p in places if normalized(p.name.split(',')[0])==city]
        if exact:
            places = exact
        if not places:
            raise WeatherUnavailable('I could not find that place. Try its city and state, or save a location alias.')
        if len(places)>1:
            raise WeatherUnavailable('That location is ambiguous. Please use a saved location name or a city and state.')
        return places[0]

    def _local_now(self, place: Location) -> datetime:
        stamp = self.provider.clock()
        if place.timezone:
            return datetime.fromtimestamp(stamp, ZoneInfo(place.timezone))
        return datetime.fromtimestamp(stamp).astimezone()

    @staticmethod
    def _spoken_clock(dt: datetime) -> str:
        hour = dt.hour % 12 or 12
        period = 'PM' if dt.hour >= 12 else 'AM'
        return f'{hour}:{dt.minute:02d} {period}'

    def _answer_time_date(self, intent: dict, options: QuerySettings, unit_id: str | None) -> dict:
        base = dict(status='answered', voice_id=options.voice_id, source='System Clock', source_url='')
        try:
            if intent['kind'] == 'time':
                if intent['place']:
                    place = self.resolve(intent['place'], options, unit_id)
                    if not place.timezone:
                        return dict(
                            base,
                            status='unavailable',
                            text='Time zone information is unavailable for that location.',
                        )
                    dt = self._local_now(place)
                    label = place.name.split(',')[0][:35]
                    return dict(
                        base,
                        text=f"In {label}, it is {self._spoken_clock(dt)} on {dt.strftime('%A, %B')} {dt.day}.",
                        location=place.name,
                        source=self.provider.source,
                        source_url=self.provider.source_url,
                    )
                place = self.default_place(options, unit_id)
                dt = self._local_now(place)
                return dict(base, text=f"It is {self._spoken_clock(dt)} on {dt.strftime('%A, %B')} {dt.day}.", location=place.name)

            place = self.default_place(options, unit_id)
            today = self._local_now(place).date()
            if intent['kind'] == 'date':
                target = today + timedelta(days=intent['offset_days'])
                prefix = 'Tomorrow is' if intent['offset_days'] == 1 else 'Today is'
                return dict(base, text=f"{prefix} {target.strftime('%A, %B')} {target.day}, {target.year}.", location=place.name)

            parsed = _parse_calendar_target(intent['target'], today)
            if not parsed:
                return dict(base, status='ignored', text='')
            target_date, label = parsed
            if intent['kind'] == 'weekday_of':
                verb = 'was' if target_date < today else 'is'
                return dict(base, text=f"{label} {verb} a {target_date.strftime('%A')}.")
            days = (target_date - today).days
            if days == 0:
                return dict(base, text=f'Today is {label}.')
            if days == 1:
                return dict(base, text=f"Tomorrow is {label} ({target_date.strftime('%B')} {target_date.day}).")
            return dict(
                base,
                text=f"There are {days} days until {label} ({target_date.strftime('%A, %B')} {target_date.day}).",
            )
        except WeatherUnavailable as exc:
            return dict(base, status='unavailable', text=str(exc))
        except ValueError as exc:
            return dict(base, status='unavailable', text=str(exc))

    def answer(self, text, unit_id=None):
        options = self.settings()
        td_intent = time_date_intent(text)
        if td_intent:
            return self._answer_time_date(td_intent, options, unit_id)
        intent = weather_intent(text)
        result = dict(status='ignored', text='', source='', source_url='')
        if not options.enabled or not intent:
            return result
        result.update(status='answered', voice_id=options.voice_id)
        if intent.get('unsupported'):
            return dict(result, text='I can check current weather, today, or tomorrow. Please ask for one of those.')
        try:
            place = self.resolve(intent['place'], options, unit_id)
            data, fetched = self.provider.forecast(place, options.units)
            unit = 'Fahrenheit' if options.units=='fahrenheit' else 'Celsius'
            def number(value):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Missing weather value')
                return round(value)
            label = place.name.split(',')[0][:35]
            if intent['day']=='current':
                current = data['current']
                # Open-Meteo returns local timestamps plus the UTC offset.
                stamp = datetime.fromisoformat(current['time']).replace(tzinfo=timezone.utc).timestamp()-data['utc_offset_seconds']
                if abs(self.provider.clock()-stamp)>7200:
                    raise WeatherUnavailable('The weather source returned old data. Please try again shortly.')
                temperature = number(current['temperature_2m'])
                condition = CONDITIONS[current['weather_code']]
                reply = f'In {label}, it is {temperature} degrees {unit} and {condition}. Feels like {number(current["apparent_temperature"])} degrees.'
                valid_time = current['time']
            else:
                daily = data['daily']
                i = 1 if intent['day']=='tomorrow' else 0
                expected = datetime.fromtimestamp(self.provider.clock()+data['utc_offset_seconds'], timezone.utc).date()
                if daily['time'][i] != (expected+timedelta(days=i)).isoformat():
                    raise WeatherUnavailable('The weather source returned an old forecast. Please try again shortly.')
                condition = CONDITIONS[daily['weather_code'][i]]
                chance = number(daily['precipitation_probability_max'][i])
                if not 0<=chance<=100:
                    raise ValueError('Invalid precipitation probability')
                reply = f'{intent["day"].capitalize()} in {label}: {condition}, high {number(daily["temperature_2m_max"][i])}, low {number(daily["temperature_2m_min"][i])} {unit}. A {chance} percent chance of precipitation.'
                valid_time = daily['time'][i]
            return dict(result, text=reply, source=self.provider.source, source_url=self.provider.source_url,
                        location=place.name, fetched_at=fetched, valid_time=valid_time)
        except WeatherUnavailable as exc:
            return dict(result, status='unavailable', text=str(exc))
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            return dict(result, status='unavailable', text='The weather source returned incomplete data. Please try again shortly.')

    def deliver(self, result, unit_id):
        if not result['text']:
            return result
        started=time.monotonic()
        if result.get('voice_id'):
            pcm = self.voices.synthesize(result['text'], result['voice_id'])
        else:
            pcm = self.speech.synthesize(result['text'])
        pages = self.state.enqueue([unit_id], self.playback_pcm(pcm), 'text', result['text'])
        return dict(result, status='queued', answer_status=result['status'], page_id=pages[0]['id'],
                    synthesis_ms=round((time.monotonic()-started)*1000))

    def submit(self, entry, job):
        # Only new, successful wake sessions generate automatic answers. Bench
        # retranscriptions and remote Listen checks never cause surprise replies.
        if self.closed or job['status']!='done' or entry['trigger']!='wake':
            return False
        is_time = bool(time_date_intent(job['text']))
        is_weather = bool(self.settings().enabled and weather_intent(job['text']))
        if not is_time and not is_weather:
            entry['reply'] = dict(status='ignored', text='')
            return False
        if not self.slots.acquire(blocking=False):
            entry['reply'] = dict(status='error', text='', error='Information reply queue is full')
            return True
        entry['reply'] = dict(status='working', text='')
        try:
            self.pool.submit(self._run, entry, job['text'], is_time)
        except RuntimeError:
            self.slots.release()
            entry['reply'] = dict(status='error', text='', error='Head-end is stopping')
        return True

    def _run(self, entry, text, is_time=False):
        try:
            if time.time()-entry['started']>90:
                raise ValueError('Question expired while waiting for processing')
            started=time.monotonic()
            result = self.answer(text, entry['unit_id'])
            result['lookup_ms']=round((time.monotonic()-started)*1000)
            if not self.closed and result.get('text') and (is_time or self.settings().enabled):
                entry['reply'] = self.deliver(result, entry['unit_id'])
            else:
                entry['reply'] = result
        except Exception as exc:
            entry['reply'] = dict(status='error', text='', error=str(exc)[:160])
        finally:
            self.slots.release()

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)


def add_query_routes(router, service):
    @router.get('/api/queries/settings')
    def get_settings():
        return service.settings()

    @router.put('/api/queries/settings')
    def save_settings(options: QuerySettings):
        try:
            return service.save(options)
        except WeatherUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get('/api/queries/locations')
    def search_locations(name: str):
        if not 2<=len(name.strip())<=80:
            raise ValueError('Use 2–80 characters for a location search')
        try:
            return service.provider.search(name.strip())
        except WeatherUnavailable as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.post('/api/queries/test')
    def test_query(options: QueryRequest):
        if options.unit_id and not any(u['id']==options.unit_id and u['online'] for u in service.state.units()):
            raise HTTPException(409, 'Choose an online unit')
        if not service.slots.acquire(blocking=False):
            raise HTTPException(429, 'Information reply queue is full')
        try:
            result = service.answer(options.text, options.unit_id)
            return service.deliver(result, options.unit_id) if options.unit_id else result
        finally:
            service.slots.release()
