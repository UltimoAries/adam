"""Durability, original-audio integrity, review rules and fail-open voice integration."""
import hashlib
import shutil
import struct
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np
from fastapi import HTTPException

from . import test_fleet
from .clips import ClipStorageFull, parse_wav, wav_bytes
from .stt import Transcript
from .training_archive import InteractionArchive, METADATA_RESERVE


class TrainingArchiveTests(unittest.TestCase):
    setUp = test_fleet.FleetTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    headers = test_fleet.FleetTests.headers
    stream = test_fleet.FleetTests.stream
    poll = test_fleet.FleetTests.poll

    @property
    def archive(self):
        return self.app.state.fleet.archive

    def upload(self, **extra):
        # Quiet ramp, with distinctive pre-trigger samples. STT boosts this; archival must not.
        samples = np.tile(np.arange(-160,160,dtype='<i2'),50)
        body = b''.join(struct.pack('<I',n)+samples[n*320:(n+1)*320].tobytes() for n in range(50))
        response = self.stream(body,**{'X-Audio-Frames':'50','X-Audio-Preroll-Ms':'200',**extra})
        self.assertEqual(response.status_code,202,response.text)
        return response.json(),wav_bytes(16000,samples)

    def completed(self, record_id):
        for _ in range(300):
            record = self.archive.get(record_id)
            if record['transcription_status'] not in ('pending','queued','running','cancelling'):
                return record
            time.sleep(.01)
        self.fail('Archive transcription callback did not finish')

    def review_data(self, **changes):
        return dict(revision=0,label='positive',phrase_start_ms=20,phrase_end_ms=180,cue_status='excluded',**changes)

    def test_original_full_audio_and_persistent_transcript_and_context(self):
        observed=[]
        class ObservedSTT:
            def transcribe(self,utterance):
                observed.append(utterance.pcm)
                return Transcript(utterance.session_id,'Hey Adam, turn off the kitchen lights.')
        self.app.state.jobs.factory=ObservedSTT
        self.app.state.auth.heartbeat(self.unit['id'],'test-firmware','hey_adam-installed')
        tags=dict(room='Kitchen',speaker='A',distance='3 metres',collection_session='evening-01')
        self.assertEqual(self.client.put('/api/training/contexts/'+self.unit['id'],json=tags).status_code,200)
        data,raw=self.upload(**{'X-Trigger':'wake','X-Wake-Model':'hey_adam-pilot-v2'})
        record=self.completed(data['archive_id'])
        self.assertEqual((record['label'],record['phrase_start_ms']),('unreviewed',None))
        self.assertEqual((record['duration_ms'],record['preroll_ms'],record['capture_ms']),(1000,200,800))
        self.assertEqual((record['trigger'],record['wake_model_id'],record['firmware']),('wake','hey_adam-pilot-v2','test-firmware'))
        self.assertEqual(record['sha256'],hashlib.sha256(raw).hexdigest())
        for key,value in tags.items():self.assertEqual(record[key],value)
        original=self.client.get('/api/training/recordings/'+record['id']+'/audio')
        self.assertEqual(original.content,raw)
        self.assertNotEqual(observed[0],parse_wav(raw)[1].tobytes())
        self.assertGreater(record['transcription_details']['input_gain'],1)
        # Reopen the persistent store after work finishes: transcript, context and originals survive.
        reopened=InteractionArchive(self.archive.directory)
        self.assertEqual(reopened.get(record['id']),record)
        self.assertEqual(reopened.audio(record['id'])[1],raw)
        self.assertEqual(reopened.context(self.unit['id']).model_dump(),tags)
        self.app.state.fleet.sessions.clear()
        self.assertEqual(self.client.get('/api/fleet').json()['sessions'][0]['text'],record['transcript'])

    def test_full_diagnostic_library_does_not_block_archive_or_command(self):
        with patch.object(self.app.state.store,'add',side_effect=ClipStorageFull('full')), patch.object(self.app.state.fleet.home_assistant,'submit') as command:
            data,raw=self.upload(**{'X-Trigger':'wake','X-Wake-Model':'hey_adam-test'})
            self.completed(data['archive_id'])
            for _ in range(100):
                if command.called:break
                time.sleep(.01)
            self.assertTrue(command.called)
            self.assertIsNone(data['clip_id'])
            self.assertEqual(self.archive.audio(data['archive_id'])[1],raw)

    def test_archive_limit_does_not_evict_or_block_voice(self):
        options=self.archive.settings().model_copy(update={'max_records':1})
        self.archive.save_settings(options)
        first,raw=self.upload();self.completed(first['archive_id'])
        with patch.object(self.app.state.fleet.home_assistant,'submit') as command:
            second,_=self.upload(**{'X-Trigger':'wake','X-Wake-Model':'hey_adam-test'})
            self.assertIsNone(second['archive_id'])
            self.assertIn('voice processing continues',second['archive_warning'])
            for _ in range(200):
                if command.called:break
                time.sleep(.01)
            self.assertTrue(command.called)
        self.assertTrue(self.archive.status()['full'])
        self.assertEqual(self.archive.status()['records'],1)
        self.assertEqual(self.archive.audio(first['archive_id'])[1],raw)

    def test_atomic_write_failure_keeps_voice_and_recovers_original_on_restart(self):
        with patch('headend.training_archive.os.replace',side_effect=OSError('disk write fault')):
            data,raw=self.upload()
        record=self.completed(data['archive_id'])
        self.assertEqual(record['transcription_status'],'done')
        self.assertIn('Voice processing continues',data['archive_warning'])
        recovered=InteractionArchive(self.archive.directory)
        self.assertEqual(recovered.audio(data['archive_id'])[1],raw)
        self.assertEqual(recovered.get(data['archive_id'])['transcript'],'Fixture transcript')

    def test_unavailable_archive_does_not_block_voice_or_fleet(self):
        self.archive.ready=False;self.archive.last_attempt=time.monotonic()
        result,_=self.upload()
        self.assertIsNone(result['archive_id'])
        for _ in range(200):
            job=self.app.state.jobs.get(result['job_id'])
            if job['status']=='done':break
            time.sleep(.01)
        self.assertEqual(job['status'],'done')
        self.assertFalse(self.client.get('/api/fleet').json()['training_archive']['ready'])

    def test_disabled_collection_preserves_existing_records(self):
        data,_=self.upload();self.completed(data['archive_id'])
        options=self.archive.settings().model_dump();options['enabled']=False
        self.assertEqual(self.client.put('/api/training/settings',json=options).status_code,200)
        second,_=self.upload();self.assertIsNone(second['archive_id'])
        self.assertEqual(InteractionArchive(self.archive.directory).settings().enabled,False)
        self.assertEqual(self.archive.status()['records'],1)

    def test_disk_reserve_stops_new_audio_not_transcription(self):
        usage=shutil.disk_usage(self.archive.directory)
        with patch('headend.training_archive.shutil.disk_usage',return_value=type(usage)(usage.total,usage.total,0)):
            data,_=self.upload()
        self.assertIsNone(data['archive_id'])
        self.assertIn('reserve',data['archive_warning'])
        self.assertIsNotNone(data['job_id'])

    def test_metadata_only_when_full_audio_will_not_fit(self):
        # Leave room for metadata but less than this recording's 32,044-byte WAV.
        used=self.archive.settings().max_bytes-METADATA_RESERVE-10000
        with patch.object(self.archive,'_usage',return_value=(0,used)):
            data,_=self.upload()
        record=self.completed(data['archive_id'])
        self.assertEqual((record['audio_status'],record['transcription_status']),('not_saved','done'))
        self.assertIn('Metadata retained',data['archive_warning'])
        self.assertEqual(self.client.put('/api/training/recordings/'+record['id']+'/review',json=self.review_data()).status_code,422)

    def test_duplicate_upload_after_restart_is_rejected(self):
        data,_=self.upload();record=self.completed(data['archive_id'])
        self.app.state.fleet.sessions.clear()
        self.assertEqual(self.stream(**{'X-Session-ID':record['session_id']}).status_code,409)
        self.assertEqual(self.archive.status()['records'],1)

    def test_stale_completion_cannot_overwrite_new_transcription_attempt(self):
        data,_=self.upload();record=self.completed(data['archive_id'])
        with self.archive.db() as db:
            db.execute("UPDATE interactions SET job_id='new-attempt',transcription_status='queued' WHERE id=?",(record['id'],))
        self.archive.attach_job(record['id'],data['job_id'])
        self.archive.complete(record['id'],dict(id=data['job_id'],status='error',error='stale attempt'))
        updated=self.archive.get(record['id'])
        self.assertEqual((updated['job_id'],updated['transcription_status']),('new-attempt','queued'))

    def test_review_validation_concurrency_and_immutable_original(self):
        data,raw=self.upload();record=self.completed(data['archive_id']);url='/api/training/recordings/'+record['id']+'/review'
        review=self.review_data();review['phrase_end_ms']=1001
        self.assertEqual(self.client.put(url,json=review).status_code,422)
        review['phrase_end_ms']=180;review['cue_status']='unchecked'
        self.assertEqual(self.client.put(url,json=review).status_code,422)
        review['cue_status']='excluded'
        saved=self.client.put(url,json=review);self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(saved.json()['label'],'positive')
        self.assertEqual(self.client.put(url,json=review).status_code,409)
        self.assertEqual(self.archive.audio(record['id'])[1],raw)
        for label in ('negative','discard','unreviewed'):
            review.update(revision=saved.json()['revision'],label=label,phrase_start_ms=None,phrase_end_ms=None,cue_status='unchecked')
            saved=self.client.put(url,json=review);self.assertEqual(saved.status_code,200,saved.text)
        self.assertEqual(InteractionArchive(self.archive.directory).get(record['id'])['label'],'unreviewed')

    def test_corrupted_original_cannot_be_downloaded_or_approved(self):
        data,_=self.upload();record=self.completed(data['archive_id'])
        self.archive._audio_path(record['id']).write_bytes(b'corrupt')
        base='/api/training/recordings/'+record['id']
        self.assertEqual(self.client.get(base+'/audio').status_code,503)
        self.assertEqual(self.client.put(base+'/review',json=self.review_data()).status_code,503)
        self.assertEqual(self.archive.get(record['id'])['label'],'unreviewed')

    def test_preview_gain_and_waveform_never_modify_original(self):
        data,raw=self.upload();record=self.completed(data['archive_id']);base='/api/training/recordings/'+record['id']
        self.assertNotEqual(self.client.get(base+'/audio?gain_db=24').content,raw)
        self.assertEqual(self.client.get(base+'/audio?gain_db=99').status_code,422)
        wave=self.client.get(base+'/waveform').json()
        self.assertEqual(wave['duration_ms'],1000);self.assertEqual(len(wave['peaks']),600)
        self.assertEqual(self.archive.audio(record['id'])[1],raw)

    def test_manual_listen_keeps_installed_model_and_does_not_execute_commands(self):
        self.app.state.auth.heartbeat(self.unit['id'],'test-fw','hey_adam-installed')
        self.assertEqual(self.client.post('/api/units/'+self.unit['id']+'/listen').status_code,200)
        self.assertEqual(self.client.get('/api/units/'+self.unit['id']+'/poll?wake_model=hey_adam-installed',headers=self.headers()).json()['type'],'listen')
        with patch.object(self.app.state.fleet.home_assistant,'submit') as command:
            data,_=self.upload();record=self.completed(data['archive_id'])
            self.assertEqual((record['trigger'],record['wake_model_id']),('touch','hey_adam-installed'))
            self.app.state.jobs.pool.submit(lambda:None).result(timeout=5)
            command.assert_not_called()

    def test_incomplete_uploads_never_enter_archive(self):
        self.assertEqual(self.stream(bytes(643)).status_code,400)
        self.assertEqual(self.archive.status()['records'],0)

    def test_restart_marks_unfinished_transcription_and_retry_has_no_commands(self):
        data,raw=self.upload();record=self.completed(data['archive_id'])
        with self.archive.db() as db:
            db.execute("UPDATE interactions SET transcription_status='running' WHERE id=?",(record['id'],))
        reopened=InteractionArchive(self.archive.directory)
        self.assertEqual(reopened.get(record['id'])['transcription_status'],'interrupted')
        self.client.put('/api/training/recordings/'+record['id']+'/review',json=self.review_data())
        with patch.object(self.app.state.fleet.home_assistant,'submit') as command:
            self.assertEqual(self.client.post('/api/training/recordings/'+record['id']+'/transcribe').status_code,202)
            self.assertEqual(self.completed(record['id'])['transcription_status'],'done')
            self.app.state.jobs.pool.submit(lambda:None).result(timeout=5)
            command.assert_not_called()
        self.assertEqual(reopened.get(record['id'])['label'],'positive')
        self.assertEqual(self.archive.status()['records'],1)
        self.assertEqual(self.archive.audio(record['id'])[1],raw)

    def test_cancelled_queued_job_persists_and_no_speech_and_errors_persist(self):
        started=threading.Event();release=threading.Event()
        class BlockingSTT:
            def transcribe(self,utterance):
                started.set();release.wait(5)
                return Transcript(utterance.session_id,'')
        self.app.state.jobs.factory=BlockingSTT
        first,_=self.upload();self.assertTrue(started.wait(3))
        try:
            second,_=self.upload();self.app.state.jobs.cancel(second['job_id'])
        finally:
            release.set()
        self.assertEqual(self.completed(first['archive_id'])['transcription_status'],'no_speech')
        self.assertEqual(self.completed(second['archive_id'])['transcription_status'],'cancelled')
        with patch.object(self.app.state.jobs.provider,'transcribe',side_effect=RuntimeError('test STT failure')):
            third,_=self.upload();record=self.completed(third['archive_id'])
        self.assertEqual(record['transcription_status'],'error')
        self.assertIn('test STT failure',record['transcription_error'])

    def test_queue_full_retains_original_with_retryable_error(self):
        with patch.object(self.app.state.jobs,'submit',side_effect=HTTPException(409,'Queue full')):
            self.assertEqual(self.stream().status_code,409)
        record=self.archive.list()['records'][0]
        self.assertEqual(record['transcription_status'],'error')
        self.assertEqual(record['audio_status'],'ready')

    def test_filters_settings_validation_and_auth_scope(self):
        data,_=self.upload();record=self.completed(data['archive_id'])
        self.assertEqual(self.client.get('/api/training/recordings?label=unreviewed&q=Fixture').json()['total'],1)
        self.assertEqual(self.client.get('/api/training/recordings?label=positive').json()['total'],0)
        self.assertEqual(self.client.get('/api/training/recordings?limit=10000').status_code,422)
        self.assertEqual(self.client.put('/api/training/settings',json={'max_bytes':1}).status_code,422)
        self.assertEqual(self.client.get('/api/training/recordings/'+'f'*32).status_code,404)
        self.client.post('/api/auth/logout')
        for path in ('status','recordings','recordings/'+record['id']+'/audio','contexts/'+self.unit['id']):
            self.assertEqual(self.client.get('/api/training/'+path,headers=self.headers()).status_code,401)
        self.assertEqual(self.client.put('/api/training/settings',json={}).status_code,401)


if __name__=='__main__':
    unittest.main()
