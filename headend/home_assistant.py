"""Persistent Home Assistant inventory with local names and bounded controls."""
from .command_reply import command_reply
from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from .ha_devices import (ENTITY_PATTERN, MAX_COMPOUND_CLAUSES, attributes_for_inventory, controls,
                         capabilities, expand_shared_verb_clause, format_entity_state, has_negation,
                         is_ha_control_clause, normalized, split_explicit_clauses,
                         strip_wake_and_polite, summary_intent, action_payload, intent)
from .ha_inventory import HomeAssistantInventory
from .latency import mark


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
    temperature_gap: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)

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
        self.http = httpx.Client(timeout=httpx.Timeout(10, connect=3), follow_redirects=False,
                                 trust_env=False, transport=transport,
                                 limits=httpx.Limits(max_connections=8, max_keepalive_connections=4, keepalive_expiry=30))

    def close(self):
        self.http.close()

    def request(self, settings, method, path, body=None, limit=256000):
        if not settings.token or not settings.token.get_secret_value():
            raise HomeAssistantUnavailable('Save a Home Assistant access token first.')
        try:
            with self.http.stream(method, settings.url + path, json=body,
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
        # Only typed, explicitly selected diagnostics may reach a reply. Never
        # echo arbitrary integration error strings, URLs or credential fields.
        diagnostics = []
        from datetime import datetime
        for key, label in [('last_changed', 'State last changed'), ('last_updated', 'State last updated')]:
            value = data.get(key)
            if isinstance(value, str) and len(value) <= 40:
                try:
                    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
                    if stamp.tzinfo:
                        diagnostics.append(label+': '+stamp.isoformat())
                except ValueError:
                    pass
        attrs = data.get('attributes') or {}
        if isinstance(attrs, dict):
            if attrs.get('restored') is True:
                diagnostics.append('Home Assistant marks this as a restored state, not a confirmed live update.')
            for key, label in [('battery_level', 'Reported battery percent'), ('linkquality', 'Reported link quality'), ('rssi', 'Reported RSSI')]:
                value = attrs.get(key)
                if type(value) in (int, float) and math.isfinite(value) and -10000 <= value <= 10000:
                    diagnostics.append(f'{label}: {value:g}. This alone does not establish an outage cause.')
        data['diagnostics'] = diagnostics
        data['attributes'] = attributes_for_inventory(attrs if isinstance(attrs, dict) else {})
        return data

    def control(self, settings, device, action, value=None, before_action=lambda: True):
        entity_id, name = device['entity_id'], device['name']
        state = self.device_state(settings, entity_id)
        current, attrs = state['state'], state['attributes']
        result = dict(source='Home Assistant', source_url=settings.url, entity_id=entity_id, name=name,
                      state=current, action=action, value=value)
        if current == 'unavailable' or (current == 'unknown' and entity_id.split('.')[0] not in ('button', 'input_button', 'scene', 'script')):
            return dict(result, status='unavailable', text=f'{name} is unavailable in Home Assistant. No command was sent.')
        if action == 'status':
            return dict(result, status='answered', text=format_entity_state(entity_id, name, current, attrs))
        attrs['_state'] = current
        attrs['_temperature_gap'] = device.get('temperature_gap')
        if action == 'level':
            action = {'light': 'brightness', 'fan': 'speed', 'cover': 'position', 'climate': 'temperature',
                      'media_player': 'volume', 'number': 'value', 'input_number': 'value'}.get(entity_id.split('.')[0], 'level')
        result['action'] = action
        control, body = action_payload(entity_id, attrs, action, value)
        expected = control.get('expected')
        if expected and current == expected:
            return dict(result, status='answered', text=f'{name} is already {current}.')
        if not before_action():
            return dict(result, status='ignored', text='The command expired or settings changed. No command was sent.')
        domain = entity_id.split('.')[0]
        self.request(settings, 'POST', f"/api/services/{domain}/{control['service']}", body)
        if domain == 'climate' and control['service'] == 'set_temperature':
            targets = {k:v for k,v in body.items() if k in ('temperature', 'target_temp_low', 'target_temp_high')}
            def confirmed(readback):
                return readback['state'] == state['state'] and all(
                    isinstance(readback['attributes'].get(k), (int, float))
                    and not isinstance(readback['attributes'][k], bool)
                    and math.isclose(readback['attributes'][k], v, rel_tol=0, abs_tol=.05)
                    for k,v in targets.items())
            try:
                after = self.device_state(settings, entity_id)
                for delay in (.2, .4, .8):
                    if confirmed(after): break
                    time.sleep(delay)
                    after = self.device_state(settings, entity_id)
                if confirmed(after):
                    if 'temperature' in targets:
                        text = f"{name} target is {targets['temperature']:g} degrees."
                    else:
                        text = f"{name} is set to heat at {targets['target_temp_low']:g} and cool at {targets['target_temp_high']:g} degrees."
                    return dict(result, state=after['state'], status='answered', text=text, targets=targets)
            except HomeAssistantUnavailable:
                pass
            return dict(result, status='unconfirmed', text=f'I sent the temperature settings for {name}, but could not confirm them.', targets=targets)
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

    @staticmethod
    def _summary_targets(rows: list[dict], category: str) -> list[dict]:
        active = [r for r in rows if r['imported'] and r['enabled'] and r['present']]
        out = []
        for r in active:
            domain = r['domain']
            dev_class = str(r.get('attributes', {}).get('device_class', '')).casefold()
            words = set(normalized(r['name']).split())
            is_window = (domain == 'binary_sensor' and dev_class == 'window') or (
                domain == 'cover' and (dev_class == 'window' or 'window' in words or 'windows' in words)
            )
            is_door = (domain == 'binary_sensor' and dev_class in ('door', 'garage_door', 'opening')) or (
                domain == 'cover' and not is_window and dev_class not in ('shade', 'blind', 'curtain', 'awning', 'shutter')
            )
            if category == 'doors' and is_door:
                out.append(r)
            elif category == 'windows' and is_window:
                out.append(r)
            elif category == 'openings' and (is_door or is_window):
                out.append(r)
            elif category == 'locks' and (domain == 'lock' or (domain == 'binary_sensor' and dev_class == 'lock')):
                out.append(r)
            elif category == 'lights' and (domain == 'light' or (domain == 'switch' and bool(words & {'light', 'lights', 'lamp', 'lamps'}))):
                out.append(r)
            elif category == 'leaks' and domain == 'binary_sensor' and dev_class == 'moisture':
                out.append(r)
        return out

    def _answer_summary(self, category: str, settings: HomeAssistantSettings) -> dict:
        labels = {
            'doors': ('door', 'doors', 'open', 'closed'),
            'windows': ('window', 'windows', 'open', 'closed'),
            'openings': ('door or window', 'doors and windows', 'open', 'closed'),
            'locks': ('lock', 'locks', 'unlocked', 'locked'),
            'lights': ('light', 'lights', 'on', 'off'),
            'leaks': ('leak sensor', 'leak sensors', 'wet', 'dry'),
        }
        singular, plural, active_word, calm_word = labels[category]
        targets = self._summary_targets(self.inventory.rows(settings), category)
        base = dict(source='Home Assistant', source_url=settings.url, category=category)
        if not targets:
            return dict(base, status='answered', text=f'No {plural} are imported in Home Assistant.')
        try:
            live = {r['entity_id']: r['state'] for r in self.client.entities(settings, all_domains=True)}
        except HomeAssistantUnavailable as exc:
            return dict(base, status='unavailable', text=str(exc))

        base['devices'] = [dict(entity_id=r['entity_id'], name=r['name'], state=live.get(r['entity_id'], 'unavailable')) for r in targets]
        base['checked_at'] = time.time()
        triggered, offline = [], []
        for r in targets:
            st = live.get(r['entity_id'], 'unavailable')
            if st in ('unavailable', 'unknown'):
                offline.append(r['name'])
                continue
            domain = r['domain']
            if category in ('doors', 'windows', 'openings'):
                is_active = (domain == 'binary_sensor' and st == 'on') or (domain == 'cover' and st != 'closed')
            elif category == 'locks':
                is_active = (domain == 'binary_sensor' and st == 'on') or (domain == 'lock' and st != 'locked')
            else:
                is_active = st == 'on'
            if is_active:
                triggered.append(r['name'])

        def _join_names(items: list[str]) -> str:
            shown = items[:3]
            extra = len(items) - len(shown)
            if extra > 0:
                return f"{', '.join(shown)}, and {extra} more"
            if len(shown) == 2:
                return f'{shown[0]} and {shown[1]}'
            if len(shown) == 3:
                return f'{shown[0]}, {shown[1]}, and {shown[2]}'
            return shown[0]

        suffix = (' Unavailable: ' + _join_names(offline) + '.') if offline else ''
        base['details'] = [r['name'] + ': ' + r['state'] for r in base['devices']][:100]
        if triggered:
            count = len(triggered)
            noun = singular if count == 1 else plural
            verb = 'is' if count == 1 else 'are'
            text = f'{count} {noun} {verb} {active_word}: {_join_names(triggered)}.{suffix}'
            return dict(base, status='answered', text=text[:195])

        reachable = len(targets) - len(offline)
        if reachable == 0:
            return dict(base, status='unavailable', text=f'All {len(offline)} {plural} are unavailable in Home Assistant.')
        if reachable == 1:
            name = next(r['name'] for r in targets if r['name'] not in offline)
            text = f'{name} is {calm_word}.{suffix}'
        else:
            text = f'All {reachable} {plural} are {calm_word}.{suffix}'
        return dict(base, status='answered', text=text[:195])

    @staticmethod
    def _prevalidate_device_step(row: dict, action: str, value) -> None:
        if action == 'status':
            return
        entity_id = row['entity_id']
        resolved = action
        if resolved == 'level':
            resolved = {
                'light': 'brightness', 'fan': 'speed', 'cover': 'position', 'climate': 'temperature',
                'media_player': 'volume', 'number': 'value', 'input_number': 'value',
            }.get(entity_id.split('.')[0], 'level')
        attrs = dict(row.get('attributes') or {})
        attrs['_temperature_gap'] = row.get('temperature_gap')
        if row.get('state'):
            attrs.setdefault('_state', row['state'])
        action_payload(entity_id, attrs, resolved, value)

    def _resolve_candidate_pairs(self, pairs: list[tuple[str, str | None]], settings: HomeAssistantSettings,
                                 explicit_multi_verb: bool) -> dict:
        if len(pairs) < 2:
            return dict(kind='ignored')
        steps = []
        matched_count = 0
        unmatched_clauses = []
        for primary, fallback in pairs:
            if has_negation(primary):
                return dict(kind='ignored')
            m = self.inventory.matches(settings, primary)
            if not m and fallback:
                m = self.inventory.matches(settings, fallback)
            if not m:
                # Resolve only explicit singular references within this request.
                # Never borrow a target from a prior conversation or choose one
                # arbitrarily after a clause naming multiple devices.
                reference = re.fullmatch(
                    r'(?:set|dim) (?:(?:the )?brightness of )?(it|that|that one|this one|that light|that device) to .+|'
                    r'(?:turn|switch) (it|that|that one|this one|that light|that device) (?:on|off)|'
                    r'(?:turn|switch) (?:on|off) (it|that|that one|this one|that light|that device)',
                    strip_wake_and_polite(primary), flags=re.I)
                if reference and steps and all(step[0] == 'device' for step in steps):
                    antecedents = {step[1]['entity_id']: step[1] for step in steps}
                    if len(antecedents) != 1:
                        return dict(kind='unsupported', text='Please name the device for that part of the command. More than one device was mentioned. No commands were sent.')
                    row = next(iter(antecedents.values()))
                    pronoun = next(group for group in reference.groups() if group)
                    action = intent(primary, pronoun, row['entity_id'], row.get('attributes'))
                    if action:
                        m = [(row, action)]
            if len(m) > 1:
                return dict(kind='ambiguous')
            if len(m) == 1:
                matched_count += 1
                steps.append(('device', m[0][0], m[0][1]))
                continue
            cat = summary_intent(primary)
            if cat:
                matched_count += 1
                steps.append(('summary', cat, None))
                continue
            unmatched_clauses.append(primary)
            steps.append(('unmatched', primary, None))

        if len(pairs) > MAX_COMPOUND_CLAUSES:
            if matched_count > 0:
                return dict(
                    kind='unsupported',
                    text=f'Please ask for up to {MAX_COMPOUND_CLAUSES} device actions at a time. No commands were sent.',
                )
            return dict(kind='ignored')

        if matched_count == len(pairs):
            deduped = []
            seen_actions: dict[str, tuple] = {}
            for kind, target, act in steps:
                if kind == 'device':
                    eid = target['entity_id']
                    if eid.startswith('light.') and act[0] == 'level':
                        act = ('brightness', act[1])
                    sig = (act[0], json.dumps(act[1], sort_keys=True) if isinstance(act[1], list) else act[1])
                    if act[0] != 'status':
                        if eid in seen_actions:
                            previous_sig, previous_index = seen_actions[eid]
                            if previous_sig == sig:
                                continue
                            # Adjacent on + brightness for one light is one
                            # turn_on(brightness_pct=...) call. Brightness zero
                            # conflicts with an explicit request to turn on.
                            if (eid.startswith('light.') and previous_index == len(deduped)-1
                                    and {previous_sig[0], sig[0]} == {'on', 'brightness'}):
                                brightness = act if act[0] == 'brightness' else deduped[previous_index][2]
                                if isinstance(brightness[1], (int, float)) and brightness[1] > 0:
                                    deduped[previous_index] = ('device', target, brightness)
                                    seen_actions[eid] = (brightness, previous_index)
                                    continue
                            return dict(
                                kind='unsupported',
                                text='Those commands conflict on the same device. No commands were sent.',
                            )
                        seen_actions[eid] = (sig, len(deduped))
                deduped.append((kind, target, act))
            return dict(kind='compound', steps=deduped)

        if matched_count > 0 and explicit_multi_verb:
            bad_ctrl = next((c for c in unmatched_clauses if is_ha_control_clause(c)), None)
            if bad_ctrl:
                return dict(kind='partial_unmatched', unmatched=bad_ctrl)

        return dict(kind='ignored')

    def resolve_plan(self, text: str, settings: HomeAssistantSettings) -> dict:
        if not isinstance(text, str) or len(text) > 300 or has_negation(text):
            return dict(kind='ignored')
        matches = self.inventory.matches(settings, text)
        if len(matches) == 1:
            return dict(kind='single', step=('device', matches[0][0], matches[0][1]))
        if len(matches) > 1:
            return dict(kind='ambiguous')
        category = summary_intent(text)
        if category:
            return dict(kind='single', step=('summary', category, None))

        explicit = split_explicit_clauses(text)
        if len(explicit) >= 2:
            expanded_pairs: list[tuple[str, str | None]] = []
            for clause in explicit:
                if self.inventory.matches(settings, clause) or summary_intent(clause):
                    expanded_pairs.append((clause, None))
                else:
                    expanded_pairs.extend(expand_shared_verb_clause(clause))
            plan = self._resolve_candidate_pairs(expanded_pairs, settings, explicit_multi_verb=True)
            if plan['kind'] in ('compound', 'ambiguous', 'unsupported'):
                return plan
            shared_fallback = expand_shared_verb_clause(strip_wake_and_polite(text))
            if len(shared_fallback) >= 2:
                alt = self._resolve_candidate_pairs(shared_fallback, settings, explicit_multi_verb=False)
                if alt['kind'] in ('compound', 'ambiguous', 'unsupported'):
                    return alt
            return plan

        shared = expand_shared_verb_clause(strip_wake_and_polite(text))
        if len(shared) >= 2:
            return self._resolve_candidate_pairs(shared, settings, explicit_multi_verb=False)
        return dict(kind='ignored')

    @staticmethod
    def _combine_results(results: list[dict], source_url: str) -> dict:
        if len(results) == 1:
            return results[0]

        same_pred = None
        same_names = []
        for r in results:
            m = re.fullmatch(r'(.+?) is ((?:now|already) [^.]+)\.', r.get('text', '').strip())
            if not m or (same_pred is not None and m.group(2) != same_pred):
                same_names = []
                break
            same_pred = m.group(2)
            same_names.append(m.group(1))

        if same_names and same_pred:
            if len(same_names) == 2:
                subject = f'{same_names[0]} and {same_names[1]}'
            else:
                subject = f"{', '.join(same_names[:-1])}, and {same_names[-1]}"
            combined_text = f'{subject} are {same_pred}.'
        else:
            any_acted = any(r.get('status') in ('answered', 'sent', 'unconfirmed') for r in results)
            cleaned = []
            for r in results:
                txt = (r.get('text') or '').strip()
                if not txt:
                    continue
                if any_acted and txt.endswith(' is unavailable in Home Assistant. No command was sent.'):
                    txt = txt[:-len(' No command was sent.')]
                cleaned.append(txt)
            if cleaned and all('. ' not in t for t in cleaned):
                clauses = [t[:-1] if t.endswith('.') else t for t in cleaned]
                if len(clauses) == 2:
                    combined_text = f'{clauses[0]}, and {clauses[1]}.'
                elif len(clauses) >= 3:
                    combined_text = f"{', '.join(clauses[:-1])}, and {clauses[-1]}."
                else:
                    combined_text = cleaned[0]
            else:
                combined_text = ' '.join(cleaned)

        if len(combined_text) > 220:
            short = combined_text[:217].rsplit(' ', 1)[0].rstrip(' ,;:.')
            combined_text = short + '.'

        statuses = [r.get('status') for r in results]
        if 'unconfirmed' in statuses:
            agg_status = 'unconfirmed'
        elif all(s == 'unavailable' for s in statuses):
            agg_status = 'unavailable'
        elif all(s == 'ignored' for s in statuses):
            agg_status = 'ignored'
        elif 'sent' in statuses:
            agg_status = 'sent'
        else:
            agg_status = 'answered'

        return dict(
            source='Home Assistant',
            source_url=source_url,
            status=agg_status,
            text=combined_text,
            results=results,
        )

    @staticmethod
    def unmatched_clause_error(clause: str) -> dict:
        snippet = ' '.join((clause or '').split())[:60]
        return dict(
            status='unsupported',
            text=f"I could not match '{snippet}'. Check its Adam name in Home Assistant settings. No commands were sent.",
        )

    def answer(self, text, settings, before_action=lambda: True):
        plan = self.resolve_plan(text, settings)
        kind = plan['kind']
        if kind == 'ignored':
            return dict(status='ignored', text='')
        if kind == 'ambiguous':
            return dict(
                status='ambiguous',
                text='That name matches more than one device. Give them distinct Adam names or aliases in the inventory. No command was sent.',
            )
        if kind == 'unsupported':
            return dict(status='unsupported', text=plan['text'])
        if kind == 'partial_unmatched':
            return self.unmatched_clause_error(plan['unmatched'])
        if kind == 'single':
            step_kind, target, act = plan['step']
            if step_kind == 'summary':
                return self._answer_summary(target, settings)
            try:
                return self.client.control(settings, target, act[0], act[1], before_action)
            except ValueError as exc:
                return dict(status='unsupported', text=str(exc))
            except HomeAssistantUnavailable as exc:
                return dict(status='unavailable', text=str(exc), source='Home Assistant', source_url=settings.url)

        return self._answer_steps(plan['steps'], settings, before_action)

    def _answer_steps(self, steps, settings, before_action):
        try:
            for step_kind, target, act in steps:
                if step_kind == 'device':
                    self._prevalidate_device_step(target, act[0], act[1])
        except ValueError as exc:
            return dict(status='unsupported', text=str(exc))

        results = []
        try:
            for step_kind, target, act in steps:
                if step_kind == 'summary':
                    res = self._answer_summary(target, settings)
                else:
                    res = self.client.control(settings, target, act[0], act[1], before_action)
                if res.get('status') == 'ignored':
                    return res
                results.append(res)
        except ValueError as exc:
            return dict(status='unsupported', text=str(exc))
        except HomeAssistantUnavailable as exc:
            return dict(status='unavailable', text=str(exc), source='Home Assistant', source_url=settings.url)
        return self._combine_results(results, settings.url)

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
                    and (entry is None or (time.time() - entry['started'] <= 30 and (entry.get('trigger') == 'text' or self.online(entry['unit_id'])))))

    def claim_session(self, unit_id: str, session_id: str) -> bool:
        with self.state.db() as db:
            db.execute('DELETE FROM home_assistant_commands WHERE created<?', (time.time() - 30*86400,))
            return db.execute('INSERT OR IGNORE INTO home_assistant_commands VALUES(?,?,?)',
                              (unit_id, session_id, time.time())).rowcount > 0

    def submit(self, entry, job):
        if self.closed or entry['trigger'] not in ('wake', 'followup', 'text') or job['status'] != 'done':
            return
        with self.lock:
            settings = self.settings()
            plan = (dict(kind='context') if entry.get('context_action') else self.resolve_plan(job['text'], settings)) if settings.enabled else dict(kind='ignored')
            # 'partial_unmatched' is claimed so the reply names the clause that failed to match.
            if not settings.enabled or plan['kind'] == 'ignored':
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
                        or time.time() - entry['started'] > 30 or not (entry.get('trigger') == 'text' or self.online(entry['unit_id']))):
                    entry['reply'] = dict(status='ignored', text='', error='Command expired, unit is offline, or settings changed')
                    return
                if not self.claim_session(entry['unit_id'] or 'web', entry['id']):
                    entry['reply'] = dict(status='ignored', text='', error='This command session was already handled')
                    return
            context = entry.get('context_action')
            if context:
                ids = context.get('entity_ids', [context.get('entity_id')])
                targets = [r for r in self.inventory.rows(settings) if r['entity_id'] in ids
                           and r['imported'] and r['enabled'] and r['present']]
                if (context['origin'] != settings.url or context['revision'] != revision
                        or not 1 <= len(ids) <= MAX_COMPOUND_CLAUSES or len(targets) != len(ids)):
                    result = dict(status='unsupported', text='Device settings changed. Please say the full command again. No command was sent.')
                elif 'entity_ids' not in context:
                    # Single-device setpoints keep their existing live-state validation.
                    try:
                        result = self.client.control(settings, targets[0], context['action'], context['value'],
                                                     lambda:self.valid(revision, entry))
                    except ValueError as exc:
                        result = dict(status='unsupported', text=str(exc))
                    except HomeAssistantUnavailable as exc:
                        result = dict(status='unavailable', text=str(exc))
                else:
                    result = self._answer_steps([('device', row, (context['action'], context['value'])) for row in targets],
                                                settings, lambda:self.valid(revision, entry))
                if entry.get('correction_from') and result.get('status') in ('answered', 'sent'):
                    result['text'] += ' The earlier '+entry['correction_from']+' command was not undone.'
            else:
                result = self.answer(text, settings, lambda: self.valid(revision, entry))
            mark(entry, 'ha_result')
            entry['reply'] = command_reply(entry, result, self.speak) if not self.closed else result
            mark(entry, 'reply_ready')
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
        if not self.refresh_thread.is_alive() and hasattr(self.client, 'close'):
            self.client.close()


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
