"""Persistent floor-plan markers, live light status, and bounded HA controls."""
import math
import threading
import time
import uuid

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from .home_assistant import DeviceAction, HomeAssistantUnavailable
from .ha_devices import controls
from .map_layout import contains, load_private_layout


# Reference layout from the maintainer's map-tap-control prototype. Keep the
# bedroom/study IDs so existing room assignments retain their meaning.
GEOMETRY = (
    ('living', 'Living room', 70, 120, 360, 380),
    ('kitchen', 'Kitchen & dining', 430, 120, 270, 245),
    ('bedroom', 'Primary bedroom', 700, 120, 230, 245),
    ('hall', 'Main hallway', 430, 365, 270, 135),
    ('bath', 'En-suite bath', 700, 365, 230, 135),
    ('entry', 'Entry', 430, 500, 135, 120),
    ('study', 'Guest room / office', 565, 500, 365, 120),
    ('patio', 'Patio', 70, 500, 360, 120),
)
ROOMS = tuple((r[0], r[1]) for r in GEOMETRY)
BOUNDS = {r[0]: dict(x=r[2], y=r[3], width=r[4], height=r[5]) for r in GEOMETRY}
LAYOUT = 'floor-plan-v2'


class LightPlacement(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    room_id: str | None = Field(default=None, max_length=40)
    version: str = Field(min_length=1, max_length=60)
    x: float | None = Field(default=None, ge=-60, le=1060, allow_inf_nan=False)
    y: float | None = Field(default=None, ge=-75, le=775, allow_inf_nan=False)


class LightAction(DeviceAction):
    version: str = Field(min_length=1, max_length=60)


class MapLights:
    def __init__(self, state, home_assistant, clock=time.monotonic, wall_clock=time.time):
        self.state, self.ha = state, home_assistant
        self.clock, self.wall_clock = clock, wall_clock
        self.lock = threading.Lock()
        self.instance = uuid.uuid4().hex
        self.cached = None
        self.layout_error = ''
        try:
            custom = load_private_layout(state.directory)
        except ValueError as exc:
            custom = None
            self.layout_error = str(exc)
        self.layout = custom['id'] if custom else LAYOUT
        self.image = custom['image'] if custom else None
        self.name = custom['name'] if custom else 'Reference layout'
        self.aspect_ratio = custom['aspect_ratio'] if custom else 10/7
        self.rooms = {r['id']: r for r in custom['rooms']} if custom else {
            key: dict(id=key, name=name, bounds=BOUNDS[key],
                      anchor=[BOUNDS[key]['x']+BOUNDS[key]['width']/2, BOUNDS[key]['y']+BOUNDS[key]['height']/2])
            for key, name in ROOMS}
        self.viewport = dict(x=0, y=0, width=1000, height=700)
        if custom:
            # Extend the canvas without moving interior geometry or changing its
            # placement key. Outdoor bands never overlap the original image.
            self.viewport = dict(x=-60, y=-75, width=1120, height=850)
            for key, name, points, anchor in (
                ('outdoor_back', 'Back outside', [[-60,-75],[1060,-75],[1060,350],[1000,350],[1000,0],[0,0],[0,350],[-60,350]], [500,-38]),
                ('outdoor_front', 'Front outside', [[-60,350],[0,350],[0,700],[1000,700],[1000,350],[1060,350],[1060,775],[-60,775]], [500,738]),
            ):
                if key in self.rooms:
                    continue
                xs, ys = zip(*points)
                self.rooms[key] = dict(id=key, name=name, points=points, anchor=anchor, outdoor=True,
                                      bounds=dict(x=min(xs), y=min(ys), width=max(xs)-min(xs), height=max(ys)-min(ys)))
        with state.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS map_light_placements (
                origin TEXT NOT NULL, layout TEXT NOT NULL, entity_id TEXT NOT NULL,
                room_id TEXT NOT NULL, name TEXT NOT NULL,
                PRIMARY KEY(origin,layout,entity_id))''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(map_light_placements)')}
            for column in ('x', 'y'):
                if column not in columns:
                    db.execute(f'ALTER TABLE map_light_placements ADD COLUMN {column} REAL')
            # Update in place once; removing a marker must not resurrect it later.
            db.execute('''INSERT OR IGNORE INTO map_light_placements(origin,layout,entity_id,room_id,name,x,y)
                          SELECT origin,?,entity_id,room_id,name,x,y FROM map_light_placements WHERE layout=?''',
                       (LAYOUT, 'sample-home-v1'))
            db.execute('DELETE FROM map_light_placements WHERE layout=?', ('sample-home-v1',))
            for room_id, bounds in BOUNDS.items():
                rows = db.execute('SELECT origin,entity_id FROM map_light_placements WHERE layout=? AND room_id=? AND x IS NULL ORDER BY origin,entity_id',
                                  (LAYOUT, room_id)).fetchall()
                for i, row in enumerate(rows):
                    x = bounds['x'] + bounds['width'] * (.25 + .25 * (i % 3))
                    y = bounds['y'] + bounds['height'] * (.45 + .15 * ((i // 3) % 3))
                    db.execute('UPDATE map_light_placements SET x=?,y=? WHERE origin=? AND layout=? AND entity_id=?',
                               (x, y, row['origin'], LAYOUT, row['entity_id']))

    def version(self):
        # Reject an old browser's placement after HA changes or a server restart.
        return f'{self.instance}:{self.ha.revision}'

    @staticmethod
    def eligible(row):
        return (row['entity_id'].startswith('light.') and row['imported']
                and row['enabled'] and row['present'])

    def place(self, entity_id, options):
        if options.room_id is not None and options.room_id not in self.rooms:
            raise HTTPException(422, 'Choose a room in this map.')
        if not entity_id.startswith('light.') or len(entity_id) > 160:
            raise HTTPException(422, 'Only Home Assistant lights can be placed on this map.')
        if (options.x is None) != (options.y is None):
            raise HTTPException(422, 'Supply both marker coordinates.')
        x, y = options.x, options.y
        if options.room_id is not None:
            room = self.rooms[options.room_id]
            bounds = room['bounds']
            if x is None:
                x, y = room['anchor']
            if not (bounds['x'] <= x <= bounds['x']+bounds['width'] and
                    bounds['y'] <= y <= bounds['y']+bounds['height']) or (room.get('points') and not contains(room['points'], x, y)):
                raise HTTPException(422, 'Place the light inside the selected room.')
        elif x is not None:
            raise HTTPException(422, 'Choose a room for the marker.')
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
                               (settings.url, self.layout, entity_id))
                else:
                    db.execute('''INSERT INTO map_light_placements(origin,layout,entity_id,room_id,name,x,y) VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT(origin,layout,entity_id) DO UPDATE SET room_id=excluded.room_id,name=excluded.name,x=excluded.x,y=excluded.y''',
                               (settings.url, self.layout, entity_id, options.room_id, row['name'], x, y))
        return dict(entity_id=entity_id, room_id=options.room_id, x=x, y=y)

    def action(self, entity_id, options):
        if not entity_id.startswith('light.') or options.action not in ('on', 'off', 'brightness', 'color_temperature', 'color'):
            raise HTTPException(422, 'Choose a supported light control.')
        with self.ha.lock:
            if options.version != self.version():
                raise HTTPException(409, 'Device settings changed. Refresh the map before controlling lights.')
            revision = self.ha.revision
        try:
            result = self.ha.device_action(entity_id, options, expected_revision=revision)
            return {key: result[key] for key in ('status', 'text', 'state') if key in result}
        finally:
            with self.lock:
                self.cached = None

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
                        'SELECT entity_id,room_id,name,x,y FROM map_light_placements WHERE origin=? AND layout=?',
                        (settings.url, self.layout))}
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
                    brightness_known = type(brightness) in (int, float) and math.isfinite(brightness) and 0 <= brightness <= 255
                    if not brightness_known:
                        brightness = 255
                    devices.append(dict(entity_id=key, name=row['name'] if row else placement['name'],
                                        room_id=placement['room_id'] if placement else None,
                                        x=placement['x'] if placement else None, y=placement['y'] if placement else None,
                                        state=status, brightness=round(brightness/255, 3) if status == 'on' else 0,
                                        brightness_known=brightness_known,
                                        controls=[{k: v for k, v in c.items() if k in ('key', 'label', 'kind', 'min', 'max', 'step')}
                                                  for c in controls(key, live.get('attributes', {}))] if status != 'unavailable' else [],
                                        assignable=assignable))
                rooms = []
                for room_id, room in self.rooms.items():
                    lights = [d for d in devices if d['room_id'] == room_id]
                    on = sum(d['state'] == 'on' for d in lights)
                    unknown = sum(d['state'] == 'unavailable' for d in lights)
                    rooms.append(dict(**room, assigned_count=len(lights), on_count=on,
                                      unknown_count=unknown, state='on' if on else 'unknown' if unknown else 'off' if lights else 'unassigned',
                                      brightness=max((d['brightness'] for d in lights), default=0)))
                return dict(layout=self.layout, name=self.name, aspect_ratio=self.aspect_ratio, viewport=self.viewport,
                            background_url='/api/map/background' if self.image else None, layout_error=self.layout_error,
                            version=version, enabled=settings.enabled,
                            checked_at=self.cached['checked_at'], error=self.cached['error'], rooms=rooms,
                            devices=sorted(devices, key=lambda d: (d['name'].casefold(), d['entity_id'])))


def add_map_light_routes(router, service):
    @router.get('/api/map/background')
    def background():
        if service.image is None:
            raise HTTPException(404, 'No private map image is configured.')
        return FileResponse(service.image, media_type='image/png', headers={'Cache-Control': 'no-store'})

    @router.get('/api/map/lights')
    def snapshot():
        return service.snapshot()

    @router.put('/api/map/lights/{entity_id}')
    def place(entity_id: str, options: LightPlacement):
        return service.place(entity_id, options)

    @router.post('/api/map/lights/{entity_id}/actions')
    def action(entity_id: str, options: LightAction):
        return service.action(entity_id, options)
