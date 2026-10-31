"""Persistent Home Assistant inventory with local names and bounded controls."""
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from .ha_devices import (ENTITY_PATTERN, attributes_for_inventory, controls, capabilities,
                         normalized, action_payload)
from .ha_inventory import HomeAssistantInventory


class HomeAssistantSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True, strict=True)
    enabled: bool = False
    auto_import: bool = False
    url: str = Field(default='http://homeassistant.local:8123', max_length=250)
    token: SecretStr | None = None  # None preserves the saved token; empty clears it.
    entity_id: str = Field(default='', pattern=r'^(?:(?:switch|light)\.[a-z0-9_]+)?$', max_length=150)
    name: str = Field(default='Living Room', min_length=1, max_length=48, pattern=r'^[\w -]+$')

    @field_validator('url')
    @classmethod
    def origin(cls, value):
        value = value.strip().rstrip('/')
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path or parsed.query or parsed.fragment
                or re.search(r'[\s\\\x00-\x1f\x7f]', value)):
            raise ValueError('Use the Home Assistant HTTP(S) address without a path or credentials')
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError('Invalid Home Assistant port')
        return value

    @field_validator('token')
    @classmethod
    def credential(cls, value):
        if value is not None:
            token = value.get_secret_value()
            if len(token) > 4096 or (token and not re.fullmatch(r'[A-Za-z0-9._~-]+', token)):
                raise ValueError('Use a valid Home Assistant access token')
        return value

    @field_validator('name')
    @classmethod
    def spoken_name(cls, value):
        if not re.search(r'\w', value):
            raise ValueError('Enter a spoken name for the selected switch')
        return value.strip()


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=1, max_length=300)
    unit_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')


class DeviceSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(default='', max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=8)
    imported: bool = True
    enabled: bool = True
    use_default_name: bool = True

    @field_validator('name')
    @classmethod
    def valid_name(cls, value):
        if value and (not normalized(value) or any(ord(c) < 32 for c in value)):
            raise ValueError('Use a readable device name')
        return value.strip()

    @field_validator('aliases')
    @classmethod
    def valid_aliases(cls, values):
        if any(not normalized(v) or len(v) > 80 or any(ord(c) < 32 for c in v) for v in values):
            raise ValueError('Use up to eight readable aliases of at most 80 characters')
        return list(dict.fromkeys(v.strip() for v in values))


class DeviceAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: str = Field(min_length=1, max_length=40)
    value: float | str | list[float] | None = None


class HomeAssistantUnavailable(Exception):
    pass


def entity_capabilities(entity_id, attributes):
    return capabilities(entity_id, attributes if isinstance(attributes, dict) else {})


class HomeAssistantClient:
    """Bounded HTTP requests without redirects, proxy environment or action retries."""
    def __init__(self, transport=None):
        self.transport = transport

    def request(self, settings, method, path, body=None, limit=256000):
        if not settings.token or not settings.token.get_secret_value():
            raise HomeAssistantUnavailable('Save a Home Assistant access token first.')
        try:
            with httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False,
                              trust_env=False, transport=self.transport) as client:
                with client.stream(method, settings.url + path, json=body,
                                   headers={'Authorization': 'Bearer ' + settings.token.get_secret_value()}) as response:
                    if response.status_code in (401, 403):
                        raise HomeAssistantUnavailable('Home Assistant rejected the access token. Check its account and permissions.')
                    response.raise_for_status()
                    raw = bytearray()
                    deadline = time.monotonic() + 15
                    for chunk in response.iter_bytes(chunk_size=4096):
                        raw.extend(chunk)
                        if len(raw) > limit or time.monotonic() > deadline:
                            raise HomeAssistantUnavailable('Home Assistant response exceeded its limit.')
                    data = json.loads(raw)
                    if not isinstance(data, (dict, list)):
                        raise ValueError('Expected an object')
                    return data
        except (httpx.HTTPError, ValueError) as exc:
            # Remote bodies, URLs, and exception text must never expose credentials.
            message = ('Home Assistant could not confirm the result. Check the device state before trying again.'
                       if method == 'POST' else 'Could not connect to Home Assistant. Check its address and that it is running.')
            raise HomeAssistantUnavailable(message) from exc

    def check(self, settings):
        data = self.request(settings, 'GET', '/api/config')
        if not isinstance(data, dict) or not isinstance(data.get('version'), str):
            raise HomeAssistantUnavailable('The server did not return a Home Assistant configuration.')
        return dict(connected=True, version=data['version'][:80])

    def entities(self, settings, all_domains=False):
        data = self.request(settings, 'GET', '/api/states', limit=2_000_000)
        if not isinstance(data, list):
            raise HomeAssistantUnavailable('Home Assistant returned an invalid device list.')
        result = []
        for row in data:
            if (not isinstance(row, dict) or not isinstance(row.get('entity_id'), str)
                    or not re.fullmatch(ENTITY_PATTERN if all_domains else r'(?:switch|light)\.[a-z0-9_]+', row['entity_id'])):
                continue
            attributes = row.get('attributes') or {}
            name = attributes.get('friendly_name') if isinstance(attributes, dict) else None
            if not isinstance(name, str) or not name.strip():
                name = row['entity_id'].split('.')[1].replace('_', ' ').title()
            attributes = attributes_for_inventory(attributes if isinstance(attributes, dict) else {})
            result.append(dict(entity_id=row['entity_id'], name=str(name)[:100], state=str(row.get('state', 'unknown'))[:80],
                               capabilities=entity_capabilities(row['entity_id'], attributes), attributes=attributes,
                               controls=controls(row['entity_id'], attributes)))
        return sorted(result, key=lambda row: (row['name'].casefold(), row['entity_id']))

    def device_state(self, settings, entity_id):
        data = self.request(settings, 'GET', '/api/states/'+entity_id)
        if not isinstance(data, dict) or data.get('entity_id') != entity_id or not isinstance(data.get('state'), str):
            raise HomeAssistantUnavailable('Home Assistant returned an invalid device state.')
        data['attributes'] = attributes_for_inventory(data.get('attributes') or {})
        return data

    def control(self, settings, device, action, value=None, before_action=lambda: True):
        entity_id, name = device['entity_id'], device['name']
        state = self.device_state(settings, entity_id)
        current, attrs = state['state'], state['attributes']
        result = dict(source='Home Assistant', source_url=settings.url, entity_id=entity_id, state=current)
        if current == 'unavailable' or (current == 'unknown' and entity_id.split('.')[0] not in ('button', 'input_button', 'scene', 'script')):
            return dict(result, status='unavailable', text=f'{name} is unavailable in Home Assistant. No command was sent.')
        if action == 'status':
            detail = f'{name} is {current}.'
            if 'current_temperature' in attrs: detail += f" Current temperature is {attrs['current_temperature']}."
            return dict(result, status='answered', text=detail)
        attrs['_state'] = current
        if action == 'level':
            action = {'light': 'brightness', 'fan': 'speed', 'cover': 'position', 'climate': 'temperature',
                      'media_player': 'volume', 'number': 'value', 'input_number': 'value'}.get(entity_id.split('.')[0], 'level')
        control, body = action_payload(entity_id, attrs, action, value)
        expected = control.get('expected')
        if expected and current == expected:
            return dict(result, status='answered', text=f'{name} is already {current}.')
        if not before_action():
            return dict(result, status='ignored', text='The command expired or settings changed. No command was sent.')
        domain = entity_id.split('.')[0]
        self.request(settings, 'POST', f"/api/services/{domain}/{control['service']}", body)
        if not expected:
            try: current = self.device_state(settings, entity_id)['state']
            except HomeAssistantUnavailable: pass
            return dict(result, state=current, status='sent', text=f"Sent {control['label'].lower()} to {name}.")
        try:
            current = self.device_state(settings, entity_id)['state']
            for delay in (.2, .4, .8):
                if current == expected: break
                time.sleep(delay)
                current = self.device_state(settings, entity_id)['state']
        except HomeAssistantUnavailable:
            return dict(result, status='unconfirmed', text=f'I sent the command for {name}, but could not confirm its state.')
        if current != expected:
            return dict(result, state=current, status='unconfirmed', text=f'I sent the command for {name}. Home Assistant reports {current}.')
        return dict(result, state=current, status='answered', text=f'{name} is now {current}.')

class HomeAssistantService:
    def __init__(self, state, deliver, client=None):
        self.state, self.deliver = state, deliver
        self.client = client or HomeAssistantClient()
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='home-assistant')
        self.slots = threading.BoundedSemaphore(4)
        self.closed = False
        self.revision = 0
        self.sync_lock = threading.Lock()
        self.refresh_error = ''
        self.last_refresh = 0
        self.stop_refresh = threading.Event()
        with state.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS home_assistant_settings
                    (id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, url TEXT NOT NULL,
                     token TEXT NOT NULL, entity_id TEXT NOT NULL, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS home_assistant_commands
                    (unit_id TEXT NOT NULL, session_id TEXT NOT NULL, created REAL NOT NULL,
                     PRIMARY KEY(unit_id, session_id));
            ''')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(home_assistant_settings)')}
            if 'auto_import' not in columns:
                db.execute('ALTER TABLE home_assistant_settings ADD COLUMN auto_import INTEGER NOT NULL DEFAULT 0')
        self.inventory = HomeAssistantInventory(state)
        self.inventory.legacy(self.settings())
        self.refresh_thread = threading.Thread(target=self.refresh_loop, name='ha-inventory', daemon=True)
        self.refresh_thread.start()

    def refresh_loop(self):
        while not self.stop_refresh.wait(60):
            if self.settings().enabled:
                try: self.refresh_inventory()
                except (HomeAssistantUnavailable, ValueError): pass

    def refresh_inventory(self):
        with self.sync_lock:
            with self.lock:
                settings, revision = self.settings(), self.revision
            try:
                rows = self.client.entities(settings, all_domains=True)
                with self.lock:
                    if self.closed or revision != self.revision:
                        raise ValueError('Settings changed during refresh. Refresh again.')
                    before = [(r['entity_id'], r['spoken_names'], r['present']) for r in self.inventory.rows(settings)]
                    self.inventory.sync(settings, rows)
                    after = [(r['entity_id'], r['spoken_names'], r['present']) for r in self.inventory.rows(settings)]
                    if before != after: self.revision += 1
                    self.last_refresh, self.refresh_error = time.time(), ''
            except (HomeAssistantUnavailable, ValueError) as exc:
                self.refresh_error = str(exc)
                raise
        return self.inventory_view()

    def inventory_view(self):
        with self.lock:
            settings = self.settings()
            return dict(devices=self.inventory.rows(settings), last_refresh=self.last_refresh,
                        error=self.refresh_error, enabled=settings.enabled, auto_import=settings.auto_import)

    def save_device(self, entity_id, options):
        with self.lock:
            row = self.inventory.save(self.settings(), entity_id, options)
            self.revision += 1
            return row

    def device_action(self, entity_id, options):
        if not self.slots.acquire(blocking=False): raise HTTPException(429, 'Home Assistant command queue is full')
        try:
            with self.lock:
                settings, revision = self.settings(), self.revision
                row = next((r for r in self.inventory.rows(settings) if r['entity_id'] == entity_id), None)
            if self.closed or not settings.enabled: raise HTTPException(409, 'Enable Home Assistant control first')
            if not row or not row['imported'] or not row['enabled'] or not row['present']:
                raise HTTPException(409, 'Import and enable a present device before controlling it')
            try:
                return self.client.control(settings, row, options.action, options.value, lambda: self.valid(revision))
            except HomeAssistantUnavailable as exc:
                raise HTTPException(503, str(exc)) from None
        finally:
            self.slots.release()

    def settings(self):
        with self.state.db() as db:
            row = db.execute('SELECT * FROM home_assistant_settings WHERE id=1').fetchone()
        return HomeAssistantSettings(enabled=bool(row['enabled']), url=row['url'], token=row['token'],
                                     entity_id=row['entity_id'], name=row['name'], auto_import=bool(row['auto_import'])) if row else HomeAssistantSettings()

    def public_settings(self):
        options = self.settings()
        return dict(enabled=options.enabled, auto_import=options.auto_import, url=options.url, entity_id=options.entity_id, name=options.name,
                    has_token=bool(options.token and options.token.get_secret_value()))

    def save(self, options):
        with self.lock:
            current = self.settings()
            token = options.token if options.token is not None else current.token
            if current.url != options.url and options.token is None and current.token:
                raise ValueError('Enter a new token when changing the Home Assistant address')
            secret = token.get_secret_value() if token else ''
            if options.enabled and not secret:
                raise ValueError('Save an access token before enabling Home Assistant')
            with self.state.db() as db:
                db.execute('''INSERT OR REPLACE INTO home_assistant_settings
                    (id,enabled,url,token,entity_id,name,auto_import) VALUES(1,?,?,?,?,?,?)''',
                           (int(options.enabled), options.url, secret, options.entity_id, options.name.strip(), int(options.auto_import)))
            self.inventory.legacy(options)
            if current.url != options.url:
                self.last_refresh, self.refresh_error = 0, ''
            self.revision += 1
            return self.public_settings()

    def online(self, unit_id):
        return any(unit['id'] == unit_id and unit['online'] for unit in self.state.units())

    def seen(self, unit_id, session_id):
        with self.state.db() as db:
            return db.execute('SELECT 1 FROM home_assistant_commands WHERE unit_id=? AND session_id=?',
                              (unit_id, session_id)).fetchone() is not None

    def answer(self, text, settings, before_action=lambda: True):
        matches = self.inventory.matches(settings, text)
        if not matches:
            return dict(status='ignored', text='')
        if len(matches) != 1:
            return dict(status='ambiguous', text='That name matches more than one device. Give them distinct Adam names or aliases in the inventory. No command was sent.')
        row, (action, value) = matches[0]
        try:
            return self.client.control(settings, row, action, value, before_action)
        except ValueError as exc:
            return dict(status='unsupported', text=str(exc))
        except HomeAssistantUnavailable as exc:
            return dict(status='unavailable', text=str(exc), source='Home Assistant', source_url=settings.url)

    def speak(self, result, unit_id):
        if not unit_id or not result['text']:
            return result
        try:
            return self.deliver(result, unit_id)
        except Exception:
            # Preserve the action's result even if the reply voice fails or exceeds 15 seconds.
            return dict(result, delivery_error='The answer could not be played. Read it here; the command was not retried.')

    def execute(self, options):
        if not self.slots.acquire(blocking=False):
            raise HTTPException(429, 'Home Assistant command queue is full')
        try:
            with self.lock:
                settings, revision = self.settings(), self.revision
            if self.closed or not settings.enabled:
                raise HTTPException(409, 'Enable Home Assistant in saved settings first')
            if options.unit_id and not self.online(options.unit_id):
                raise HTTPException(409, 'Choose an online unit')
            return self.speak(self.answer(options.text, settings, lambda: self.valid(revision)), options.unit_id)
        finally:
            self.slots.release()

    def valid(self, revision, entry=None):
        with self.lock:
            return (not self.closed and self.settings().enabled and revision == self.revision
                    and (entry is None or (time.time() - entry['started'] <= 30 and self.online(entry['unit_id']))))

    def submit(self, entry, job):
        if self.closed or entry['trigger'] != 'wake' or job['status'] != 'done':
            return
        with self.lock:
            settings = self.settings()
            if not settings.enabled or not self.inventory.matches(settings, job['text']):
                entry['reply'] = dict(status='ignored', text='')
                return False
            if entry.get('home_assistant_submitted'):
                return True
            entry['home_assistant_submitted'] = True
            if not self.slots.acquire(blocking=False):
                entry['reply'] = dict(status='error', text='', error='Home Assistant command queue is full')
                return True
            entry['reply'] = dict(status='working', text='')
            try:
                self.pool.submit(self._run, entry, job['text'], self.revision)
            except RuntimeError:
                self.slots.release()
                entry['reply'] = dict(status='error', text='', error='Head-end is stopping')
            return True

    def _run(self, entry, text, revision):
        try:
            with self.lock:
                settings = self.settings()
                if (self.closed or not settings.enabled or revision != self.revision
                        or time.time() - entry['started'] > 30 or not self.online(entry['unit_id'])):
                    entry['reply'] = dict(status='ignored', text='', error='Command expired, unit is offline, or settings changed')
                    return
                with self.state.db() as db:
                    db.execute('DELETE FROM home_assistant_commands WHERE created<?', (time.time() - 30*86400,))
                    claimed = db.execute('INSERT OR IGNORE INTO home_assistant_commands VALUES(?,?,?)',
                                         (entry['unit_id'], entry['id'], time.time())).rowcount
                if not claimed:
                    entry['reply'] = dict(status='ignored', text='', error='This command session was already handled')
                    return
            result = self.answer(text, settings, lambda: self.valid(revision, entry))
            entry['reply'] = self.speak(result, entry['unit_id']) if not self.closed else result
        except Exception:
            entry['reply'] = dict(status='error', text='', error='Home Assistant command failed; it was not retried')
        finally:
            self.slots.release()

    def close(self):
        self.stop_refresh.set()
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.refresh_thread.join(timeout=17)


def add_home_assistant_routes(router, service):
    @router.get('/api/home-assistant/settings')
    def get_settings():
        return service.public_settings()

    @router.put('/api/home-assistant/settings')
    async def save_settings(request: Request):
        # FastAPI's default validation response includes input values. Never echo a token.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 8192:
                raise HTTPException(413, 'Home Assistant settings exceed their size limit')
        try:
            options = HomeAssistantSettings.model_validate_json(raw)
        except ValueError:
            raise HTTPException(400, 'Use a valid HTTP(S) server address without a path or credentials, and a valid access token') from None
        return service.save(options)

    @router.post('/api/home-assistant/check')
    def check_connection():
        try:
            return service.client.check(service.settings())
        except HomeAssistantUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get('/api/home-assistant/entities')
    def get_entities():
        try:
            return service.client.entities(service.settings())
        except HomeAssistantUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.post('/api/home-assistant/commands')
    def execute_command(options: CommandRequest):
        return service.execute(options)

    @router.get('/api/home-assistant/inventory')
    def inventory():
        return service.inventory_view()

    @router.post('/api/home-assistant/inventory/refresh')
    def refresh_inventory():
        try:
            return service.refresh_inventory()
        except HomeAssistantUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.patch('/api/home-assistant/inventory/{entity_id}')
    def save_device(entity_id: str, options: DeviceSettings):
        return service.save_device(entity_id, options)

    @router.post('/api/home-assistant/inventory/{entity_id}/actions')
    def device_action(entity_id: str, options: DeviceAction):
        return service.device_action(entity_id, options)
