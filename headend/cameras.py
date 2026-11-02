"""Camera registry and bounded gesture events; video processing lives in a sidecar."""
import asyncio
import base64
import binascii
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from typing import Literal

from .home_assistant import DeviceAction

Gesture = Literal['None', 'Closed_Fist', 'Open_Palm', 'Pointing_Up', 'Thumb_Down', 'Thumb_Up', 'Victory', 'ILoveYou']
Status = Literal['connecting', 'streaming', 'paused', 'offline', 'model_error', 'stream_error']
SNAPSHOT_MAX_BYTES = 200000
SNAPSHOT_DAYS = 7
SNAPSHOT_RESERVE_BYTES = 256*1024*1024


class GestureRule(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    gesture: Gesture = 'Open_Palm'
    action_enabled: bool = False
    entity_id: str = Field(default='', max_length=150, pattern=r'^(?:[a-z_]+\.[a-z0-9_]+)?$')
    action: str = Field(default='on', min_length=1, max_length=40)
    value: float | str | list[float] | None = None


class CameraOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=64)
    rtsp_url: SecretStr = SecretStr('')
    enabled: bool = False
    confidence: float = Field(default=.75, ge=.5, le=.99, allow_inf_nan=False)
    hold_seconds: float = Field(default=1, ge=.5, le=5, allow_inf_nan=False)
    cooldown_seconds: int = Field(default=5, ge=3, le=120)
    fps: int = Field(default=3, ge=1, le=5)
    snapshot_events: bool = False
    rules: list[GestureRule] = Field(default_factory=lambda:[GestureRule()], max_length=7)


class Observation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(pattern=r'^[a-f0-9]{32}$')
    sequence: int = Field(ge=0)
    status: Status
    gesture: Gesture = 'None'
    confidence: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    age_ms: float = Field(default=0, ge=0, le=60000, allow_inf_nan=False)
    inference_ms: float = Field(default=0, ge=0, le=60000, allow_inf_nan=False)
    width: int = Field(default=0, ge=0, le=8192)
    height: int = Field(default=0, ge=0, le=8192)
    captured_unix: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    snapshot_jpeg: str | None = Field(default=None, max_length=266668, repr=False)


def valid_stream(value):
    """LAN IPv4 literals only: avoid DNS rebinding and arbitrary URL protocols."""
    try:
        if len(value) > 2048 or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
            raise ValueError()
        parsed = urlsplit(value)
        address = ipaddress.IPv4Address(parsed.hostname)
        networks = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
        if (parsed.scheme != 'rtsp' or parsed.fragment or
                not any(address in ipaddress.ip_network(n) for n in networks) or
                not 1 <= (parsed.port or 554) <= 65535):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError('Use an RTSP URL with a private LAN IPv4 address and valid port.') from None
    return value


class GestureGate:
    """Require a fresh sustained gesture; release for a second before rearming."""
    def __init__(self):
        self.since = self.last = self.absent = None
        self.armed = False  # A hand already present at startup must be removed first.
        self.fired = -1e20

    def update(self, matching, now, hold, cooldown):
        if self.last is not None and now - self.last > 1.25:
            self.since = self.absent = None
            self.armed = False
        self.last = now
        if not matching:
            self.since = None
            if self.absent is None:
                self.absent = now
            if now - self.absent >= 1:
                self.armed = True
            return False
        self.absent = None
        if self.since is None:
            self.since = now
        if self.armed and now-self.since >= hold and now-self.fired >= cooldown:
            self.armed = False
            self.fired = now
            return True
        return False


class CameraService:
    def __init__(self, state, home_assistant, voice_busy=lambda:False, clock=time.monotonic):
        self.state, self.ha, self.voice_busy, self.clock = state, home_assistant, voice_busy, clock
        self.lock = threading.RLock()
        self.runtime, self.previews, self.gates, self.probes = {}, {}, {}, {}
        self.worker_seen = None
        self.next_cleanup = 0
        token_path = state.directory/'camera-worker.token'
        try:
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, 'w') as out:
                out.write(secrets.token_hex(32))
        self.worker_token = token_path.read_text().strip()
        with state.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cameras(id TEXT PRIMARY KEY, revision TEXT NOT NULL, config TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS camera_events(id TEXT PRIMARY KEY, camera_id TEXT NOT NULL,
                    created REAL NOT NULL, gesture TEXT NOT NULL, outcome TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS camera_event_debug(event_id TEXT PRIMARY KEY,
                    metadata TEXT NOT NULL, snapshot_status TEXT NOT NULL, image BLOB);
            ''')
            self.prune_debug(db)

    def rows(self):
        with self.state.db() as db:
            return [dict({'snapshot_events':False}, id=r['id'], revision=r['revision'], **json.loads(r['config'])) for r in db.execute('SELECT * FROM cameras ORDER BY rowid')]

    @staticmethod
    def prune_debug(db):
        db.execute('DELETE FROM camera_event_debug WHERE event_id NOT IN (SELECT id FROM camera_events)')
        db.execute('''UPDATE camera_event_debug SET image=NULL,snapshot_status='expired'
            WHERE image IS NOT NULL AND event_id IN (SELECT id FROM camera_events WHERE created<?)''',
            (time.time()-SNAPSHOT_DAYS*86400,))

    def snapshot(self, event_id, delete=False):
        with self.lock, self.state.db() as db:
            self.prune_debug(db)
            row = db.execute('SELECT image FROM camera_event_debug WHERE event_id=?', (event_id,)).fetchone()
            if row is not None and row['image'] is not None and delete:
                db.execute("UPDATE camera_event_debug SET image=NULL,snapshot_status='deleted' WHERE event_id=?", (event_id,))
        if row is None or row['image'] is None:
            raise HTTPException(404, 'No saved snapshot for this event')
        return bytes(row['image'])

    def save_snapshot(self, event_id, encoded):
        # A snapshot failure must not prevent or replay the Home Assistant action.
        status, raw = 'missing', None
        if encoded:
            try:
                raw = base64.b64decode(encoded, validate=True)
                if len(raw) > SNAPSHOT_MAX_BYTES or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
                    raise ValueError()
                status = 'saved'
            except (ValueError, binascii.Error):
                status, raw = 'invalid', None
        try:
            if raw and shutil.disk_usage(self.state.directory).free < SNAPSHOT_RESERVE_BYTES+len(raw):
                status, raw = 'low_disk', None
            with self.state.db() as db:
                db.execute('UPDATE camera_event_debug SET snapshot_status=?,image=? WHERE event_id=?', (status, raw, event_id))
        except (OSError, sqlite3.Error):
            try:
                with self.state.db() as db:
                    db.execute("UPDATE camera_event_debug SET snapshot_status='storage_error',image=NULL WHERE event_id=?", (event_id,))
            except sqlite3.Error:
                pass

    def get(self, camera_id):
        row = next((r for r in self.rows() if r['id'] == camera_id), None)
        if row is None:
            raise HTTPException(404, 'Camera not found')
        return row

    def public(self):
        now = self.clock()
        with self.lock:
            result = []
            for row in self.rows():
                url = urlsplit(row.pop('rtsp_url'))
                status = dict(self.runtime.get(row['id'], {}))
                seen = status.pop('seen', None)
                if seen is None or now-seen > 10:
                    status = dict(status='offline')
                status.pop('sequence', None)
                row.update(endpoint=f'{url.hostname}:{url.port or 554}', has_stream=True,
                           runtime=status, has_preview=row['id'] in self.previews and now-self.previews[row['id']][0] < 10)
                result.append(row)
            with self.state.db() as db:
                self.prune_debug(db)
                events = []
                for r in db.execute('''SELECT e.*,d.metadata,d.snapshot_status,length(d.image) AS snapshot_bytes
                        FROM camera_events e LEFT JOIN camera_event_debug d ON e.id=d.event_id
                        ORDER BY e.created DESC LIMIT 30'''):
                    event = dict(r)
                    event['metadata'] = json.loads(event['metadata']) if event['metadata'] else None
                    event['has_snapshot'] = bool(event['snapshot_bytes'])
                    events.append(event)
            return dict(cameras=result, events=events, worker_online=self.worker_seen is not None and now-self.worker_seen < 10,
                        voice_busy=bool(self.voice_busy()), max_active=1)

    def save(self, options, camera_id=None):
        with self.lock:
            rows = self.rows()
            old = self.get(camera_id) if camera_id else None
            if not old and len(rows) >= 8:
                raise ValueError('Up to eight cameras can be saved.')
            if options.enabled and any(r['enabled'] and r['id'] != camera_id for r in rows):
                raise ValueError('Pause the active camera first. This Pi pilot runs one camera at a time.')
            raw = options.rtsp_url.get_secret_value().strip()
            raw = valid_stream(raw or (old['rtsp_url'] if old else ''))
            if not options.name.strip() or any(ord(c) < 32 for c in options.name):
                raise ValueError('Enter a readable camera name.')
            gestures = [r.gesture for r in options.rules]
            if 'None' in gestures or len(set(gestures)) != len(gestures):
                raise ValueError('Choose a different gesture for each rule.')
            devices = self.ha.inventory_view()['devices'] if any(r.action_enabled for r in options.rules) else []
            for rule in options.rules:
                if rule.action_enabled:
                    device = next((r for r in devices if r['entity_id'] == rule.entity_id and r['imported'] and r['enabled'] and r['present']), None)
                    if device is None:
                        raise ValueError('Choose an imported, enabled device.')
                    self.ha._prevalidate_device_step(device, rule.action, rule.value)
            camera_id = camera_id or uuid.uuid4().hex[:16]
            revision = uuid.uuid4().hex
            data = dict(options.model_dump(exclude={'rtsp_url'}), name=options.name.strip(), rtsp_url=raw)
            with self.state.db() as db:
                db.execute('INSERT OR REPLACE INTO cameras VALUES(?,?,?)', (camera_id, revision, json.dumps(data)))
            self.runtime.pop(camera_id, None)
            self.previews.pop(camera_id, None)
            self.gates.pop(camera_id, None)
            return dict(id=camera_id)

    def delete(self, camera_id):
        with self.lock:
            self.get(camera_id)
            with self.state.db() as db:
                db.execute('DELETE FROM cameras WHERE id=?', (camera_id,))
                db.execute('DELETE FROM camera_events WHERE camera_id=?', (camera_id,))
                self.prune_debug(db)
            for collection in (self.runtime, self.previews, self.gates, self.probes):
                collection.pop(camera_id, None)

    def worker_auth(self, request):
        peer = request.client.host if request.client else ''
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        if peer not in ('127.0.0.1', '::1', 'testclient') or not hmac.compare_digest(token, self.worker_token):
            raise HTTPException(401, 'Camera worker authentication required')

    def worker_config(self):
        with self.lock:
            self.worker_seen = self.clock()
            if self.worker_seen >= self.next_cleanup:
                with self.state.db() as db:
                    self.prune_debug(db)
                self.next_cleanup = self.worker_seen+60
            busy = bool(self.voice_busy())
            if busy:
                self.gates.clear()
                self.previews.clear()
            selected = next((r for r in self.rows() if r['enabled']), None)
            # Tests use the same bounded worker, without arming actions.
            probe = next((key for key, expiry in self.probes.items() if expiry > self.clock()), None)
            if probe and selected is None:
                selected = self.get(probe)
            return dict(camera=selected, paused=busy)

    def observe(self, camera_id, observation):
        with self.lock:
            row = self.get(camera_id)
            if observation.revision != row['revision']:
                raise HTTPException(409, 'Camera settings changed')
            old = self.runtime.get(camera_id, {})
            # Worker uses time.time_ns(); retries and stale observations cannot replay events.
            if observation.sequence <= old.get('sequence', -1):
                return dict(accepted=False)
            now = self.clock()
            busy = bool(self.voice_busy())
            valid = observation.status == 'streaming' and observation.age_ms <= 1000 and not busy
            public = observation.model_dump(exclude={'revision', 'age_ms', 'snapshot_jpeg', 'captured_unix'})
            public.update(seen=now, status='paused' if busy else observation.status)
            self.runtime[camera_id] = public
            if not valid:
                self.gates.pop(camera_id, None)
                self.previews.pop(camera_id, None)
                return dict(accepted=True)
            if not row['enabled']:
                return dict(accepted=True)
            gates = self.gates.setdefault(camera_id, {})
            selected = None
            for rule in row['rules']:
                gate = gates.setdefault(rule['gesture'], GestureGate())
                matching = observation.gesture == rule['gesture'] and observation.confidence >= row['confidence']
                if gate.update(matching, now, row['hold_seconds'], row['cooldown_seconds']):
                    selected = rule
            if selected is None:
                return dict(accepted=True)
            event_id = uuid.uuid4().hex
            outcome = 'pending' if selected['action_enabled'] else 'detected'
            metadata = dict(camera_name=row['name'], revision=row['revision'], sequence=str(observation.sequence),
                captured_unix=observation.captured_unix, confidence=observation.confidence,
                confidence_threshold=row['confidence'], hold_seconds=row['hold_seconds'],
                cooldown_seconds=row['cooldown_seconds'], fps=row['fps'], frame_age_ms=observation.age_ms,
                inference_ms=observation.inference_ms, source_width=observation.width, source_height=observation.height,
                rule=selected, model='MediaPipe gesture_recognizer float16 v1')
            with self.state.db() as db:
                db.execute('INSERT INTO camera_events VALUES(?,?,?,?,?)', (event_id, camera_id, time.time(), selected['gesture'], outcome))
                db.execute('INSERT INTO camera_event_debug VALUES(?,?,?,NULL)',
                    (event_id, json.dumps(metadata), 'missing' if row['snapshot_events'] else 'disabled'))
                db.execute('DELETE FROM camera_events WHERE id NOT IN (SELECT id FROM camera_events ORDER BY created DESC LIMIT 200)')
                self.prune_debug(db)
            if row['snapshot_events']:
                self.save_snapshot(event_id, observation.snapshot_jpeg)
            # Serialize configuration changes against dispatch. No retries, even on timeout.
            if selected['action_enabled']:
                try:
                    result = self.ha.device_action(selected['entity_id'], DeviceAction(action=selected['action'], value=selected['value']))
                    outcome = result.get('status') if result.get('status') in ('answered', 'sent', 'unconfirmed', 'ignored', 'unavailable') else 'failed'
                except Exception:
                    outcome = 'failed'
                with self.state.db() as db:
                    db.execute('UPDATE camera_events SET outcome=? WHERE id=?', (outcome, event_id))
            return dict(accepted=True, event_id=event_id)


async def bounded_body(request, limit):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, 'Camera request is too large')
    return bytes(raw)


def add_camera_routes(app, service):
    app.state.cameras = service

    @app.get('/api/cameras')
    def cameras():
        return service.public()

    @app.get('/api/cameras/events/{event_id}/snapshot')
    def event_snapshot(event_id: str):
        return Response(service.snapshot(event_id), media_type='image/jpeg')

    @app.delete('/api/cameras/events/{event_id}/snapshot')
    def delete_snapshot(event_id: str):
        service.snapshot(event_id, delete=True)
        return dict(deleted=True)

    @app.get('/api/cameras/events/{event_id}/metadata')
    def event_metadata(event_id: str):
        with service.lock, service.state.db() as db:
            service.prune_debug(db)
            row = db.execute('''SELECT e.*,d.metadata,d.snapshot_status FROM camera_events e
                LEFT JOIN camera_event_debug d ON e.id=d.event_id WHERE e.id=?''', (event_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Camera event not found')
        event = dict(row)
        event['metadata'] = json.loads(event['metadata']) if event['metadata'] else None
        return event

    async def parse_options(request):
        try:
            return CameraOptions.model_validate_json(await bounded_body(request, 8192))
        except ValueError:
            raise HTTPException(400, 'Check camera name, RTSP address, gesture settings, and action.') from None

    @app.post('/api/cameras', status_code=201)
    async def create_camera(request: Request):
        return await asyncio.to_thread(service.save, await parse_options(request))

    @app.put('/api/cameras/{camera_id}')
    async def update_camera(camera_id: str, request: Request):
        return await asyncio.to_thread(service.save, await parse_options(request), camera_id)

    @app.delete('/api/cameras/{camera_id}')
    def delete_camera(camera_id: str):
        service.delete(camera_id)
        return dict(deleted=True)

    @app.post('/api/cameras/{camera_id}/test')
    def test_camera(camera_id: str):
        with service.lock:
            service.get(camera_id)
            if any(r['enabled'] and r['id'] != camera_id for r in service.rows()):
                raise HTTPException(409, 'Pause the active camera before testing another.')
            service.probes[camera_id] = service.clock()+20
        return dict(queued=True)

    @app.get('/api/cameras/{camera_id}/preview')
    def preview(camera_id: str):
        with service.lock:
            service.get(camera_id)
            frame = service.previews.get(camera_id)
            if not frame or service.clock()-frame[0] > 10:
                raise HTTPException(404, 'No recent preview. Start detection or test the connection.')
            return Response(frame[1], media_type='image/jpeg')

    @app.get('/api/camera-worker/config')
    def worker_config(request: Request):
        service.worker_auth(request)
        return service.worker_config()

    @app.post('/api/camera-worker/{camera_id}/observation')
    async def observation(camera_id: str, request: Request):
        service.worker_auth(request)
        try:
            options = Observation.model_validate_json(await bounded_body(request, 280000))
        except ValueError:
            raise HTTPException(400, 'Invalid camera observation') from None
        return await asyncio.to_thread(service.observe, camera_id, options)

    @app.put('/api/camera-worker/{camera_id}/preview')
    async def put_preview(camera_id: str, request: Request):
        service.worker_auth(request)
        raw = await bounded_body(request, 200000)
        if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
            raise HTTPException(400, 'JPEG required')
        def save_preview():
            with service.lock:
                row = service.get(camera_id)
                if request.headers.get('x-camera-revision') != row['revision'] or service.voice_busy():
                    raise HTTPException(409, 'Camera changed or paused')
                service.previews[camera_id] = (service.clock(), raw)
        await asyncio.to_thread(save_preview)
        return dict(saved=True)
