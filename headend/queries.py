"""Small, explicit information handlers. No remote speech, LLM, or device actions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import math
import re
import threading
import time
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator


def normalized(text):
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()


# Speech recognition often omits the comma between a US city and state.
US_STATES = dict(zip(
    'AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC'.split(),
    ('Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|District of Columbia').split('|')))


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

    @model_validator(mode='after')
    def labels(self):
        if not normalized(self.name) or any(not normalized(a) or len(a)>60 for a in self.aliases):
            raise ValueError('Use nonempty location names and aliases of up to 60 characters')
        return self


class QuerySettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    provider: Literal['open-meteo'] = 'open-meteo'
    units: Literal['fahrenheit', 'celsius'] = 'fahrenheit'
    voice_id: str | None = Field(default=None, min_length=5, max_length=90)
    default_location: str = 'Shelton, Washington'
    locations: list[Location] = Field(default_factory=lambda: [Location(
        name='Shelton, Washington', latitude=47.21509, longitude=-123.10071,
        aliases=['Shelton', 'Shelton WA', 'home'])], min_length=1, max_length=16)

    @model_validator(mode='after')
    def locations_valid(self):
        if self.default_location not in [p.name for p in self.locations]:
            raise ValueError('Choose a saved location as the default')
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
                places.append(Location(name=label[:60], latitude=row['latitude'], longitude=row['longitude']))
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
        if options.voice_id:
            self.voices.registry.resolve(options.voice_id)
        with self.state.db() as db:
            db.execute('INSERT OR REPLACE INTO query_settings VALUES(1,?)', (options.model_dump_json(),))
        return options

    def resolve(self, name, settings):
        if not name or normalized(name) in ('here', 'outside', 'my location'):
            return next(p for p in settings.locations if p.name==settings.default_location)
        key = normalized(name)
        for place in settings.locations:
            if key in {normalized(s) for s in [place.name, *place.aliases]}:
                return place
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

    def answer(self, text):
        options = self.settings()
        intent = weather_intent(text)
        result = dict(status='ignored', text='', source='', source_url='')
        if not options.enabled or not intent:
            return result
        result.update(status='answered', voice_id=options.voice_id)
        if intent.get('unsupported'):
            return dict(result, text='I can check current weather, today, or tomorrow. Please ask for one of those.')
        try:
            place = self.resolve(intent['place'], options)
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
                from datetime import timedelta
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
            return
        if not self.settings().enabled or not weather_intent(job['text']):
            entry['reply'] = dict(status='ignored', text='')
            return
        if not self.slots.acquire(blocking=False):
            entry['reply'] = dict(status='error', text='', error='Information reply queue is full')
            return
        entry['reply'] = dict(status='working', text='')
        try:
            self.pool.submit(self._run, entry, job['text'])
        except RuntimeError:
            self.slots.release()
            entry['reply'] = dict(status='error', text='', error='Head-end is stopping')

    def _run(self, entry, text):
        try:
            if time.time()-entry['started']>90:
                raise ValueError('Question expired while waiting for processing')
            started=time.monotonic()
            result = self.answer(text)
            result['lookup_ms']=round((time.monotonic()-started)*1000)
            if not self.closed and self.settings().enabled:
                entry['reply'] = self.deliver(result, entry['unit_id'])
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
        return service.save(options)

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
            result = service.answer(options.text)
            return service.deliver(result, options.unit_id) if options.unit_id else result
        finally:
            service.slots.release()
