"""Authenticated typed commands with durable request deduplication."""
import hashlib
import json
import time

from fastapi import HTTPException
from pydantic import BaseModel, Field


class TextCommand(BaseModel):
    request_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    text: str = Field(min_length=1, max_length=500)
    unit_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{16}$')
    speak_reply: bool = False


class TextCommands:
    def __init__(self, fleet):
        self.fleet = fleet
        self.entries = {}
        with fleet.state.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS text_commands '
                       '(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, created REAL NOT NULL, result TEXT)')
        fleet.router.add_api_route('/api/commands/text', self.submit, methods=['POST'], status_code=202)
        fleet.router.add_api_route('/api/commands/text/{request_id}', self.get, methods=['GET'])

    def snapshot(self, entry):
        reply = dict(entry.get('reply') or {})
        result = dict(id=entry['id'], text=entry['text'], unit_id=entry['unit_id'],
                      started=entry['started'], reply=reply)
        if reply.get('status') != 'working':
            with self.fleet.state.db() as db:
                db.execute('UPDATE text_commands SET result=? WHERE id=?', (json.dumps(result), entry['id']))
        return result

    async def get(self, request_id: str):
        if request_id in self.entries:
            return self.snapshot(self.entries[request_id])
        with self.fleet.state.db() as db:
            row = db.execute('SELECT result FROM text_commands WHERE id=?', (request_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Command not found')
        if row['result']:
            return json.loads(row['result'])
        return dict(id=request_id, reply=dict(status='unknown', text=
                    'ADAM restarted before the result was saved. Check the device or timer state; this command will not be repeated.'))

    async def submit(self, options: TextCommand):
        text = options.text.strip()
        if not text:
            raise HTTPException(422, 'Enter a command')
        fingerprint = hashlib.sha256(json.dumps(options.model_dump(exclude={'request_id'}),
                                               sort_keys=True).encode()).hexdigest()
        with self.fleet.state.db() as db:
            existing = db.execute('SELECT fingerprint FROM text_commands WHERE id=?', (options.request_id,)).fetchone()
        if existing:
            if existing['fingerprint'] != fingerprint:
                raise HTTPException(409, 'This request ID belongs to a different command')
            return await self.get(options.request_id)
        units = {u['id']: u for u in self.fleet.state.units()}
        if options.unit_id and options.unit_id not in units:
            raise HTTPException(404, 'Speaker not found')
        if options.speak_reply and (not options.unit_id or not units[options.unit_id]['online']):
            raise HTTPException(409, 'Choose an online speaker for a spoken reply')
        working = [e for e in self.entries.values() if e.get('reply', {}).get('status') == 'working']
        if len(working) >= 16:
            raise HTTPException(429, 'Command queue is full; wait for a reply')
        # Retain request IDs: retrying an old request must never repeat an action.
        # Only hashes and fetched results persist; recordings/training are untouched.
        with self.fleet.state.db() as db:
            db.execute('INSERT INTO text_commands(id,fingerprint,created) VALUES(?,?,?)',
                       (options.request_id, fingerprint, time.time()))
        if len(self.entries) >= 100:
            for key, entry in list(self.entries.items()):
                if entry.get('reply', {}).get('status') != 'working':
                    self.snapshot(entry)
                    del self.entries[key]
                    break
        entry = dict(id=options.request_id, text=text, unit_id=options.unit_id,
                     trigger='text', speak_reply=options.speak_reply, started=time.time(),
                     reply=dict(status='ignored', text=''))
        self.entries[entry['id']] = entry
        try:
            self.fleet.dispatch_command(entry, dict(status='done', text=text))
        except Exception:
            entry['reply'] = dict(status='error', text='The command could not be completed. Check its result before sending a new command; it was not retried.')
        return self.snapshot(entry)
