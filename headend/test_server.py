import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
import wave
import numpy as np
from fastapi.testclient import TestClient
from .clips import mono_16k, parse_wav, wav_bytes
from .server import create_app
from .stt import Transcript


class FakeDevice:
    port = 'COM6'

    def __init__(self):
        self.calls = []

    def run(self, operation, value=None):
        self.calls.append((operation, value))
        if operation == 'fetch':
            return wav_bytes(16000, np.zeros((1600, 2), dtype='<i2'))
        if operation == 'status':
            return dict(port=self.port, mic={'ready': '1', 'recording': '0'}, audio={}, ui={})
        return {'ok': True}


class FakeSTT:
    def transcribe(self, utterance):
        return Transcript(utterance.session_id, 'Fixture transcript')


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.device = FakeDevice()
        self.app = create_app(self.directory.name, provider_factory=FakeSTT,
                              device=self.device, testing=True)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.directory.cleanup()

    def upload(self, data=None):
        data = data if data is not None else wav_bytes(16000, np.zeros((1600, 2), dtype='<i2'))
        response = self.client.post('/api/clips', content=data, headers={'X-Filename': 'test.wav'})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json(), data

    def wait_job(self, job_id):
        for _ in range(200):
            job = self.client.get('/api/jobs/' + job_id).json()
            if job['status'] not in ('queued', 'running', 'cancelling'):
                return job
            time.sleep(0.01)
        self.fail('Job did not finish')

    def test_upload_is_lossless_and_rejects_malformed_wav(self):
        clip, raw = self.upload()
        self.assertEqual(self.client.get(f"/api/clips/{clip['id']}/audio").content, raw)
        self.assertEqual(clip['seconds'], 0.1)
        self.assertEqual(clip['levels'][0]['clipped'], 0)
        duplicate, _ = self.upload(raw)
        self.assertEqual(clip['id'], duplicate['id'])
        self.assertEqual(len(self.client.get('/api/clips').json()), 1)
        for invalid in (b'not a wave', raw[:-10]):
            self.assertEqual(self.client.post('/api/clips', content=invalid).status_code, 400)

    def test_channel_review_and_rate_conversion(self):
        data = np.zeros((4410, 2), dtype='<i2')
        data[:, 0] = (1000 * np.sin(np.arange(4410) * 2*np.pi*440/44100)).astype('<i2')
        data[:, 1] = 17
        clip, raw = self.upload(wav_bytes(44100, data))
        review = self.client.get(f"/api/clips/{clip['id']}/audio?channel=2&gain_db=12")
        rate, selected = parse_wav(review.content)
        self.assertEqual(rate, 44100)
        self.assertEqual(selected.shape, (4410, 1))
        self.assertTrue(np.all(selected == round(17 * 10**0.6)))
        self.assertEqual(self.client.get(f"/api/clips/{clip['id']}/audio").content, raw)
        pcm, _ = mono_16k(44100, data, 1)
        resampled = np.frombuffer(pcm, dtype='<i2')
        self.assertEqual(len(resampled), 1600)
        self.assertEqual(np.argmax(np.abs(np.fft.rfft(resampled)))*10, 440)

    def test_transcription_job_correlates_clip_and_channel(self):
        clip, _ = self.upload()
        response = self.client.post(f"/api/clips/{clip['id']}/transcriptions", json={'channel': 2})
        self.assertEqual(response.status_code, 202)
        job = self.wait_job(response.json()['id'])
        self.assertEqual(job['status'], 'done')
        self.assertEqual(job['clip_id'], clip['id'])
        self.assertEqual(job['channel'], 2)
        self.assertEqual(job['text'], 'Fixture transcript')

    def test_busy_job_keeps_health_responsive_and_cancel_suppresses_text(self):
        started, release = threading.Event(), threading.Event()

        class BlockingSTT:
            def transcribe(self, utterance):
                started.set()
                release.wait(4)
                return Transcript(utterance.session_id, 'Must not be returned after cancellation')

        self.app.state.jobs.factory = BlockingSTT
        clip, _ = self.upload()
        url = f"/api/clips/{clip['id']}/transcriptions"
        job_id = self.client.post(url, json={}).json()['id']
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.get('/api/health').status_code, 200)
            self.assertEqual(self.client.post(url, json={}).status_code, 409)
            self.assertEqual(self.client.post(f'/api/jobs/{job_id}/cancel').json()['status'], 'cancelling')
        finally:
            release.set()
        job = self.wait_job(job_id)
        self.assertEqual(job['status'], 'cancelled')
        self.assertEqual(job['text'], '')

    def test_limits_origin_and_explicit_delete(self):
        clip, _ = self.upload()
        self.assertEqual(self.client.post('/api/clips', content=bytes(4*1024*1024+1)).status_code, 413)
        self.assertEqual(self.client.post('/api/device/record', json={'seconds':16}).status_code, 422)
        self.assertEqual(self.client.post('/api/device/record', json={'seconds':0.101}).status_code, 400)
        self.assertEqual(self.client.post('/api/device/stop', headers={'Origin':'https://unrelated.example'}).status_code, 403)
        self.assertEqual(self.client.delete('/api/clips/' + clip['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/clips/' + clip['id']).status_code, 404)

    def test_device_bridge_endpoints_and_ui_assets(self):
        self.assertEqual(self.client.get('/').status_code, 200)
        self.assertEqual(self.client.get('/assets/app.js').status_code, 200)
        self.assertEqual(self.client.get('/api/device').json()['port'], 'COM6')
        self.client.post('/api/device/record', json={'seconds': 8})
        self.assertIn(('record', 8000), self.device.calls)
        self.assertEqual(self.client.post('/api/device/fetch').json()['source'], 'device')


if __name__ == '__main__':
    unittest.main()
