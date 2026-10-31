"""Administrator-only collection and review routes (protected by server middleware)."""
from contextlib import contextmanager
import sqlite3

import numpy as np
from fastapi import HTTPException, Query
from fastapi.responses import Response

from .clips import parse_wav, wav_bytes
from .training_archive import ArchiveSettings, ArchiveUnavailable, CollectionTags, Review, ReviewConflict


@contextmanager
def archive_errors():
    try:
        yield
    except KeyError as exc:
        raise HTTPException(404, 'Recording not found') from exc
    except ReviewConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ArchiveUnavailable, OSError, sqlite3.Error) as exc:
        raise HTTPException(503, 'Archive storage unavailable. Check disk space and the head-end log. '+str(exc)[:200]) from exc


def add_training_routes(router, archive, jobs, state):
    @router.get('/api/training/status')
    def status():
        return archive.status()

    @router.put('/api/training/settings')
    def settings(options: ArchiveSettings):
        with archive_errors():
            return archive.save_settings(options)

    def require_unit(unit_id):
        if not any(u['id']==unit_id for u in state.units()):
            raise HTTPException(404, 'Device not found')

    @router.get('/api/training/contexts/{unit_id}')
    def context(unit_id: str):
        require_unit(unit_id)
        with archive_errors():
            return archive.context(unit_id)

    @router.put('/api/training/contexts/{unit_id}')
    def save_context(unit_id: str, tags: CollectionTags):
        require_unit(unit_id)
        with archive_errors():
            return archive.save_context(unit_id, tags)

    @router.get('/api/training/recordings')
    def recordings(label: str='', unit_id: str='', q: str=Query('',max_length=100),
                   limit: int=Query(50,ge=1,le=100), offset: int=Query(0,ge=0)):
        with archive_errors():
            return archive.list(label,unit_id,q,limit,offset)

    @router.get('/api/training/recordings/{record_id}')
    def recording(record_id: str):
        with archive_errors():
            return archive.get(record_id)

    @router.put('/api/training/recordings/{record_id}/review')
    def review(record_id: str, options: Review):
        with archive_errors():
            return archive.review(record_id,options)

    @router.get('/api/training/recordings/{record_id}/audio')
    def audio(record_id: str, gain_db: int=0):
        if gain_db not in (0,12,24):
            raise HTTPException(422, 'Preview gain must be 0, 12 or 24 dB')
        with archive_errors():
            record,raw = archive.audio(record_id)
            if gain_db:
                rate,samples = parse_wav(raw)
                raw = wav_bytes(rate,np.clip(np.rint(samples.astype(np.float64)*10**(gain_db/20)),-32768,32767).astype('<i2'))
            return Response(raw,media_type='audio/wav',headers={
                'Content-Disposition': f'inline; filename="{record_id}{"-preview" if gain_db else "-original"}.wav"',
                'X-Original-SHA256': record['sha256'],
            })

    @router.get('/api/training/recordings/{record_id}/waveform')
    def waveform(record_id: str):
        with archive_errors():
            record,raw = archive.audio(record_id)
            _,samples = parse_wav(raw)
            samples = samples[:,0].astype(np.float64)
            peak = float(np.max(np.abs(samples)))
            bins = np.array_split(samples,min(600,len(samples)))
            return dict(duration_ms=record['duration_ms'],preroll_ms=record['preroll_ms'],
                        peak=peak/32768,peaks=[float(np.max(np.abs(b)))/max(1,peak) for b in bins])

    @router.post('/api/training/recordings/{record_id}/transcribe',status_code=202)
    def retry(record_id: str):
        # The archive lock also holds completion callbacks until the new job is attached.
        # Review transcription never dispatches Home Assistant actions or spoken replies.
        with archive_errors(), archive.lock:
            record,raw = archive.audio(record_id)
            if record['transcription_status']=='pending':
                raise HTTPException(409, 'The live interaction is still being queued')
            if record['job_id']:
                try:
                    current = jobs.get(record['job_id'])
                except HTTPException:
                    current = None
                if current and current['status'] in ('queued','running','cancelling'):
                    raise HTTPException(409, 'This recording is already being transcribed')
            job = jobs.submit(None,raw,1,True,allow_queue=True,trigger=record['trigger'],
                              model_id=record['wake_model_id'],on_done=lambda result: archive.complete(record_id,result))
            with archive.db() as db:
                db.execute("UPDATE interactions SET job_id=?,transcription_status='queued',transcription_error='' WHERE id=?",(job['id'],record_id))
            return dict(job_id=job['id'])
