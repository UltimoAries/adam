"""Cached clock/weather metadata for the device home screen; never blocks audio polling."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import math
import threading
import time
import unicodedata
from zoneinfo import ZoneInfo

from .queries import CONDITIONS


def panel_text(value, limit=32):
    return unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()[:limit]


class PanelService:
    def __init__(self, queries, enabled=True, clock=time.time):
        self.queries, self.enabled, self.clock = queries, enabled, clock
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='panel-weather')
        self.closed = False
        self.entries = {}

    def snapshot(self, device_name='', unit_id=None):
        options = self.queries.settings()
        place = self.queries.default_place(options, unit_id)
        signature = (options.enabled, options.units, place.name, place.latitude, place.longitude, place.timezone)
        now = self.clock()
        with self.lock:
            if signature not in self.entries:
                if len(self.entries) >= 64:
                    self.entries.pop(next(iter(self.entries)))
                self.entries[signature] = dict(pending=False, next_update=0, cached=dict(
                    location=panel_text(place.name.split(',')[0]), units='F' if options.units == 'fahrenheit' else 'C',
                    weather_state='loading' if options.enabled else 'disabled'))
            entry = self.entries[signature]
            if options.enabled and self.enabled and not self.closed and not entry['pending'] and now >= entry['next_update']:
                entry['pending'] = True
                try:
                    self.pool.submit(self._refresh, entry, place, options.units)
                except RuntimeError:
                    entry['pending'] = False
            result = dict(entry['cached'])
        # Keep a usable clock even while weather is disabled/unavailable at startup.
        result.setdefault('utc_offset', int(datetime.now().astimezone().utcoffset().total_seconds()))
        if place.timezone:
            result['utc_offset'] = int(datetime.fromtimestamp(now, ZoneInfo(place.timezone)).utcoffset().total_seconds())
        result['unix_time'] = int(now)
        result['device_name'] = panel_text(device_name, 24) if options.panel_show_device_name else ''
        if result.get('weather_state') == 'ready' and now - result.get('observed_at', 0) > 7200:
            result['weather_state'] = 'stale'
            result.pop('temperature', None)
        return result

    def _refresh(self, entry, place, units):
        try:
            data, fetched = self.queries.provider.forecast(place, units)
            current = data['current']
            offset, value, code = data['utc_offset_seconds'], current['temperature_2m'], current['weather_code']
            if (type(offset) is not int or not -50400 <= offset <= 50400
                    or isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value)
                    or not -150 <= value <= 180 or type(code) is not int or code not in CONDITIONS):
                raise ValueError('Invalid panel weather')
            stamp = datetime.fromisoformat(current['time']).replace(tzinfo=timezone.utc).timestamp() - offset
            if abs(self.clock() - stamp) > 7200:
                raise ValueError('Stale panel weather')
            result = dict(weather_state='ready', temperature=round(value), weather_code=code, observed_at=int(stamp),
                          utc_offset=offset, fetched_at=int(fetched))
            delay = 600
        except Exception:
            result, delay = dict(weather_state='unavailable'), 60
        with self.lock:
            if not self.closed:
                entry['cached'].update(result)
                if result['weather_state'] != 'ready':
                    entry['cached'].pop('temperature', None)
                entry['next_update'] = self.clock() + delay
            entry['pending'] = False

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
