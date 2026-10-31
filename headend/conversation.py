"""Short spoken answers: preferred LAN Ollama, with a bounded local Pi fallback."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import re
import threading
import time
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator


SYSTEM = ('You are ADAM, a home voice assistant. Answer in one short sentence, '
          'at most 30 words and 180 characters. Use plain speech without markup. '
          'You cannot control devices or access live weather, news or personal data. '
          'Never claim to have performed an action. Admit uncertainty.')


def question(text):
    return re.sub(r'^(?:(?:hey|okay|ok)[ ,]+)?(?:adam|jarvis)\b[\s,.:!?-]*', '', text.strip(), flags=re.I).strip()


def control_request(text):
    text = re.sub(r'^(?:(?:can|could|would) you\s+)?(?:please\s+)?', '', question(text), flags=re.I)
    return bool(re.match(r'^(?:turn|switch|toggle|dim|brighten|set|lock|unlock|open|close|start|stop|pause|resume|mute|unmute|arm|disarm|activate|deactivate)\b', text, re.I))


class ConversationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    remote_url: str = Field(default='http://gpu-host.local:11434', max_length=250)
    remote_model: str = Field(default='qwen3.5:9b', min_length=1, max_length=150)
    local_url: str = Field(default='http://127.0.0.1:11434', max_length=250)
    local_model: str = Field(default='qwen3.5:0.8b', min_length=1, max_length=150)
    remote_timeout_seconds: float = Field(default=6, ge=1, le=30, allow_inf_nan=False)
    local_timeout_seconds: float = Field(default=20, ge=1, le=60, allow_inf_nan=False)
    cooldown_seconds: int = Field(default=30, ge=5, le=300)

    @field_validator('remote_url', 'local_url')
    @classmethod
    def origin(cls, value, info):
        value = value.strip().rstrip('/')
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.path or parsed.query or parsed.fragment or re.search(r'[\s\\\x00-\x1f]', value)):
            raise ValueError('Use an Ollama HTTP(S) origin without a path or credentials')
        parsed.port
        if info.field_name == 'local_url' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('The fallback must run on the head-end itself')
        return value


def spoken_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('Invalid conversation answer')
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.S | re.I)
    if re.search(r'</?think', text, re.I):
        raise ValueError('Incomplete thinking output')
    text = re.sub(r'[`*_#]', '', text)
    text = ' '.join(text.split()).strip()
    if not text:
        raise ValueError('Empty conversation answer')
    if len(text) > 200:
        short = text[:197].rsplit(' ', 1)[0].rstrip(' ,;:')
        ending = list(re.finditer(r'[.!?](?:\s|$)', short))
        text = short[:ending[-1].start()+1] if ending else short+'...'
    return text


class ConversationService:
    def __init__(self, state, deliver, transport=None, clock=time.monotonic):
        self.state, self.deliver, self.transport, self.clock = state, deliver, transport, clock
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='conversation')
        self.probes = ThreadPoolExecutor(max_workers=1, thread_name_prefix='llm-status')
        self.probe_pending, self.next_probe = False, 0
        self.availability, self.checked_at = {}, None
        self.slots = threading.BoundedSemaphore(4)
        self.closed, self.generation = False, 0
        self.retry_at, self.last_provider, self.last_error = 0, None, None
        with state.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS conversation_settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
            row = db.execute('SELECT data FROM conversation_settings WHERE id=1').fetchone()
        self.options = ConversationSettings.model_validate_json(row[0]) if row else ConversationSettings()

    def public(self):
        with self.lock:
            return dict(self.options.model_dump(), last_provider=self.last_provider, last_error=self.last_error,
                        retry_in_seconds=max(0, round(self.retry_at-self.clock())))

    def save(self, options):
        with self.lock:
            with self.state.db() as db:
                db.execute('INSERT OR REPLACE INTO conversation_settings VALUES(1,?)', (options.model_dump_json(),))
            self.options = options.model_copy(deep=True)
            self.generation += 1
            self.retry_at, self.last_provider, self.last_error = 0, None, None
            self.availability, self.checked_at, self.next_probe = {}, None, 0
            return self.public()

    async def _model_loaded(self, options, provider):
        """Read loaded-model metadata only; never generate text or load a model."""
        async def check():
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(1.5, connect=.5)) as client:
                async def get(path):
                    async with client.stream('GET', getattr(options, provider+'_url')+path) as response:
                        response.raise_for_status()
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 128*1024:
                                raise ValueError('Model status exceeded its size limit')
                    return json.loads(raw)['models']
                tags = await get('/api/tags')
                wanted = getattr(options, provider+'_model')
                if ':' not in wanted.rsplit('/', 1)[-1]:
                    wanted += ':latest'
                selected = next((m for m in tags if m.get('name') == wanted), None)
                if selected is None:
                    return False
                active = await get('/api/ps')
                return any(m.get('name') == wanted or
                    (selected.get('digest') and m.get('digest') == selected['digest']) for m in active)
        try:
            return bool(await asyncio.wait_for(check(), timeout=1.5))
        except Exception:
            return False

    def _probe_models(self, options, generation):
        async def check():
            return await asyncio.gather(self._model_loaded(options, 'remote'), self._model_loaded(options, 'local'))
        try:
            remote, local = asyncio.run(check())
            with self.lock:
                if generation == self.generation and not self.closed:
                    self.availability = dict(remote=remote, local=local)
                    self.checked_at = self.clock()
                    self.next_probe = self.checked_at+5
        finally:
            with self.lock:
                self.probe_pending = False

    def panel_provider(self):
        """Cached effective route for idle panels; a slow probe never blocks a poll."""
        with self.lock:
            if self.closed or not self.options.enabled:
                return 'disabled'
            now = self.clock()
            if now >= self.next_probe and not self.probe_pending:
                self.probe_pending = True
                try:
                    self.probes.submit(self._probe_models, self.options.model_copy(), self.generation)
                except RuntimeError:
                    self.probe_pending = False
            if self.checked_at is None or now-self.checked_at > 15:
                return 'unknown'
            if self.availability.get('remote') and now >= self.retry_at:
                return 'remote'
            if self.availability.get('local'):
                return 'local'
            return 'unavailable'

    async def _request(self, url, model, text, timeout, local):
        async def request():
            async with httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                    timeout=httpx.Timeout(timeout, connect=min(1.5, timeout))) as client:
                async with client.stream('POST', url+'/api/chat', json={
                        'model': model, 'stream': False, 'think': False, 'keep_alive': -1,
                        'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': text}],
                        'options': dict(num_ctx=1024 if local else 4096, num_predict=64,
                                        temperature=0.3, **({'num_thread': 2} if local else {}))}) as response:
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 32768:
                            raise ValueError('Conversation response exceeded its size limit')
                    data = json.loads(raw)
            message = data.get('message') if isinstance(data, dict) else None
            if not isinstance(message, dict) or message.get('tool_calls') or not data.get('done'):
                raise ValueError('Invalid or incomplete conversation response')
            return spoken_text(message.get('content'))
        return await asyncio.wait_for(request(), timeout=timeout)

    def answer(self, text):
        with self.lock:
            options, generation = self.options.model_copy(), self.generation
            attempt_remote = self.clock() >= self.retry_at
        if not options.enabled:
            return dict(status='ignored', text='')
        text = question(text)
        if not text:
            return dict(status='ignored', text='')
        if control_request(text):
            return dict(status='unsupported', text='I could not match that device command. Check its Adam name and enabled controls in Home Assistant settings.')
        error = None
        for provider in (['remote', 'local'] if attempt_remote else ['local']):
            local = provider == 'local'
            try:
                answer = asyncio.run(self._request(getattr(options, provider+'_url'),
                    getattr(options, provider+'_model'), text,
                    getattr(options, provider+'_timeout_seconds'), local))
            except Exception as exc:
                error = provider.capitalize()+' conversation unavailable: '+type(exc).__name__
                with self.lock:
                    if generation == self.generation:
                        self.last_error = error
                        if not local:
                            self.retry_at = self.clock()+options.cooldown_seconds
                continue
            with self.lock:
                if generation == self.generation:
                    self.last_provider = provider
                    if not local:
                        self.retry_at, self.last_error = 0, None
            return dict(status='answered', text=answer, provider=provider, model=getattr(options, provider+'_model'))
        with self.lock:
            if generation == self.generation:
                self.last_provider = 'unavailable'
        return dict(status='unavailable', text='My conversation models are unavailable right now. Please try again shortly.')

    def submit(self, entry, job):
        with self.lock:
            if (self.closed or not self.options.enabled or entry['trigger'] != 'wake' or
                    job['status'] != 'done' or entry.get('conversation_submitted')):
                return
            entry['conversation_submitted'] = True
            if not self.slots.acquire(blocking=False):
                entry['reply'] = dict(status='error', text='', error='Conversation queue is full')
                return
            entry['reply'] = dict(status='working', text='')
            try:
                self.pool.submit(self._run, entry, job['text'], self.generation)
            except RuntimeError:
                self.slots.release()
                entry['reply'] = dict(status='ignored', text='')

    def _run(self, entry, text, generation):
        try:
            with self.lock:
                valid = not self.closed and self.options.enabled and generation == self.generation
            if not valid:
                entry['reply'] = dict(status='ignored', text='', error='Conversation settings changed')
                return
            if time.time()-entry['started'] > 60:
                entry['reply'] = dict(status='ignored', text='', error='Question expired')
                return
            started = self.clock()
            result = self.answer(text)
            result['lookup_ms'] = round((self.clock()-started)*1000)
            with self.lock:
                valid = not self.closed and self.options.enabled and generation == self.generation
            if valid:
                entry['reply'] = self.deliver(result, entry['unit_id']) if result['text'] else result
            else:
                entry['reply'] = dict(status='ignored', text='', error='Conversation settings changed')
        except Exception:
            entry['reply'] = dict(status='error', text='', error='The conversation answer could not be played')
        finally:
            self.slots.release()

    def close(self):
        with self.lock:
            self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
        self.probes.shutdown(wait=True, cancel_futures=True)


class ConversationRequest(BaseModel):
    text: str = Field(min_length=1, max_length=800)


def add_conversation_routes(router, service):
    @router.get('/api/conversation/settings')
    def settings():
        return service.public()

    @router.put('/api/conversation/settings')
    def save(options: ConversationSettings):
        return service.save(options)

    @router.post('/api/conversation/test')
    def test(options: ConversationRequest):
        if not service.slots.acquire(blocking=False):
            raise HTTPException(429, 'Conversation queue is full')
        try:
            return service.answer(options.text)
        finally:
            service.slots.release()
