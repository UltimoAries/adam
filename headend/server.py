"""Local audio test server. Start with python -m headend.server."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
import os
import re
import socket
from pathlib import Path
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .clips import ClipStore, MAX_UPLOAD, mono_16k, parse_wav, wav_bytes
from .device import DeviceBridge, DeviceError
from .session import Trigger, Utterance
from .stt import FasterWhisperSTT
from .state import State
from .fleet import Fleet
from .voices import VoiceService
from .model_services import ModelServices, RoutedTranscriber, RoutedSpeech, add_model_routes

ROOT = Path(__file__).resolve().parent


class TranscriptionJobs:
    def __init__(self, model_directory, provider_factory=None):
        self.directory = Path(model_directory)
        self.factory = provider_factory or (lambda: FasterWhisperSTT(self.directory))
        self.provider = None
        self.model_state = 'available' if (self.directory / 'model.bin').is_file() else 'missing'
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='local-stt')
        self.lock = threading.Lock()
        self.jobs = {}
        self.active = None

    def preload(self):
        try:
            self.model_state='loading'
            self.provider=self.factory()
            # Warm the VAD/decoder on synthetic silence, never microphone audio.
            if hasattr(self.provider, 'preload'):
                self.provider.preload()
            else:
                self.provider.transcribe(Utterance('warmup',Trigger.TOUCH,None,bytes(16000)))
            self.model_state='ready'
        except Exception:
            self.provider=None
            self.model_state='error'

    def submit(self, clip, raw, channel, boost, allow_queue=False, trigger=Trigger.TOUCH, model_id=None, on_done=None):
        with self.lock:
            waiting = sum(j['status'] in ('queued','running','cancelling') for j in self.jobs.values())
            if waiting and not allow_queue:
                raise HTTPException(409, 'The recognizer is busy. Wait for the current job to finish.')
            if waiting>=8:
                raise HTTPException(409,'The transcription queue is full')
            job_id = uuid.uuid4().hex
            job = dict(id=job_id, clip_id=clip['id'] if clip else None, channel=channel, boost=boost,
                       status='queued', text='', cancelled=False,trigger=Trigger(trigger).value,model_id=model_id)
            self.jobs[job_id] = job
            if self.active is None:
                self.active = job_id
            if len(self.jobs) > 100:
                del self.jobs[next(iter(self.jobs))]
            self.pool.submit(self._run, job_id, raw, on_done)
            return dict(job)

    def _run(self, job_id, raw, on_done=None):
        started = time.monotonic()
        try:
            with self.lock:
                job = self.jobs[job_id]
                if job['cancelled']:
                    job['status'] = 'cancelled'
                    return
                self.active = job_id
                job['status'] = 'running'
                channel, boost = job['channel'], job['boost']
            rate, samples = parse_wav(raw)
            pcm, gain = mono_16k(rate, samples, channel, boost)
            model_load_ms = 0
            if self.provider is None:
                with self.lock:
                    self.model_state = 'loading'
                load_start = time.monotonic()
                self.provider = self.factory()
                model_load_ms = round((time.monotonic() - load_start) * 1000)
                with self.lock:
                    self.model_state = 'ready'
            inference_start = time.monotonic()
            transcript = self.provider.transcribe(Utterance(job_id, Trigger(job['trigger']), job['model_id'], pcm))
            inference_ms = round((time.monotonic() - inference_start) * 1000)
            with self.lock:
                job = self.jobs[job_id]
                if job['cancelled']:
                    job['status'] = 'cancelled'
                else:
                    job.update(status='done' if transcript.text else 'no_speech', text=transcript.text,
                               language='en', input_gain=gain, model_load_ms=model_load_ms,
                               inference_ms=inference_ms, elapsed_ms=round((time.monotonic() - started) * 1000))
        except Exception as exc:
            with self.lock:
                job = self.jobs[job_id]
                job.update(status='cancelled' if job['cancelled'] else 'error', error=str(exc))
                if self.provider is None:
                    self.model_state = 'error'
        finally:
            with self.lock:
                self.active = next((key for key,value in self.jobs.items() if value['status']=='queued'),None)
            # Also persist cancellation before a queued job starts. Never hold the recognition lock.
            if on_done:
                try:
                    on_done(self.get(job_id))
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Could not complete transcription follow-up')

    def get(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, 'Transcription job not found')
            return dict(self.jobs[job_id])

    def cancel(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, 'Transcription job not found')
            job = self.jobs[job_id]
            if job['status'] in ('queued', 'running'):
                job['cancelled'] = True
                job['status'] = 'cancelling'
            return dict(job)


class TranscribeRequest(BaseModel):
    channel: int = Field(default=1, ge=1, le=2)
    boost: bool = True


class RecordRequest(BaseModel):
    seconds: float = Field(default=8, ge=0.1, le=15, allow_inf_nan=False)


class ReplayRequest(BaseModel):
    channel: int = Field(default=1, ge=1, le=2)


def create_app(data_directory=None, provider_factory=None, device=None, testing=False,
               state_directory=None, auth_required=None, lan_ip=None, speech=None,voices=None):
    runtime = Path(os.environ.get('ADAM_DATA_DIR', ROOT/'data'))
    models = Path(os.environ.get('ADAM_MODELS_DIR', ROOT/'models'))
    store = ClipStore(Path(data_directory) if data_directory else runtime/'clips')
    model = models/'stt'/'base.en'
    state = State(state_directory or (Path(data_directory)/'.fleet' if data_directory else runtime))
    services = ModelServices(state)
    local_factory = provider_factory or (lambda: FasterWhisperSTT(model))
    jobs = TranscriptionJobs(model, lambda: RoutedTranscriber(services, local_factory))
    bridge = device or DeviceBridge(os.environ.get('VOICE_USB_PORT', 'COM6' if os.name == 'nt' else '/dev/ttyACM0'))
    voices = voices or VoiceService(state.directory/'voices', builtin=models/'voices'/'basic-english'/'1.0.0')
    speech = speech or RoutedSpeech(services, voices)
    fleet = Fleet(state,store,jobs,bridge,f'http://{lan_ip or "127.0.0.1"}:8765',speech,voices,panel_updates=not testing)
    require_auth = not testing if auth_required is None else auth_required

    @asynccontextmanager
    async def lifespan(app):
        if not testing:
            def warm_voice():
                try:
                    fleet.voices.generate('Ready.',fleet.queries.settings().voice_id)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Voice warmup failed; requests may retry')
            await asyncio.gather(asyncio.to_thread(jobs.preload),asyncio.to_thread(warm_voice))
        yield
        await asyncio.to_thread(jobs.pool.shutdown,wait=True,cancel_futures=False)
        fleet.home_assistant.close(); fleet.timers.close()
        fleet.panel.close()
        fleet.conversation.close()
        fleet.queries.close()
        fleet.voices.close()

    app = FastAPI(title='ADAM (Automation Display and Mapping)', version='0.6', lifespan=lifespan,
                  docs_url=None, redoc_url=None)
    app.state.store, app.state.jobs, app.state.device = store, jobs, bridge
    app.state.fleet,app.state.auth = fleet,state
    app.state.voices=fleet.voices
    app.state.model_services=services
    hosts = ['localhost', '127.0.0.1', '[::1]'] + (['testserver'] if testing else [])
    if lan_ip:
        hosts.append(lan_ip)
    if os.environ.get('ADAM_HOSTNAME'):
        hosts.append(os.environ['ADAM_HOSTNAME'])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware('http')
    async def local_origin(request, call_next):
        origin = request.headers.get('origin')
        if request.method in ('POST', 'DELETE', 'PUT', 'PATCH') and origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or parsed.netloc != request.headers.get('host'):
                return JSONResponse({'detail': 'Open the test UI from this local server.'}, status_code=403)
        path = request.url.path
        unit_route = re.fullmatch(r'/api/units/[a-f0-9]{16}/(?:poll|audio|pages/[a-f0-9]{32}/(?:audio|ack))',path)
        public = path=='/api/health' or path.startswith('/api/auth/') or unit_route
        if require_auth and path.startswith('/api/') and not public and not state.authenticated(request.cookies.get('voice_session')):
            return JSONResponse({'detail':'Sign in to the head-end.'},status_code=401)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(ValueError)
    async def bad_clip(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=400)

    @app.exception_handler(KeyError)
    async def missing_clip(request, exc):
        return JSONResponse({'detail': 'Clip not found'}, status_code=404)

    @app.exception_handler(DeviceError)
    async def device_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    @app.get('/api/health')
    def health():
        routing = services.public()
        conversation = fleet.conversation.public()
        return dict(status='ok', local_only=all(p['mode']=='local' for p in routing.values()) and not conversation['enabled'], model='base.en', model_state=jobs.model_state,
                    usb_port=bridge.port, transcription_mode='after_clip', active_job=jobs.active,
                    version='0.6',network='lan' if lan_ip else 'loopback',voice_engine='piper-onnx',
                    weather_provider='open-meteo',speech_local=routing['tts']['mode']=='local',home_assistant_adapter='inventory-v1',
                    model_routing={k:dict(mode=v['mode'],last_provider=v['last_provider']) for k,v in routing.items()},
                    conversation={k:conversation[k] for k in ('enabled','last_provider','remote_model','local_model')})

    @app.get('/api/clips')
    def list_clips():
        return store.list()

    @app.post('/api/clips', status_code=201)
    async def upload_clip(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_UPLOAD:
                raise HTTPException(413, 'Clip exceeds the 4 MiB upload limit')
        name = unquote(request.headers.get('x-filename', 'uploaded-clip.wav'))
        return await asyncio.to_thread(store.add, bytes(raw), name)

    @app.get('/api/clips/{clip_id}')
    def clip_metadata(clip_id: str):
        return store.get(clip_id)[0]

    @app.get('/api/clips/{clip_id}/audio')
    def clip_audio(clip_id: str, channel: int = 0, gain_db: int = 0):
        metadata, raw = store.get(clip_id)
        if channel == 0 and gain_db == 0:
            return Response(raw, media_type='audio/wav',
                            headers={'Content-Disposition': f'inline; filename="{clip_id}.wav"'})
        rate, data = parse_wav(raw)
        if channel not in range(metadata['channels'] + 1) or gain_db not in (0, 12, 24, 30):
            raise ValueError('Invalid channel or review gain')
        if channel:
            data = data[:, channel - 1:channel]
        if gain_db:
            import numpy as np
            data = np.clip(np.rint(data.astype(float) * 10 ** (gain_db / 20)), -32768, 32767).astype('<i2')
        return Response(wav_bytes(rate, data), media_type='audio/wav')

    @app.delete('/api/clips/{clip_id}')
    def delete_clip(clip_id: str):
        store.delete(clip_id)
        return {'deleted': True}

    @app.post('/api/clips/{clip_id}/transcriptions', status_code=202)
    def transcribe(clip_id: str, options: TranscribeRequest):
        metadata, raw = store.get(clip_id)
        if options.channel > metadata['channels']:
            raise ValueError('That microphone channel is not present in this clip')
        return jobs.submit(metadata, raw, options.channel, options.boost)

    @app.get('/api/jobs/{job_id}')
    def job(job_id: str):
        return jobs.get(job_id)

    @app.post('/api/jobs/{job_id}/cancel')
    def cancel_job(job_id: str):
        return jobs.cancel(job_id)

    @app.get('/api/device')
    async def device_status():
        return await asyncio.to_thread(bridge.run, 'status')

    @app.post('/api/device/record')
    async def record(options: RecordRequest):
        ms = round(options.seconds * 1000)
        if ms % 10:
            raise ValueError('Recording length must use 0.01-second increments')
        return await asyncio.to_thread(bridge.run, 'record', ms)

    @app.post('/api/device/stop')
    async def stop():
        return await asyncio.to_thread(bridge.run, 'stop')

    @app.post('/api/device/replay')
    async def replay(options: ReplayRequest):
        return await asyncio.to_thread(bridge.run, 'replay', options.channel)

    @app.post('/api/device/fetch', status_code=201)
    async def fetch():
        raw = await asyncio.to_thread(bridge.run, 'fetch')
        name = 'ESP32 ' + datetime.now().strftime('%H-%M-%S') + '.wav'
        return await asyncio.to_thread(store.add, raw, name, 'device')

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web' / 'dist' / 'index.html')

    @app.get('/bench')
    def bench():
        return FileResponse(ROOT/'web'/'dist'/'bench.html')

    @app.get('/voices')
    def voice_library():
        return FileResponse(ROOT/'web'/'dist'/'voices.html')

    @app.get('/queries')
    def information_settings():
        return FileResponse(ROOT/'web'/'dist'/'queries.html')

    @app.get('/home-assistant')
    def home_assistant_settings():
        return FileResponse(ROOT/'web'/'dist'/'home-assistant.html')

    @app.get('/training')
    async def training_page():
        return FileResponse(ROOT/'web'/'dist'/'training.html')

    @app.get('/models')
    def model_settings():
        return FileResponse(ROOT/'web'/'dist'/'models.html')

    add_model_routes(app,services)
    app.include_router(fleet.router)
    app.mount('/assets', StaticFiles(directory=ROOT / 'web' / 'dist'), name='assets')
    return app


if __name__ == '__main__':
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--lan',action='store_true')
    parser.add_argument('--lan-ip',default=os.environ.get('ADAM_LAN_IP'))
    options = parser.parse_args()
    lan_ip = options.lan_ip
    if options.lan and not lan_ip:
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.connect(('192.0.2.1',9))
            lan_ip = probe.getsockname()[0]
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    print('ADAM (Automation Display and Mapping): http://127.0.0.1:8765', flush=True)
    if lan_ip:
        print(f'Device LAN endpoint: http://{lan_ip}:8765',flush=True)
    uvicorn.run(create_app(lan_ip=lan_ip), host='0.0.0.0' if lan_ip else '127.0.0.1',port=8765,
                access_log=False,limit_concurrency=32,timeout_keep_alive=5)
