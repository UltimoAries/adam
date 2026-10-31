"""Optional LAN speech workers, with independent local fallbacks.

The workers only transcribe or synthesize. Command execution remains in ADAM.
Uses the audio HTTP protocol implemented by Speaches; no cloud SDK is needed.
"""
import asyncio
import json
import threading
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from .clips import MAX_UPLOAD, mono_16k, parse_wav, wav_bytes
from .session import Trigger, Utterance
from .stt import Transcript


class ServiceProfile(BaseModel):
    mode: Literal['local', 'prefer_remote'] = 'local'
    url: str = Field(default='', max_length=250)
    model: str = Field(default='', max_length=200)
    voice: str = Field(default='', max_length=100)
    api_key: str = Field(default='', max_length=512, repr=False)
    clear_key: bool = False
    timeout_seconds: float = Field(default=6, ge=1, le=30, allow_inf_nan=False)
    cooldown_seconds: int = Field(default=30, ge=5, le=300)

    @model_validator(mode='after')
    def validate_profile(self):
        self.url = self.url.strip().rstrip('/')
        self.model, self.voice = self.model.strip(), self.voice.strip()
        if self.url:
            address = urlsplit(self.url)
            if (address.scheme not in ('http', 'https') or not address.hostname or
                    address.username is not None or address.password is not None or
                    address.query or address.fragment or any(c.isspace() for c in self.url)):
                raise ValueError('Use an HTTP(S) service base URL without credentials or query parameters')
            try:
                address.port
            except ValueError:
                raise ValueError('Invalid service port') from None
        if '\r' in self.api_key or '\n' in self.api_key:
            raise ValueError('Invalid API key')
        if self.mode == 'prefer_remote' and (not self.url or not self.model):
            raise ValueError('Choose a service URL and model before preferring remote speech')
        return self


class ServiceSettings(BaseModel):
    stt: ServiceProfile = Field(default_factory=ServiceProfile)
    tts: ServiceProfile = Field(default_factory=ServiceProfile)

    @model_validator(mode='after')
    def validate_voice(self):
        if self.tts.mode == 'prefer_remote' and not self.tts.voice:
            raise ValueError('Choose the remote speech voice')
        return self


class ModelServices:
    def __init__(self, state, transport=None, clock=time.monotonic):
        self.state, self.transport, self.clock = state, transport, clock
        self.lock = threading.RLock()
        self.generation = 0
        self.runtime = {}
        with state.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS model_services (id INTEGER PRIMARY KEY CHECK(id=1), settings TEXT NOT NULL)')
            row = db.execute('SELECT settings FROM model_services WHERE id=1').fetchone()
        self.settings = ServiceSettings.model_validate_json(row[0]) if row else ServiceSettings()

    def public(self):
        with self.lock:
            result = {}
            for kind in ('stt', 'tts'):
                profile = getattr(self.settings, kind)
                status = self.runtime.get(kind, {})
                result[kind] = dict(profile.model_dump(exclude={'api_key', 'clear_key'}),
                    has_key=bool(profile.api_key), last_provider=status.get('last_provider'),
                    last_error=status.get('last_error'),
                    retry_in_seconds=max(0, round(status.get('retry_at', 0)-self.clock())))
            return result

    def save(self, options):
        with self.lock:
            saved = options.model_copy(deep=True)
            for kind in ('stt', 'tts'):
                profile = getattr(saved, kind)
                old = getattr(self.settings, kind)
                # Never carry a saved credential to a different server automatically.
                if profile.clear_key:
                    profile.api_key = ''
                elif not profile.api_key and profile.url == old.url:
                    profile.api_key = old.api_key
                profile.clear_key = False
            with self.state.db() as db:
                db.execute('INSERT OR REPLACE INTO model_services VALUES (1,?)', (saved.model_dump_json(),))
            self.settings = saved
            self.generation += 1
            self.runtime.clear()
            return self.public()

    async def _request(self, profile, path, limit, **kwargs):
        headers = {'Authorization': 'Bearer '+profile.api_key} if profile.api_key else {}
        async def request():
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                    follow_redirects=False, timeout=httpx.Timeout(profile.timeout_seconds, connect=1.5)) as client:
                async with client.stream('POST', profile.url+'/'+path, headers=headers, **kwargs) as response:
                    response.raise_for_status()
                    output = bytearray()
                    async for chunk in response.aiter_bytes():
                        output.extend(chunk)
                        if len(output) > limit:
                            raise ValueError('Model response exceeded the size limit')
                    return bytes(output)
        # Includes connection, upload and streamed body, rather than a per-chunk timeout.
        return await asyncio.wait_for(request(), timeout=profile.timeout_seconds)

    def route(self, kind, remote, local):
        with self.lock:
            profile = getattr(self.settings, kind).model_copy(deep=True)
            generation = self.generation
            attempt = (profile.mode == 'prefer_remote' and
                       self.clock() >= self.runtime.get(kind, {}).get('retry_at', 0))
        if attempt:
            try:
                output = remote(profile)
            except Exception as exc:
                # Exception URLs, response bodies and credentials never reach the UI/logs.
                with self.lock:
                    if generation == self.generation:
                        self.runtime[kind] = dict(last_error='Remote speech failed: '+type(exc).__name__,
                            retry_at=self.clock()+profile.cooldown_seconds)
            else:
                with self.lock:
                    if generation == self.generation:
                        self.runtime[kind] = dict(last_provider='remote', last_error=None, retry_at=0)
                return output
        try:
            output = local()
        except Exception:
            with self.lock:
                if generation == self.generation:
                    self.runtime.setdefault(kind, {})['last_provider'] = 'local_error'
            raise
        with self.lock:
            if generation == self.generation:
                self.runtime.setdefault(kind, {})['last_provider'] = 'local'
        return output

    def transcribe(self, utterance, local):
        def remote(profile):
            wav = wav_bytes(16000, np.frombuffer(utterance.pcm, dtype='<i2'))
            raw = asyncio.run(self._request(profile, 'audio/transcriptions', 64*1024,
                files={'file': ('utterance.wav', wav, 'audio/wav')},
                data={'model': profile.model, 'language': 'en', 'response_format': 'json'}))
            data = json.loads(raw)
            if not isinstance(data, dict) or not isinstance(data.get('text'), str) or len(data['text']) > 8000:
                raise ValueError('Invalid transcription response')
            # Empty text is a valid no-speech result, not a reason to retry another model.
            return Transcript(utterance.session_id, data['text'].strip())
        return self.route('stt', remote, lambda: local(utterance))

    def synthesize(self, text, local):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
            raise ValueError('Use 1–200 characters of speech')
        def remote(profile):
            raw = asyncio.run(self._request(profile, 'audio/speech', MAX_UPLOAD,
                json=dict(model=profile.model, voice=profile.voice, input=text.strip(), response_format='wav')))
            rate, samples = parse_wav(raw)
            pcm = mono_16k(rate, samples, 1)[0]
            values = np.frombuffer(pcm, dtype='<i2').astype(np.int32)
            if not 1600 <= len(values) <= 240000 or np.max(np.abs(values)) < 2:
                raise ValueError('Remote speech is empty or exceeds 15 seconds')
            return pcm
        return self.route('tts', remote, lambda: local(text))


class RoutedTranscriber:
    def __init__(self, services, factory):
        self.services, self.factory, self.local = services, factory, None

    def local_transcribe(self, utterance):
        if self.local is None:
            self.local = self.factory()
        return self.local.transcribe(utterance)

    def preload(self):
        # Keep fallback ready even if the LAN worker is online at startup.
        self.local_transcribe(Utterance('warmup', Trigger.TOUCH, None, bytes(16000)))

    def transcribe(self, utterance):
        return self.services.transcribe(utterance, self.local_transcribe)


class RoutedSpeech:
    def __init__(self, services, local):
        self.services, self.local = services, local

    def synthesize(self, text):
        return self.services.synthesize(text, self.local.synthesize)


def add_model_routes(app, services):
    router = APIRouter()

    @router.get('/api/models/settings')
    def settings():
        return services.public()

    @router.put('/api/models/settings')
    async def save(request: Request):
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 8192:
                    raise ValueError('Settings too large')
            options = ServiceSettings.model_validate_json(bytes(raw))
        except ValueError:
            # Pydantic's standard 422 includes rejected inputs, including API keys.
            raise HTTPException(400, 'Check the mode, URL, model, voice and timeout (1–30 seconds).') from None
        return await asyncio.to_thread(services.save, options)

    app.include_router(router)
