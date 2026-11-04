"""Authenticated device streaming and paging protocol v1 (16 kHz mono PCM)."""
import asyncio
from datetime import datetime, timezone
import re
import struct
import time
import uuid

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect
from .clips import MAX_UPLOAD, ClipStorageFull, parse_wav, mono_16k, wav_bytes
from .device import DeviceError
from .session import AudioSession, Trigger
from .voices import VoiceService
from .voice_api import add_voice_routes
from .queries import QueryService, add_query_routes, time_date_intent, weather_intent
from .home_assistant import HomeAssistantService, add_home_assistant_routes
from .panel import PanelService
from .timers import TimerService, add_timer_routes
from .training_archive import InteractionArchive
from .training_api import add_training_routes
from .conversation import ConversationService, add_conversation_routes
from .recording_work import RecordingWork
from .latency import mark
from .intercom import Intercom, add_intercom_routes
from .text_commands import TextCommands
from .status_queries import StatusQueries
from .dialogue import Dialogue
from .command_reply import command_reply
from .context_actions import resolve as resolve_context_action
from .intent_preview import add_intent_preview_routes


class Credentials(BaseModel):
    username: str = Field(min_length=3,max_length=40)
    password: str = Field(min_length=1,max_length=128)


class UnitName(BaseModel):
    name: str = Field(min_length=1,max_length=48)


class PairRequest(UnitName):
    url: str = Field(default='',max_length=100)


class TextPage(BaseModel):
    units: list[str] = Field(min_length=1,max_length=32)
    text: str = Field(min_length=1,max_length=200)
    voice_id: str | None = Field(default=None,min_length=5,max_length=90)


class PageAck(BaseModel):
    status: str
    detail: str = Field(default='',max_length=160)


class Fleet:
    def __init__(self, state, store, jobs, bridge, advertised_url, speech=None,voices=None,panel_updates=True):
        self.state,self.store,self.jobs,self.bridge = state,store,jobs,bridge
        self.url = advertised_url
        self.voices = voices or VoiceService(state.directory/'voices')
        self.speech = speech or self.voices
        self.queries = QueryService(state, self.voices, self.speech, self.playback_pcm)
        self.intercom = Intercom(state, self.queries.deliver)
        self.timers = TimerService(state, self.queries.deliver)
        self.home_assistant = HomeAssistantService(state, self.queries.deliver)
        self.conversation = ConversationService(state, self.queries.deliver)
        self.panel = PanelService(self.queries, enabled=panel_updates)
        self.archive = InteractionArchive(state.directory/'training_archive')
        self.recordings = RecordingWork(self.archive, self.store, self.jobs)
        self.dialogue = Dialogue()
        self.sessions = {}
        self.active = set()
        self.commands = {}
        self.login_attempts = {}
        self.router = APIRouter()
        self.routes()
        self.text_commands = TextCommands(self)
        self.status_queries = StatusQueries(self)
        add_intercom_routes(self.router, self.intercom)
        add_voice_routes(self.router,self.voices)
        add_query_routes(self.router,self.queries)
        add_timer_routes(self.router,self.timers)
        add_home_assistant_routes(self.router,self.home_assistant)
        add_training_routes(self.router,self.archive,self.jobs,self.state)
        add_conversation_routes(self.router,self.conversation)
        add_intent_preview_routes(self.router, self)

    def transcription_done(self, entry, job):
        mark(entry, 'transcribed')
        entry.setdefault('timings_ms', {}).update({k: job[k] for k in ('queue_ms', 'inference_ms') if k in job})
        if job['status'] != 'done' or entry['trigger'] not in ('wake', 'followup'):
            return
        if entry['trigger'] == 'followup' and entry.get('endpoint_reason') == 'limit':
            entry['end_conversation'] = True
            entry['reply'] = command_reply(entry, dict(status='unsupported', text='There was too much continuous audio. Say Hey Adam and try again.'), self.queries.deliver)
            return
        self.dispatch_command(entry, job)

    def dispatch_command(self, entry, job):
        job = self.dialogue.prepare(entry, job)
        if entry.get('end_conversation'):
            self.dialogue.clear_grants(entry.get('unit_id'))
            key = self.status_queries.key(entry)
            with self.status_queries.lock:
                self.status_queries.contexts.pop(key, None)
                if key:
                    self.status_queries.latest[key] = entry.get('id')
            entry['reply'] = command_reply(entry, dict(status='answered', text='Okay.'), self.queries.deliver)
            return
        clarification = resolve_context_action(self, entry, job['text'])
        if clarification:
            self.status_queries.invalidate(entry)
            entry['reply'] = command_reply(entry, clarification, self.queries.deliver)
            return
        if entry.get('context_action'):
            self.status_queries.invalidate(entry)
            self.home_assistant.submit(entry, job)
            return
        if self.status_queries.submit(entry, job):
            return
        if self.intercom.submit(entry, job):
            return
        if (entry.get('trigger') == 'text' and not entry.get('unit_id')
                and self.timers.matches_utterance(job['text'], None)):
            entry['reply'] = dict(status='unsupported', text='Choose a speaker for this timer command. Timers ring on that speaker.')
            return
        if self.timers.submit(entry, job):
            return
        if time_date_intent(job['text']):
            self.queries.submit(entry, job)
            return
        w_intent = weather_intent(job['text'])
        indoor_temp_candidate = (
            w_intent is not None
            and not w_intent.get('unsupported')
            and bool(re.search(r'\btemperature\b', job['text'], re.I))
            and not bool(re.search(r'\b(weather|forecast|rain|raining|snow|snowing|outside|outdoors)\b', job['text'], re.I))
        )
        if w_intent and not indoor_temp_candidate:
            self.queries.submit(entry, job)
        elif self.home_assistant.submit(entry, job):
            return
        elif w_intent:
            self.queries.submit(entry, job)
        else:
            self.conversation.submit(entry, job)
        if entry.get('trigger') == 'text' and entry.get('reply', {}).get('status') == 'ignored':
            entry['reply'] = dict(status='unsupported', text='I could not handle that command. Check the device name or enable conversation in Model services.')

    def require_unit(self, request, unit_id):
        header = request.headers.get('authorization','')
        token = header.removeprefix('Bearer ') if header.startswith('Bearer ') else ''
        if not token or not self.state.unit_auth(unit_id,token):
            raise HTTPException(401,'Invalid device credentials')

    @staticmethod
    async def body(request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>MAX_UPLOAD:
                raise HTTPException(413,'Recording exceeds the upload limit')
        return bytes(raw)

    @staticmethod
    def playback_pcm(pcm):
        values = np.frombuffer(pcm,dtype='<i2').astype(np.float64)
        if not 1600 <= len(values) <= 240000:
            raise ValueError('Use a message between 0.1 and 15 seconds long; shorten longer text')
        peak = float(np.max(np.abs(values)))
        if peak < 2:
            raise ValueError('That recording contains no audible message')
        return np.rint(values*min(8,12000/peak)).astype('<i2').tobytes()

    def routes(self):
        router = self.router

        @router.get('/api/auth/status')
        async def auth_status(request: Request):
            return dict(configured=self.state.configured(),authenticated=self.state.authenticated(request.cookies.get('voice_session')),
                        setup_local=request.client.host in ('127.0.0.1','::1','testclient'))

        @router.post('/api/auth/setup')
        async def setup(request: Request, options: Credentials):
            if request.client.host not in ('127.0.0.1','::1','testclient'):
                raise HTTPException(403,'Create the first account through a local connection or SSH tunnel at 127.0.0.1')
            await asyncio.to_thread(self.state.setup,options.username,options.password)
            return await login(request,options)

        @router.post('/api/auth/login')
        async def login(request: Request, options: Credentials):
            now, ip = time.monotonic(),request.client.host
            attempts = [t for t in self.login_attempts.get(ip,[]) if now-t<60]
            if len(attempts)>=8:
                raise HTTPException(429,'Too many sign-in attempts; wait one minute')
            self.login_attempts[ip] = attempts+[now]
            if len(self.login_attempts)>1000:
                self.login_attempts = {ip:attempts+[now]}
            token = await asyncio.to_thread(self.state.login,options.username,options.password)
            if not token:
                raise HTTPException(401,'Incorrect username or password')
            self.login_attempts.pop(ip,None)
            response = Response(content='{"authenticated":true}',media_type='application/json')
            response.set_cookie('voice_session',token,max_age=43200,httponly=True,samesite='strict',secure=request.url.scheme=='https')
            return response

        @router.post('/api/auth/logout')
        async def logout(request: Request):
            self.state.logout(request.cookies.get('voice_session'))
            response = Response(content='{}',media_type='application/json')
            response.delete_cookie('voice_session')
            return response

        @router.get('/api/fleet')
        async def fleet_status():
            sessions = []
            for item in list(self.sessions.values())[-100:][::-1]:
                entry = dict(item)
                if 'timings_ms' in item:
                    entry['timings_ms'] = dict(item['timings_ms'])
                if entry.get('job_id'):
                    try:
                        job = self.jobs.get(entry['job_id'])
                        entry.update(status=job['status'],text=job.get('text',''),error=job.get('error',''),inference_ms=job.get('inference_ms'))
                    except HTTPException:
                        pass
                sessions.append(entry)
            live_keys = {(r['unit_id'],r['id']) for r in sessions}
            saved = await asyncio.to_thread(self.archive.recent_sessions)
            sessions.extend(r for r in saved if (r['unit_id'],r['id']) not in live_keys)
            sessions = sorted(sessions,key=lambda r:r['started'],reverse=True)[:100]
            archive_status = await asyncio.to_thread(self.archive.status)
            default_voice=await asyncio.to_thread(self.voices.registry.default)
            return dict(units=self.state.units(),pages=self.state.pages(),sessions=sessions,
                        intercom=self.intercom.public(),
                        timers=self.timers.list_timers(),
                        url=self.url,protocol=1,wake_requirement='Hey Adam on device',default_voice=default_voice,
                        training_archive=archive_status)

        @router.post('/api/units',status_code=201)
        async def add_unit(options: UnitName):
            return self.state.add_unit(options.name)

        @router.patch('/api/units/{unit_id}')
        async def rename(unit_id: str,options: UnitName):
            self.state.rename(unit_id,options.name)
            return {'renamed':True}

        @router.post('/api/units/pair',status_code=201)
        async def pair(options: PairRequest):
            url = options.url or self.url
            if not re.fullmatch(r'http://(?:\d{1,3}\.){3}\d{1,3}:8765',url) or url.startswith('http://127.'):
                raise ValueError('Use the head-end LAN address, for example http://192.168.1.50:8765')
            unit = self.state.add_unit(options.name)
            try:
                await asyncio.to_thread(self.bridge.run,'provision',dict(url=url,**unit))
            except Exception as exc:
                # A failed USB command must not leave a phantom registered unit.
                with self.state.db() as db:
                    db.execute('DELETE FROM units WHERE id=?',(unit['id'],))
                if isinstance(exc, DeviceError):
                    # The browser's USB ports are unrelated to this server's
                    # serial bridge. Do not echo device replies or credentials.
                    raise DeviceError(
                        'USB pairing failed on the head-end computer. Connect the unit to the '
                        'computer running ADAM (the Pi for a Pi-hosted page), enable its USB '
                        'access, and close other serial tools. The unit must already run ADAM '
                        'firmware; this button cannot flash firmware or access USB on another laptop.'
                    ) from None
                raise
            return dict(id=unit['id'],name=unit['name'],paired=True)

        @router.post('/api/units/{unit_id}/listen')
        async def listen(unit_id: str):
            if not any(u['id']==unit_id and u['online'] for u in self.state.units()):
                raise HTTPException(409,'Device is offline')
            if unit_id in self.active or self.intercom.current(unit_id):
                raise HTTPException(409,'Device is already listening')
            self.commands[unit_id] = dict(type='listen',expires=time.time()+15)
            return {'requested':True}

        @router.post('/api/pages/text',status_code=201)
        async def text_page(options: TextPage):
            if options.voice_id:
                pcm = await asyncio.to_thread(self.voices.synthesize,options.text,options.voice_id)
            else:
                pcm = await asyncio.to_thread(self.speech.synthesize,options.text)
            return self.state.enqueue(options.units,self.playback_pcm(pcm),'text',options.text)

        @router.post('/api/pages/voice',status_code=201)
        async def voice_page(request: Request):
            units = request.headers.get('x-units','').split(',')
            rate,samples = parse_wav(await self.body(request))
            pcm = mono_16k(rate,samples,1)[0]
            return self.state.enqueue(units,self.playback_pcm(pcm),'voice')

        @router.post('/api/pages/{page_id}/cancel')
        async def cancel(page_id: str):
            self.state.cancel_page(page_id)
            return {'cancelled':True}

        @router.get('/api/units/{unit_id}/poll')
        async def poll(request: Request,unit_id: str,firmware: str='',wake_model: str='unavailable',busy: bool=False,dismissed: str='',intercom: bool=False,followup: bool=False,followup_version: int=1):
            self.require_unit(request,unit_id)
            self.state.heartbeat(unit_id,firmware,wake_model)
            self.intercom.heartbeat(unit_id, intercom, busy)
            with self.state.db() as db:
                unit_name = db.execute('SELECT name FROM units WHERE id=?', (unit_id,)).fetchone()['name']
            panel = self.panel.snapshot(unit_name, unit_id)
            panel['llm_provider'] = self.conversation.panel_provider()
            panel['timers'] = self.timers.reconcile_poll(unit_id, dismissed)
            if busy:
                return dict(type='idle', panel=panel)
            call = self.intercom.offer(unit_id)
            if call:
                return dict(call, panel=panel)
            command = self.commands.pop(unit_id,None)
            if command and command['expires']>time.time():
                return dict(command, panel=panel)
            offer = self.state.offer(unit_id) or {'type':'idle'}
            if followup and offer['type'] == 'page' and self.conversation.options.followup_listening:
                entry = next((e for e in list(self.sessions.values()) if e['unit_id'] == unit_id
                    and e.get('reply', {}).get('page_id') == offer['id']), None)
                seconds = self.dialogue.offer(unit_id, offer['id'], entry, adaptive=followup_version>=2) if entry else 0
                if seconds:
                    offer['followup_seconds'] = seconds
                    offer['interruptible'] = followup_version >= 2 and self.conversation.options.interrupt_replies
            return dict(offer, panel=panel)

        @router.get('/api/units/{unit_id}/pages/{page_id}/audio')
        async def page_audio(request: Request,unit_id: str,page_id: str):
            self.require_unit(request,unit_id)
            return Response(self.state.page_audio(unit_id,page_id),media_type='application/octet-stream')

        @router.post('/api/units/{unit_id}/pages/{page_id}/ack')
        async def ack(request: Request,unit_id: str,page_id: str,options: PageAck):
            self.require_unit(request,unit_id)
            self.state.ack(unit_id,page_id,options.status,options.detail)
            self.dialogue.ack(unit_id, page_id, options.status)
            for entry in list(self.sessions.values()):
                if entry['unit_id'] == unit_id and entry.get('reply', {}).get('page_id') == page_id:
                    mark(entry, 'page_' + options.status)
            return {'accepted':True}

        @router.post('/api/units/{unit_id}/audio',status_code=202)
        async def audio(request: Request,unit_id: str):
            self.require_unit(request,unit_id)
            if unit_id in self.active or len(self.active)>=4 or self.intercom.current(unit_id):
                raise HTTPException(409,'Audio receiver is busy')
            session_id = request.headers.get('x-session-id','')
            if session_id in self.sessions or self.home_assistant.seen(unit_id, session_id) or await asyncio.to_thread(self.archive.seen,unit_id,session_id):
                raise HTTPException(409,'Session identifier already used')
            try:
                frames = int(request.headers.get('x-audio-frames','0'))
            except ValueError:
                raise ValueError('Invalid audio frame count')
            if not 5<=frames<=750:
                raise ValueError('Audio sessions must contain 0.1–15 seconds in 20 ms frames')
            try:preroll_ms=int(request.headers.get('x-audio-preroll-ms','0'))
            except ValueError:raise ValueError('Invalid audio pre-roll duration')
            if not 0<=preroll_ms<=2000 or preroll_ms%20 or preroll_ms>=frames*20:
                raise ValueError('Pre-roll must be 0–2000 ms in complete 20 ms frames, followed by live audio')
            session = AudioSession(session_id,request.headers.get('x-trigger','touch'),request.headers.get('x-wake-model'))
            mode=request.headers.get('x-audio-mode','fixed')
            if mode not in ('fixed','silence-v1') or (mode=='silence-v1' and session.trigger not in (Trigger.WAKE, Trigger.FOLLOWUP)):
                raise ValueError('Invalid audio endpoint mode')
            automatic=mode=='silence-v1'
            # The durable duplicate lookup above yields; reserve again without a yield
            # so concurrent uploads cannot both claim the same device or fifth slot.
            if unit_id in self.active or len(self.active)>=4 or self.intercom.current(unit_id):
                raise HTTPException(409,'Audio receiver is busy')
            followup_info = {}
            if session.trigger == Trigger.FOLLOWUP:
                if not self.conversation.options.followup_listening:
                    raise ValueError('Follow-up listening is disabled')
                followup_info = self.dialogue.consume(unit_id, request.headers.get('x-followup-page', ''))
            else:
                self.dialogue.clear_grants(unit_id)
            self.active.add(unit_id)
            entry = dict(id=session_id,unit_id=unit_id,trigger=session.trigger.value,model_id=session.model_id,
                status='listening',received=0,expected=frames*640,started=time.time(),text='',preroll_ms=preroll_ms,
                endpoint_mode=mode, **followup_info)
            self.sessions[session_id] = entry
            if len(self.sessions)>100:
                del self.sessions[next(k for k,v in self.sessions.items() if v['status']!='listening')]
            buffered = bytearray()
            sequence = 0
            ended=False
            try:
                unit = next(u for u in self.state.units() if u['id']==unit_id)
                entry.update(device_name=unit['name'],firmware=unit['firmware'],installed_model=unit['wake_model'],
                             collection_tags=await asyncio.to_thread(self.archive.capture_context,unit_id))
                async with asyncio.timeout(20):
                    async for chunk in request.stream():
                        buffered.extend(chunk)
                        if len(buffered)+sequence*644>(frames+int(automatic))*644:
                            raise ValueError('Audio body exceeds declared length')
                        while len(buffered)>=644:
                            packet = bytes(buffered[:644]); del buffered[:644]
                            if ended:
                                raise ValueError('Audio after end record')
                            if automatic and struct.unpack_from('<I',packet)[0]==0xffffffff:
                                actual=struct.unpack_from('<I',packet,4)[0]
                                reason=packet[8]
                                if actual!=sequence or not preroll_ms//20+50<=actual<=frames or reason not in (1,2,3) or any(packet[9:]):
                                    raise ValueError('Invalid audio end record')
                                ended=True
                                entry.update(expected=sequence*640,endpoint_reason={1:'silence',2:'limit',3:'no_activity'}[reason])
                                continue
                            session.append(struct.unpack_from('<I',packet)[0],packet[4:])
                            sequence += 1
                            if sequence>frames:
                                raise ValueError('Too many audio frames')
                            entry['received'] = session.buffered_bytes
                if buffered or (not ended if automatic else sequence!=frames):
                    raise ValueError('Incomplete audio session')
                entry.update(capture_ms=sequence*20-preroll_ms,upload_ms=round((time.time()-entry['started'])*1000),
                             processing_started_monotonic=time.monotonic(), timings_ms={})
                utterance = session.finish()
                name = 'Wi-Fi ' + datetime.now(timezone.utc).strftime('%H-%M-%S') + '.wav'
                raw = wav_bytes(16000,np.frombuffer(utterance.pcm,dtype='<i2'))
                entry.update(recording_status='pending', clip_id=None, archive_id=None, archive_warning='')
                work = self.recordings.reserve(entry)
                def completed(result):
                    try:
                        self.transcription_done(entry, result)
                    finally:
                        self.recordings.completed(work, entry, result)
                try:
                    mark(entry, 'stt_submitted')
                    job = self.jobs.submit(None,raw,1,True,
                                       allow_queue=True,trigger=utterance.trigger,model_id=utterance.model_id,
                                       on_done=completed)
                except Exception as exc:
                    if work is not None:
                        # Retain the original for diagnostic retranscription even
                        # when the bounded recognition queue rejects this session.
                        self.recordings.completed(work, entry, dict(status='error', error='Transcription was not queued: '+str(exc)[:160]))
                        saved = self.recordings.pool.submit(self.recordings.save, work, raw, name, entry, None)
                        await asyncio.wrap_future(saved)
                    raise
                entry.update(status='queued',job_id=job['id'])
                if work is not None:
                    saved = self.recordings.pool.submit(self.recordings.save, work, raw, name, entry, job['id'])
                    # Older diagnostic clients expect recording IDs in the response.
                    # v29 speakers opt in to immediate acceptance so they can poll.
                    if request.headers.get('x-recording-async') != '1':
                        await asyncio.wrap_future(saved)
                return dict(session_id=session_id,clip_id=entry.get('clip_id'),job_id=job['id'],archive_id=entry.get('archive_id'),
                            recording_status=entry['recording_status'], archive_warning=entry.get('archive_warning',''))
            except ClientDisconnect:
                session.cancel()
                entry.update(status='aborted',error='Device cancelled or disconnected')
                return Response(status_code=499)
            except (Exception,asyncio.CancelledError) as exc:
                session.cancel()
                entry.update(status='aborted',error='Connection lost' if isinstance(exc,ClientDisconnect) else str(exc)[:160])
                if entry.get('archive_id') and not entry.get('job_id'):
                    await asyncio.to_thread(self.archive.complete,entry['archive_id'],dict(status='error',error='Transcription was not queued: '+str(exc)[:160]))
                if isinstance(exc,TimeoutError):
                    raise HTTPException(408,'Audio session timed out') from exc
                raise
            finally:
                self.active.discard(unit_id)
