"""Private, bounded live PCM relay. Call audio never enters STT or the archive."""
from .command_reply import command_reply
import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
import re
import threading
import time
import uuid

import numpy as np
from fastapi import HTTPException, WebSocket
from pydantic import BaseModel, Field


FRAME_BYTES = 640  # 20 ms, mono signed little-endian 16-bit PCM at 16 kHz


def intent(text):
    text = re.sub(r'[^a-z0-9 ]', ' ', text.lower())
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^(?:hey |ok |okay )?adam\s+', '', text)
    match = re.fullmatch(r'(?:call|drop in (?:on|to)|connect (?:me )?to) (.+?)(?: please)?', text)
    return match[1] if match else None


def normalized(text):
    return ' '.join(re.findall(r'[a-z0-9]+', text.lower()))


@dataclass
class Call:
    id: str
    caller: str
    callee: str
    created: float
    connected: set = field(default_factory=set)
    queues: dict = field(default_factory=dict)
    last_rx: dict = field(default_factory=dict)
    budget: dict = field(default_factory=dict)
    reason: str = ''
    active_at: float = 0
    last_voice: float = 0

    @property
    def units(self):
        return (self.caller, self.callee)


class Intercom:
    def __init__(self, state, deliver, clock=time.monotonic):
        self.state, self.deliver, self.clock = state, deliver, clock
        self.lock = threading.RLock()
        self.calls = {}
        self.capabilities = {}
        with state.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS intercom_settings (unit_id TEXT PRIMARY KEY, allow_drop_in INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS intercom_requests (unit_id TEXT, session_id TEXT, created REAL, PRIMARY KEY(unit_id,session_id));
            ''')

    def settings(self):
        with self.state.db() as db:
            return {r['unit_id']: bool(r['allow_drop_in']) for r in db.execute('SELECT * FROM intercom_settings')}

    def configure(self, unit_id, allow):
        if unit_id not in {u['id'] for u in self.state.units()}:
            raise HTTPException(404, 'Speaker not found')
        with self.lock:
            with self.state.db() as db:
                db.execute('INSERT INTO intercom_settings VALUES (?,?) ON CONFLICT(unit_id) DO UPDATE SET allow_drop_in=excluded.allow_drop_in', (unit_id, int(allow)))
            if not allow:
                for call in self.calls.values():
                    if unit_id in call.units:
                        self._end(call, 'Drop-in disabled')

    def heartbeat(self, unit_id, capable, busy):
        with self.lock:
            self.capabilities[unit_id] = (capable, busy, self.clock())
            self._sweep()

    def _end(self, call, reason):
        if not call.reason:
            call.reason = reason
            call.queues.clear()

    def _sweep(self):
        now = self.clock()
        for call in self.calls.values():
            if call.reason:
                continue
            if not call.active_at and now-call.created > 15:
                self._end(call, 'Connection timed out')
            elif now-call.created > 600:
                self._end(call, 'Ten-minute call limit reached')
            elif call.active_at and now-call.last_voice > 90:
                self._end(call, 'Call ended after 90 seconds of silence')
            elif any(now-t > 3 for t in call.last_rx.values()):
                self._end(call, 'Speaker disconnected')
        ended = [c.id for c in self.calls.values() if c.reason]
        for cid in ended[:-30]:
            del self.calls[cid]

    def current(self, unit_id):
        with self.lock:
            self._sweep()
            return next((c for c in self.calls.values() if not c.reason and unit_id in c.units), None)

    def start(self, caller, callee, voice=False):
        with self.lock:
            self._sweep()
            units = {u['id']: u for u in self.state.units()}
            if caller == callee:
                raise HTTPException(409, 'Choose a different speaker')
            if caller not in units or callee not in units:
                raise HTTPException(404, 'Speaker not found')
            if not self.settings().get(callee, False):
                raise HTTPException(409, 'Allow drop-in on the destination speaker first')
            for uid in (caller, callee):
                if not units[uid]['online']:
                    raise HTTPException(409, f"{units[uid]['name']} is offline")
                capable, busy, seen = self.capabilities.get(uid, (False, False, 0))
                if not capable or self.clock()-seen > 30:
                    raise HTTPException(409, f"{units[uid]['name']} needs intercom firmware")
                if self.current(uid) or (busy and not (voice and uid == caller)):
                    raise HTTPException(409, f"{units[uid]['name']} is busy")
            if sum(not c.reason for c in self.calls.values()) >= 4:
                raise HTTPException(409, 'All four call slots are in use')
            now = self.clock()
            call = Call(uuid.uuid4().hex, caller, callee, now, last_voice=now)
            for uid in call.units:
                call.queues[uid] = deque(maxlen=10)
            self.calls[call.id] = call
            return self.describe(call)

    def describe(self, call):
        return dict(id=call.id, caller=call.caller, callee=call.callee,
                    state='ended' if call.reason else 'active' if call.active_at else 'connecting',
                    reason=call.reason)

    def public(self):
        with self.lock:
            self._sweep()
            settings = self.settings()
            return dict(units=[dict(id=u['id'], name=u['name'], online=u['online'],
                                   capable=self.capabilities.get(u['id'], (False,))[0],
                                   allow_drop_in=settings.get(u['id'], False)) for u in self.state.units()],
                        calls=[self.describe(c) for c in self.calls.values()][-30:])

    def offer(self, unit_id):
        with self.lock:
            call = self.current(unit_id)
            if not call:
                return None
            other = call.callee if unit_id == call.caller else call.caller
            name = next((u['name'] for u in self.state.units() if u['id'] == other), 'Speaker')
            return dict(type='call', id=call.id, peer_name=name[:24])

    def end(self, call_id, unit_id=None):
        with self.lock:
            call = self.calls.get(call_id)
            if not call or (unit_id and unit_id not in call.units):
                raise HTTPException(404, 'Call not found')
            self._end(call, 'Call ended')

    def close(self):
        with self.lock:
            for call in self.calls.values():
                self._end(call, 'Head-end stopping')

    def submit(self, entry, job):
        name = intent(job['text'])
        if name is None:
            return False
        if not entry.get('unit_id'):
            entry['reply'] = dict(status='unsupported', text='Choose a speaker to call from. Calls connect two speakers, not your phone microphone.')
            return True
        with self.state.db() as db:
            if db.execute('SELECT 1 FROM intercom_requests WHERE unit_id=? AND session_id=?', (entry['unit_id'], entry['id'])).fetchone():
                return True
            db.execute('INSERT INTO intercom_requests VALUES (?,?,?)', (entry['unit_id'], entry['id'], time.time()))
            db.execute('DELETE FROM intercom_requests WHERE created<?', (time.time()-86400*7,))
        matches = [u for u in self.state.units() if normalized(u['name']) == name]
        try:
            if len(matches) != 1:
                raise HTTPException(409, 'Please use the exact name of one speaker')
            call = self.start(entry['unit_id'], matches[0]['id'], voice=entry.get('trigger') in ('wake', 'followup'))
            entry['reply'] = dict(status='done', text='Connecting intercom.', call_id=call['id'])
        except HTTPException as exc:
            entry['reply'] = command_reply(entry, dict(status='done', text=str(exc.detail)), self.deliver)
        return True

    def receive(self, call, unit_id, pcm):
        """Relay both directions independently; echo cancellation runs on-device."""
        with self.lock:
            if call.reason:
                return
            now = self.clock()
            if len(pcm) != FRAME_BYTES:
                self._end(call, 'Invalid audio frame')
                return
            credits, updated = call.budget.get(unit_id, (10., now))
            credits = min(10., credits+(now-updated)*55)-1
            call.budget[unit_id] = (credits, now)
            if credits < 0:
                self._end(call, 'Audio arrived too quickly')
                return
            call.last_rx[unit_id] = now
            if not call.active_at:
                return
            samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
            if float(np.mean(samples*samples)) > 450**2:
                call.last_voice = now
            other = call.callee if unit_id == call.caller else call.caller
            call.queues[other].append((now, pcm))

    async def socket(self, ws: WebSocket, call_id, unit_id):
        # No browser-cookie authentication, query tokens, or cross-origin clients.
        authorization = ws.headers.get('authorization', '')
        token = authorization[7:] if authorization.startswith('Bearer ') else ''
        if ws.headers.get('origin') or not token or not self.state.unit_auth(unit_id, token):
            await ws.close(code=1008)
            return
        with self.lock:
            self._sweep()
            call = self.calls.get(call_id)
            permitted = call and not call.reason and unit_id in call.units and unit_id not in call.connected
            if permitted:
                call.connected.add(unit_id)
                call.last_rx[unit_id] = self.clock()
                if len(call.connected) == 2:
                    call.active_at = call.last_voice = self.clock()
        if not permitted:
            await ws.close(code=1008)
            return
        async def send():
            previous, last_status = None, 0
            while True:
                with self.lock:
                    self._sweep()
                    status = self.describe(call)
                    packet = None
                    while not call.reason and call.queues[unit_id]:
                        stamp, candidate = call.queues[unit_id].popleft()
                        if self.clock()-stamp < .2:
                            packet = candidate
                            break
                if status != previous or self.clock()-last_status >= 1:
                    await asyncio.wait_for(ws.send_json(status), 1)
                    previous, last_status = status, self.clock()
                if call.reason:
                    return
                if packet:
                    await asyncio.wait_for(ws.send_bytes(packet), .5)
                else:
                    await asyncio.sleep(.01)

        async def receive():
            while not call.reason:
                message = await asyncio.wait_for(ws.receive(), 3)
                if message['type'] == 'websocket.disconnect':
                    return
                if message.get('text') is not None:
                    try:
                        command = json.loads(message['text'])
                    except (ValueError, TypeError):
                        return
                    if command not in ({'type':'end'}, {'type':'end','reason':'audio_overload'}):
                        return
                    with self.lock:
                        self._end(call, 'Audio overload detected; call stopped' if command.get('reason') else 'Call ended')
                    return
                if 'bytes' not in message or message['bytes'] is None:
                    return
                self.receive(call, unit_id, message['bytes'])

        tasks = []
        try:
            await ws.accept()
            tasks = [asyncio.create_task(send()), asyncio.create_task(receive())]
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            with self.lock:
                self._end(call, 'Speaker disconnected')
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await asyncio.wait_for(ws.close(), .5)
            except (RuntimeError, OSError, asyncio.TimeoutError):
                pass


class CallOptions(BaseModel):
    caller: str = Field(min_length=16, max_length=16, pattern=r'^[a-f0-9]{16}$')
    callee: str = Field(min_length=16, max_length=16, pattern=r'^[a-f0-9]{16}$')


class DropInOptions(BaseModel):
    allow_drop_in: bool


def add_intercom_routes(router, intercom):
    @router.get('/api/intercom')
    def status():
        return intercom.public()

    @router.put('/api/intercom/units/{unit_id}')
    def settings(unit_id: str, options: DropInOptions):
        intercom.configure(unit_id, options.allow_drop_in)
        return {'saved': True}

    @router.post('/api/intercom/calls', status_code=201)
    def start(options: CallOptions):
        return intercom.start(options.caller, options.callee)

    @router.post('/api/intercom/calls/{call_id}/end')
    def end(call_id: str):
        intercom.end(call_id)
        return {'ended': True}

    @router.websocket('/api/units/{unit_id}/calls/{call_id}/stream')
    async def stream(ws: WebSocket, unit_id: str, call_id: str):
        await intercom.socket(ws, call_id, unit_id)
