"""Durable original interaction WAVs and human review, independent of diagnostic clips."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .clips import parse_wav

LOG = logging.getLogger(__name__)
LABELS = ('unreviewed', 'positive', 'negative', 'discard')
METADATA_RESERVE = 65536


class ArchiveSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = True
    max_bytes: int = Field(default=10*1024**3, ge=1024**2, le=1024**4)
    min_free_bytes: int = Field(default=1024**3, ge=0, le=1024**4)
    max_records: int = Field(default=100000, ge=1, le=1000000)


class CollectionTags(BaseModel):
    model_config = ConfigDict(extra='forbid')
    room: str = Field(default='', max_length=80)
    speaker: str = Field(default='', max_length=80)
    distance: str = Field(default='', max_length=80)
    collection_session: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def clean(self):
        for key in ('room', 'speaker', 'distance', 'collection_session'):
            value = getattr(self, key).strip()
            if any(ord(c)<32 for c in value):
                raise ValueError('Tags cannot contain control characters')
            setattr(self, key, value)
        return self


class Review(CollectionTags):
    label: Literal['unreviewed', 'positive', 'negative', 'discard'] = 'unreviewed'
    phrase_start_ms: int | None = Field(default=None, ge=0, le=15000)
    phrase_end_ms: int | None = Field(default=None, ge=1, le=15000)
    cue_status: Literal['unchecked', 'excluded', 'overlap'] = 'unchecked'
    notes: str = Field(default='', max_length=2000)
    revision: int = Field(ge=0)


class ArchiveUnavailable(RuntimeError):
    pass


class ReviewConflict(ValueError):
    pass


class InteractionArchive:
    """Fail-open recording boundary. Review/settings operations report errors normally.

    A committed pending row precedes the atomic WAV rename. Startup reconciles
    interrupted writes and marks unfinished transcriptions for explicit retry.
    Nothing is automatically labelled positive, deleted, retrained or installed.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory/'archive.sqlite3'
        self.audio_directory = self.directory/'audio'
        self.lock = threading.RLock()
        self.ready = False
        self.last_warning = ''
        self.last_attempt = 0
        self._initialize()

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=.25)
            db.row_factory = sqlite3.Row
            try:
                db.execute('PRAGMA synchronous=FULL')
                with db:
                    yield db
            finally:
                db.close()

    def _warning(self, message, exc=None):
        self.last_warning = message
        LOG.warning('%s%s', message, ': '+str(exc) if exc else '')
        return message

    def _initialize(self):
        with self.lock:
            self.last_attempt = time.monotonic()
            try:
                self.audio_directory.mkdir(parents=True, exist_ok=True)
                with self.db() as db:
                    db.executescript('''
                        CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS contexts (unit_id TEXT PRIMARY KEY, data TEXT NOT NULL);
                        CREATE TABLE IF NOT EXISTS interactions (
                            id TEXT PRIMARY KEY, unit_id TEXT NOT NULL, session_id TEXT NOT NULL,
                            device_name TEXT NOT NULL, room TEXT NOT NULL, recorded_at REAL NOT NULL,
                            trigger TEXT NOT NULL, wake_model_id TEXT, firmware TEXT NOT NULL,
                            preroll_ms INTEGER NOT NULL, duration_ms INTEGER NOT NULL, capture_ms INTEGER NOT NULL,
                            endpoint_mode TEXT NOT NULL, endpoint_reason TEXT NOT NULL,
                            sha256 TEXT NOT NULL, audio_bytes INTEGER NOT NULL DEFAULT 0,
                            audio_status TEXT NOT NULL, archive_error TEXT NOT NULL DEFAULT '',
                            transcript TEXT NOT NULL DEFAULT '', transcription_status TEXT NOT NULL DEFAULT 'pending',
                            transcription_error TEXT NOT NULL DEFAULT '', job_id TEXT,
                            transcription_details TEXT NOT NULL DEFAULT '{}',
                            label TEXT NOT NULL DEFAULT 'unreviewed', phrase_start_ms INTEGER, phrase_end_ms INTEGER,
                            cue_status TEXT NOT NULL DEFAULT 'unchecked', speaker TEXT NOT NULL DEFAULT '',
                            distance TEXT NOT NULL DEFAULT '', collection_session TEXT NOT NULL DEFAULT '',
                            notes TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 0, reviewed_at REAL,
                            UNIQUE(unit_id,session_id)
                        );
                        CREATE INDEX IF NOT EXISTS interactions_recent ON interactions(recorded_at DESC,id);
                        CREATE INDEX IF NOT EXISTS interactions_label ON interactions(label,recorded_at DESC);
                    ''')
                    for row in db.execute("SELECT id,sha256 FROM interactions WHERE audio_status='pending'").fetchall():
                        path = self._audio_path(row['id'])
                        partial = path.with_suffix('.part')
                        if not path.exists() and partial.is_file():
                            candidate = partial.read_bytes()
                            if hashlib.sha256(candidate).hexdigest()==row['sha256']:
                                os.replace(partial,path)
                        raw = path.read_bytes() if path.is_file() else b''
                        valid = bool(raw) and hashlib.sha256(raw).hexdigest()==row['sha256']
                        db.execute('UPDATE interactions SET audio_status=?,audio_bytes=?,archive_error=? WHERE id=?',
                                   ('ready' if valid else 'missing',len(raw),'' if valid else 'Audio write interrupted; original is unavailable.',row['id']))
                    db.execute("UPDATE interactions SET transcription_status='interrupted',transcription_error='Head-end restarted before transcription finished. Retry from review.' WHERE transcription_status IN ('pending','queued','running','cancelling')")
                # Include abandoned, uncommitted files in the limit; never silently delete recordings.
                self.untracked_bytes = self._untracked_size()
                self.ready = True
                self.last_warning = ''
            except Exception as exc:
                self.ready = False
                self._warning('Training archive is unavailable. Voice processing continues.', exc)

    def _ensure(self):
        if not self.ready and time.monotonic()-self.last_attempt>30:
            self._initialize()
        if not self.ready:
            raise ArchiveUnavailable(self.last_warning or 'Training archive is unavailable')

    def _audio_path(self, record_id):
        if not re.fullmatch(r'[a-f0-9]{32}', record_id):
            raise KeyError('Recording not found')
        return self.audio_directory/record_id[:2]/(record_id+'.wav')

    def _untracked_size(self):
        with self.db() as db:
            known = {row['id'] for row in db.execute('SELECT id FROM interactions')}
        return sum(p.stat().st_size for p in self.audio_directory.rglob('*') if p.is_file()
                   and (p.suffix!='.wav' or p.stem not in known))

    def _usage(self, db):
        row = db.execute('SELECT count(*) AS count,coalesce(sum(audio_bytes),0) AS bytes FROM interactions').fetchone()
        metadata = sum(p.stat().st_size for p in self.directory.glob('archive.sqlite3*') if p.is_file())
        return row['count'], row['bytes']+metadata+getattr(self,'untracked_bytes',0)

    def settings(self):
        self._ensure()
        with self.db() as db:
            row = db.execute('SELECT data FROM settings WHERE id=1').fetchone()
        return ArchiveSettings.model_validate_json(row[0]) if row else ArchiveSettings()

    def save_settings(self, options):
        self._ensure()
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO settings VALUES(1,?)',(options.model_dump_json(),))
        return options

    def context(self, unit_id):
        self._ensure()
        with self.db() as db:
            row = db.execute('SELECT data FROM contexts WHERE unit_id=?',(unit_id,)).fetchone()
        return CollectionTags.model_validate_json(row[0]) if row else CollectionTags()

    def capture_context(self, unit_id):
        try:
            return self.context(unit_id).model_dump()
        except Exception as exc:
            self._warning('Training collection tags could not be loaded. Voice processing continues.', exc)
            return CollectionTags().model_dump()

    def seen(self, unit_id, session_id):
        try:
            self._ensure()
            with self.db() as db:
                return db.execute('SELECT 1 FROM interactions WHERE unit_id=? AND session_id=?',
                                  (unit_id,session_id)).fetchone() is not None
        except Exception:
            # Duplicate checking must not turn archive storage faults into voice failures.
            return False

    def save_context(self, unit_id, tags):
        self._ensure()
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO contexts VALUES(?,?)',(unit_id,tags.model_dump_json()))
        return tags

    def status(self):
        try:
            options = self.settings()
            with self.db() as db:
                count, used = self._usage(db)
                labels = {label:0 for label in LABELS}
                labels.update({r['label']:r['n'] for r in db.execute('SELECT label,count(*) AS n FROM interactions GROUP BY label')})
                missing = db.execute("SELECT count(*) FROM interactions WHERE audio_status!='ready'").fetchone()[0]
            free = shutil.disk_usage(self.directory).free
            full = count>=options.max_records or used+METADATA_RESERVE>=options.max_bytes or free<options.min_free_bytes+METADATA_RESERVE
            return dict(ready=True,settings=options.model_dump(),records=count,used_bytes=used,free_bytes=free,
                        labels=labels,missing_audio=missing,full=full,warning=self.last_warning,
                        directory=str(self.directory.resolve()))
        except Exception as exc:
            return dict(ready=False,warning=self.last_warning or 'Training archive is unavailable.',records=0,
                        labels={label:0 for label in LABELS},settings=ArchiveSettings().model_dump())

    def capture(self, raw, entry):
        """Never raise into voice processing. raw is the original, complete mono WAV."""
        try:
            self._ensure()
            with self.lock:
                options = self.settings()
                if not options.enabled:
                    return dict(id=None,warning='',status='disabled')
                rate, samples = parse_wav(raw)
                if rate!=16000 or samples.shape[1]!=1:
                    raise ValueError('Training interactions must be original 16 kHz mono PCM')
                duration = len(samples)*1000//rate
                with self.db() as db:
                    previous = db.execute('SELECT id,audio_status,archive_error FROM interactions WHERE unit_id=? AND session_id=?',
                                          (entry['unit_id'],entry['id'])).fetchone()
                    if previous:
                        return dict(id=previous['id'],status=previous['audio_status'],warning=previous['archive_error'])
                    count, used = self._usage(db)
                    free = shutil.disk_usage(self.directory).free
                    if count>=options.max_records or used+METADATA_RESERVE>options.max_bytes or free<options.min_free_bytes+METADATA_RESERVE:
                        return dict(id=None,status='full',warning=self._warning('Training archive limit or disk reserve reached. Recording not archived; voice processing continues.'))
                    can_save = used+len(raw)+METADATA_RESERVE<=options.max_bytes and free>=options.min_free_bytes+len(raw)+METADATA_RESERVE
                    warning = '' if can_save else 'Training audio budget or disk reserve reached. Metadata retained; voice processing continues.'
                    tags = CollectionTags.model_validate(entry.get('collection_tags',{}))
                    record = dict(id=uuid.uuid4().hex,unit_id=entry['unit_id'],session_id=entry['id'],
                        device_name=entry.get('device_name','Unknown unit'),room=tags.room or entry.get('device_name',''),
                        recorded_at=entry['started'],trigger=entry['trigger'],wake_model_id=entry.get('model_id') or entry.get('installed_model'),
                        firmware=entry.get('firmware',''),preroll_ms=entry['preroll_ms'],duration_ms=duration,
                        capture_ms=duration-entry['preroll_ms'],endpoint_mode=entry.get('endpoint_mode','fixed'),
                        endpoint_reason=entry.get('endpoint_reason',''),sha256=hashlib.sha256(raw).hexdigest(),
                        audio_bytes=len(raw) if can_save else 0,audio_status='pending' if can_save else 'not_saved',
                        archive_error=warning,speaker=tags.speaker,distance=tags.distance,collection_session=tags.collection_session)
                    db.execute('INSERT INTO interactions ('+','.join(record)+') VALUES ('+','.join('?' for _ in record)+')',tuple(record.values()))
                if can_save:
                    path = self._audio_path(record['id'])
                    temporary = path.with_suffix('.part')
                    try:
                        path.parent.mkdir(parents=True,exist_ok=True)
                        with temporary.open('xb') as target:
                            target.write(raw);target.flush();os.fsync(target.fileno())
                        os.replace(temporary,path)
                        with self.db() as db:
                            db.execute("UPDATE interactions SET audio_status='ready' WHERE id=?",(record['id'],))
                    except Exception as exc:
                        # Keep any original/partial file for recovery; count it against the limit.
                        with self.db() as db:
                            db.execute("UPDATE interactions SET archive_error=? WHERE id=?",('Audio write failed; check storage and restart to reconcile.',record['id']))
                        return dict(id=record['id'],status='error',warning=self._warning('Training audio could not be saved. Voice processing continues.',exc))
                if warning:
                    self._warning(warning)
                else:
                    self.last_warning = ''
                return dict(id=record['id'],status='ready' if can_save else 'not_saved',warning=warning)
        except Exception as exc:
            return dict(id=None,status='error',warning=self._warning('Training archive write failed. Voice processing continues.',exc))

    def attach_job(self, record_id, job_id):
        if not record_id:
            return ''
        try:
            with self.db() as db:
                db.execute("UPDATE interactions SET job_id=?,transcription_status=CASE WHEN transcription_status='pending' THEN 'queued' ELSE transcription_status END WHERE id=? AND (job_id IS NULL OR job_id=?)",(job_id,record_id,job_id))
            return ''
        except Exception as exc:
            return self._warning('Training transcription tracking could not be saved. Voice processing continues.',exc)

    def complete(self, record_id, job):
        if not record_id:
            return ''
        try:
            details = {key:job[key] for key in ('input_gain','inference_ms','elapsed_ms','model_load_ms','language','boost','channel') if key in job}
            with self.db() as db:
                db.execute('UPDATE interactions SET transcript=?,transcription_status=?,transcription_error=?,transcription_details=?,job_id=coalesce(?,job_id) WHERE id=? AND (? IS NULL OR job_id IS NULL OR job_id=?)',
                           (job.get('text','')[:20000],job['status'],job.get('error','')[:2000],json.dumps(details),job.get('id'),record_id,job.get('id'),job.get('id')))
            return ''
        except Exception as exc:
            return self._warning('Training transcript could not be saved. Voice processing continues.',exc)

    @staticmethod
    def _record(row):
        result = dict(row)
        result['transcription_details'] = json.loads(result['transcription_details'])
        result.update(sample_rate=16000,channels=1,bits=16,timestamp_source='headend_upload_start',
                      recorded_iso=datetime.fromtimestamp(result['recorded_at'],timezone.utc).isoformat())
        return result

    def get(self, record_id):
        self._ensure();self._audio_path(record_id)
        with self.db() as db:
            row = db.execute('SELECT * FROM interactions WHERE id=?',(record_id,)).fetchone()
        if not row:
            raise KeyError('Recording not found')
        return self._record(row)

    def audio(self, record_id):
        record = self.get(record_id)
        if record['audio_status']!='ready':
            raise ArchiveUnavailable('The original audio is unavailable for this interaction')
        raw = self._audio_path(record_id).read_bytes()
        if len(raw)!=record['audio_bytes'] or hashlib.sha256(raw).hexdigest()!=record['sha256']:
            raise ArchiveUnavailable('Original audio failed its integrity check; preserve it for investigation')
        return record,raw

    def list(self, label='', unit_id='', query='', limit=50, offset=0):
        self._ensure()
        if label and label not in LABELS:
            raise ValueError('Unknown review label')
        clauses, args = [], []
        for key,value in (('label',label),('unit_id',unit_id)):
            if value:
                clauses.append(key+'=?');args.append(value)
        if query:
            clauses.append('(transcript LIKE ? OR collection_session LIKE ? OR speaker LIKE ? OR device_name LIKE ?)')
            args += ['%'+query[:100]+'%']*4
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.db() as db:
            total = db.execute('SELECT count(*) FROM interactions'+where,args).fetchone()[0]
            rows = db.execute('SELECT * FROM interactions'+where+' ORDER BY recorded_at DESC,id DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        return dict(records=[self._record(row) for row in rows],total=total,limit=limit,offset=offset)

    def review(self, record_id, review):
        with self.lock:
            record = self.get(record_id)
            if record['revision']!=review.revision:
                raise ReviewConflict('This review changed in another tab. Reload it before saving.')
            start,end = review.phrase_start_ms,review.phrase_end_ms
            if (start is None)!=(end is None) or (start is not None and not 0<=start<end<=record['duration_ms']):
                raise ValueError('Phrase boundaries must be inside the recording, with start before end')
            if review.label in ('positive','negative') and record['audio_status']!='ready':
                raise ValueError('Audio is required to approve a positive or negative')
            if review.label in ('positive','negative'):
                self.audio(record_id)  # An approval must never silently accept a corrupted original.
            if review.label=='positive' and (start is None or review.cue_status=='unchecked'):
                raise ValueError('Mark the wake phrase boundaries and check the listening cue before approving a positive')
            values = review.model_dump(exclude={'revision'})
            values.update(revision=review.revision+1,reviewed_at=time.time())
            with self.db() as db:
                db.execute('UPDATE interactions SET '+','.join(key+'=?' for key in values)+' WHERE id=?',(*values.values(),record_id))
            return self.get(record_id)

    def recent_sessions(self):
        try:
            return [dict(id=r['session_id'],unit_id=r['unit_id'],device_name=r['device_name'],
                         trigger=r['trigger'],model_id=r['wake_model_id'],started=r['recorded_at'],
                         status=r['transcription_status'],text=r['transcript'],error=r['transcription_error'],
                         preroll_ms=r['preroll_ms'],capture_ms=r['capture_ms'],endpoint_reason=r['endpoint_reason'],
                         received=max(0,r['audio_bytes']-44),expected=r['duration_ms']*32,
                         clip_id=None,archive_id=r['id'],archive_status=r['audio_status'],archive_warning=r['archive_error'])
                    for r in self.list(limit=100)['records']]
        except Exception:
            return []
