"""One GPU host/preference for recognition and conversation; local Piper output."""
import math
import threading
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .conversation import ConversationSettings
from .model_catalog import bounded_body
from .model_services import ServiceProfile, ServiceSettings


class GPUFailover:
    def __init__(self, clock=time.monotonic):
        self.clock, self.until = clock, 0
        self.lock = threading.Lock()

    def remaining(self):
        with self.lock:
            return max(0, math.ceil(self.until-self.clock()))

    def fail(self, seconds):
        with self.lock:
            self.until = max(self.until, self.clock()+seconds)

    def reset(self):
        with self.lock:
            self.until = 0


class ProcessingSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['local', 'prefer_remote'] = 'local'
    gpu_url: str = Field(default='http://gpu-host.local', max_length=250)
    speech_port: int = Field(default=8000, ge=1, le=65535)
    conversation_port: int = Field(default=11434, ge=1, le=65535)
    stt_model: str = Field(default='', max_length=200)
    conversation_model: str = Field(default='adam:latest', min_length=1, max_length=150)
    api_key: SecretStr = Field(default_factory=lambda:SecretStr(''), max_length=512)
    clear_key: bool = False
    conversation_enabled: bool = True
    stt_timeout: float = Field(default=3, ge=1, le=30, allow_inf_nan=False)
    conversation_timeout: float = Field(default=6, ge=1, le=30, allow_inf_nan=False)
    cooldown_seconds: int = Field(default=30, ge=5, le=300)

    @field_validator('gpu_url')
    @classmethod
    def host(cls, value):
        value = ConversationSettings(remote_url=value).remote_url
        if urlsplit(value).port is not None:
            raise ValueError('Set service ports in advanced settings')
        return value


class ProcessingService:
    def __init__(self, speech, conversation):
        self.speech, self.conversation = speech, conversation
        self.failover = GPUFailover()
        speech.gpu_failover = conversation.gpu_failover = self.failover

    def public(self):
        with self.speech.lock, self.conversation.lock:
            stt, chat = self.speech.settings.stt, self.conversation.options
            parsed = urlsplit(stt.url or chat.remote_url)
            chat_address = urlsplit(chat.remote_url)
            common = stt.mode == chat.mode and (not stt.url or
                (parsed.scheme, parsed.hostname) == (chat_address.scheme, chat_address.hostname))
            hostname = parsed.hostname or 'gpu-host.local'
            if ':' in hostname:
                hostname = '['+hostname+']'
            return dict(mode=stt.mode if common else 'custom',
                gpu_url=parsed.scheme+'://'+hostname, speech_port=urlsplit(stt.url).port or 8000,
                conversation_port=urlsplit(chat.remote_url).port or 11434,
                stt_model=stt.model, conversation_model=chat.remote_model,
                has_key=bool(stt.api_key), conversation_enabled=chat.enabled,
                stt_timeout=stt.timeout_seconds, conversation_timeout=chat.remote_timeout_seconds,
                cooldown_seconds=stt.cooldown_seconds,
                retry_in_seconds=self.failover.remaining(),
                stt_provider=self.speech.runtime.get('stt',{}).get('last_provider'),
                conversation_provider=self.conversation.last_provider,
                local_stt_model='base.en', local_conversation_model=chat.local_model,
                voice_provider='local')

    def save(self, options):
        with self.speech.lock, self.conversation.lock:
            old = self.speech.settings
            speech_url = options.gpu_url+':'+str(options.speech_port)+'/v1'
            key = '' if options.clear_key else options.api_key.get_secret_value() or (
                old.stt.api_key if speech_url == old.stt.url else '')
            stt = ServiceProfile(mode=options.mode, url=speech_url, model=options.stt_model,
                api_key=key, timeout_seconds=options.stt_timeout, cooldown_seconds=options.cooldown_seconds)
            speech = ServiceSettings(stt=stt, tts=old.tts.model_copy(update={'mode':'local'}))
            chat = ConversationSettings.model_validate(dict(self.conversation.options.model_dump(),
                mode=options.mode, enabled=options.conversation_enabled,
                remote_url=options.gpu_url+':'+str(options.conversation_port),
                remote_model=options.conversation_model, remote_timeout_seconds=options.conversation_timeout,
                cooldown_seconds=options.cooldown_seconds))
            # Both routes commit together. Failed validation/commit changes neither route.
            with self.speech.state.db() as db:
                db.execute('INSERT OR REPLACE INTO model_services VALUES(1,?)',(speech.model_dump_json(),))
                db.execute('INSERT OR REPLACE INTO conversation_settings VALUES(1,?)',(chat.model_dump_json(),))
            self.speech.settings = speech
            self.speech.generation += 1
            self.speech.runtime.clear()
            self.conversation.options = chat
            self.conversation.generation += 1
            self.conversation.retry_at, self.conversation.last_provider, self.conversation.last_error = 0, None, None
            self.conversation.availability, self.conversation.checked_at, self.conversation.next_probe = {}, None, 0
            self.failover.reset()
            return self.public()


def add_processing_routes(app, speech, conversation):
    service = ProcessingService(speech, conversation)
    app.state.processing = service

    @app.get('/api/processing/settings')
    def get_settings():
        return service.public()

    @app.put('/api/processing/settings')
    async def save_settings(request: Request):
        import asyncio
        try:
            options = ProcessingSettings.model_validate_json(await bounded_body(request))
            return await asyncio.to_thread(service.save, options)
        except ValueError:
            raise HTTPException(400, 'Choose a processing mode, GPU address without a port, valid service ports, and both models. Check the API key and timing values.') from None
