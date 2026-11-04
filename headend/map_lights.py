"""Persistent map placement and read-only light status; never sends HA actions."""
import math
import threading
import time
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .home_assistant import HomeAssistantUnavailable


ROOMS = (('living', 'Living room'), ('kitchen', 'Kitchen'),
         ('bedroom', 'Bedroom'), ('study', 'Study'))
ROOM_IDS = {key for key, _ in ROOMS}
LAYOUT = 'sample-home-v1'


class LightPlacement(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    room_id: str | None = Field(default=None, max_length=40)
    version: str = Field(min_length=1, max_length=60)


class MapLights:
    def __init__(self, state, home_assistant, clock=time.monotonic, wall_clock=time.time):
        self.state, self.ha = state, home_assistant
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.Lock()
        self.instance = uuid.uuid4().hex
        self.cached = None
        with state.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS map_light_placements (
                origin TEXT NOT NULL, layout TEXT NOT NULL, entity_id TEXT NOT NULL,
                room_id TEXT NOT NULL, name TEXT NOT NULL,
                PRIMARY KEY(origin,layout,entity_id))''')

    def version(self):
        # Reject an old browser's placement after HA changes or a server restart.
        return f'{self.instance}:{self.ha.revision}'

    @staticmethod
    def eligible(row):
        return (row['entity_id'].startswith('light.') and row['imported']
                and row['enabled'] and row['present'])

    def place(self, entity_id, options):
        if options.room_id is not None and options.room_id not in ROOM_IDS:
            raise HTTPException(422, 'Choose a room in this map.')
        if not entity_id.startswith('light.') or len(entity_id) > 160:
            raise HTTPException(422, 'Only Home Assistant lights can be placed on this map.')
        with self.ha.lock:
            settings = self.ha.settings()
            if options.version != self.version():
                raise HTTPException(409, 'Device settings changed. Refresh the map and try again.')
            row = next((r for r in self.ha.inventory.rows(settings) if r['entity_id'] == entity_id), None)
            if options.room_id is not None and (not settings.enabled or not row or not self.eligible(row)):
                raise HTTPException(409, 'Import and enable a present light before assigning it.')
            with self.state.db() as db:
                if options.room_id is None:
                    db.execute('DELETE FROM map_light_placements WHERE origin=? AND layout=? AND entity_id=?',
                               (settings.url, LAYOUT, entity_id))
                else:
                    db.execute('''INSERT INTO map_light_placements VALUES(?,?,?,?,?)
                        ON CONFLICT(origin,layout,entity_id) DO UPDATE SET room_id=excluded.room_id,name=excluded.name''',
                               (settings.url, LAYOUT, entity_id, options.room_id, row['name']))
        return dict(entity_id=entity_id, room_id=options.room_id)

    def snapshot(self):
        # Share one bounded HA read across dashboard tabs, including failures.
        with self.lock:
            with self.ha.lock:
                settings, version = self.ha.settings(), self.version()
            if self.cached is None or self.cached['version'] != version or self.clock()-self.cached['time'] >= 5:
                live, error, checked_at = {}, '', None
                if settings.enabled:
                    try:
                        live = {r['entity_id']: r for r in self.ha.client.entities(settings)
                                if r['entity_id'].startswith('light.')}
                        checked_at = self.wall_clock()
                    except HomeAssistantUnavailable:
                        error = 'Light status is unavailable. Check the Home Assistant connection.'
                else:
                    error = 'Enable Home Assistant to see live light status.'
                self.cached = dict(version=version, time=self.clock(), live=live,
                                   error=error, checked_at=checked_at)
            with self.ha.lock:
                # Never return a previous origin's names/states after a settings change.
                if version != self.version():
                    self.cached = None
                    raise HTTPException(409, 'Device settings changed. Refresh the map.')
                inventory = {r['entity_id']: r for r in self.ha.inventory.rows(settings)}
                with self.state.db() as db:
                    placed = {r['entity_id']: dict(r) for r in db.execute(
                        'SELECT entity_id,room_id,name FROM map_light_placements WHERE origin=? AND layout=?',
                        (settings.url, LAYOUT))}
                devices = []
                ids = {key for key, row in inventory.items() if self.eligible(row)} | placed.keys()
                for key in ids:
                    row, placement = inventory.get(key), placed.get(key)
                    assignable = bool(settings.enabled and row and self.eligible(row))
                    live = self.cached['live'].get(key, {}) if assignable else {}
                    status = live.get('state')
                    if status not in ('on', 'off'):
                        status = 'unavailable'
                    brightness = live.get('attributes', {}).get('brightness')
                    if type(brightness) not in (int, float) or not math.isfinite(brightness) or not 0 <= brightness <= 255:
                        brightness = 255
                    devices.append(dict(entity_id=key, name=row['name'] if row else placement['name'],
                                        room_id=placement['room_id'] if placement else None,
                                        state=status, brightness=round(brightness/255, 3) if status == 'on' else 0,
                                        assignable=assignable))
                rooms = []
                for room_id, name in ROOMS:
                    lights = [d for d in devices if d['room_id'] == room_id]
                    on = sum(d['state'] == 'on' for d in lights)
                    unknown = sum(d['state'] == 'unavailable' for d in lights)
                    rooms.append(dict(id=room_id, name=name, assigned_count=len(lights), on_count=on,
                                      unknown_count=unknown, state='on' if on else 'unknown' if unknown else 'off' if lights else 'unassigned',
                                      brightness=max((d['brightness'] for d in lights), default=0)))
                return dict(layout=LAYOUT, version=version, enabled=settings.enabled,
                            checked_at=self.cached['checked_at'], error=self.cached['error'], rooms=rooms,
                            devices=sorted(devices, key=lambda d: (d['name'].casefold(), d['entity_id'])))


def add_map_light_routes(router, service):
    @router.get('/api/map/lights')
    def snapshot():
        return service.snapshot()

    @router.put('/api/map/lights/{entity_id}')
    def place(entity_id: str, options: LightPlacement):
        return service.place(entity_id, options)
