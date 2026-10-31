import asyncio
import json
from pathlib import Path
import tempfile
import unittest

import httpx
import numpy as np
from fastapi.testclient import TestClient

from .clips import wav_bytes
from .model_services import ModelServices, RoutedTranscriber, ServiceSettings
from .server import create_app
from .session import Trigger, Utterance
from .state import State
from .stt import Transcript
from .test_server import FakeDevice, FakeSTT


class ModelServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(Path(self.temp.name))
        self.now = 100
        self.calls = []
        self.reply = httpx.Response(200, json={'text': 'Turn off the bed light'})
        self.services = ModelServices(self.state, httpx.MockTransport(self.handle), clock=lambda:self.now)
        self.utterance = Utterance('one', Trigger.WAKE, 'hey_adam', bytes(3200))
        self.local_calls = 0

    def handle(self, request):
        self.calls.append(request)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def configure(self, **changes):
        profile = dict(mode='prefer_remote', url='http://gpu:8000/v1', model='test-model', voice='test-voice', api_key='secret')
        profile.update(changes)
        return self.services.save(ServiceSettings(stt=profile, tts=profile))

    def local(self, utterance):
        self.local_calls += 1
        return Transcript(utterance.session_id, 'local transcript')

    def test_local_is_default_and_never_contacts_worker(self):
        self.assertEqual(self.services.transcribe(self.utterance, self.local).text, 'local transcript')
        self.assertEqual(self.calls, [])

    def test_remote_protocol_and_no_speech_result(self):
        self.configure()
        result = self.services.transcribe(self.utterance, self.local)
        self.assertEqual(result.session_id, 'one')
        self.assertEqual(result.text, 'Turn off the bed light')
        request = self.calls[0]
        self.assertEqual(str(request.url), 'http://gpu:8000/v1/audio/transcriptions')
        self.assertEqual(request.headers['authorization'], 'Bearer secret')
        self.assertIn(b'RIFF', request.content)
        self.reply = httpx.Response(200, json={'text': ''})
        self.assertEqual(self.services.transcribe(self.utterance, self.local).text, '')
        self.assertEqual(self.local_calls, 0)

    def test_offline_cooldown_and_automatic_recovery(self):
        self.configure()
        self.reply = httpx.ConnectError('secret URL must not escape')
        for _ in range(2):
            self.assertEqual(self.services.transcribe(self.utterance, self.local).text, 'local transcript')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.local_calls, 2)
        self.assertEqual(self.services.public()['stt']['last_provider'], 'local')
        self.assertNotIn('secret', json.dumps(self.services.public()))
        self.now += 31
        self.reply = httpx.Response(200, json={'text': 'remote again'})
        self.assertEqual(self.services.transcribe(self.utterance, self.local).text, 'remote again')
        self.assertIsNone(self.services.public()['stt']['last_error'])

    def test_invalid_oversized_and_redirected_transcripts_fall_back(self):
        for reply in (httpx.Response(200, json={'text': 1}), httpx.Response(200, content=b'x'*65537),
                      httpx.Response(302, headers={'location':'http://elsewhere/'}), httpx.Response(401)):
            with self.subTest(status=reply.status_code):
                self.configure()
                self.reply = reply
                self.assertEqual(self.services.transcribe(self.utterance, self.local).text, 'local transcript')

    def test_speech_resamples_and_falls_back_on_silent_or_invalid_wav(self):
        self.configure()
        self.reply = httpx.Response(200, content=wav_bytes(24000, np.full(2400, 1234, dtype='<i2')))
        calls = []
        local = lambda text: calls.append(text) or b'local pcm'
        pcm = self.services.synthesize('Hello', local)
        self.assertEqual(len(pcm), 3200)
        self.assertEqual(json.loads(self.calls[-1].content)['response_format'], 'wav')
        self.assertEqual(calls, [])
        for raw in (b'bad wav', wav_bytes(16000, np.zeros(1600, dtype='<i2'))):
            self.configure()
            self.reply = httpx.Response(200, content=raw)
            self.assertEqual(self.services.synthesize('Hello', local), b'local pcm')
        self.assertEqual(calls, ['Hello', 'Hello'])
        # STT failure does not disable a healthy TTS service.
        self.assertEqual(self.services.public()['stt']['retry_in_seconds'], 0)

    def test_timeout_cancels_remote_then_falls_back(self):
        cancelled = []
        async def slow(request):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.append(True)
        self.services.transport = httpx.MockTransport(slow)
        self.configure(timeout_seconds=1)
        self.assertEqual(self.services.transcribe(self.utterance, self.local).text, 'local transcript')
        self.assertEqual(cancelled, [True])
        self.assertEqual(self.local_calls, 1)

    def test_keys_persist_but_never_follow_changed_server(self):
        self.configure()
        self.assertNotIn('api_key', self.services.public()['stt'])
        self.configure(api_key='')
        restored = ModelServices(self.state)
        self.assertEqual(restored.settings.stt.api_key, 'secret')
        self.configure(api_key='', url='http://other:8000/v1')
        self.assertFalse(self.services.public()['stt']['has_key'])
        self.configure(clear_key=True)
        self.assertFalse(self.services.public()['tts']['has_key'])

    def test_preload_warms_only_local_and_local_failure_is_reported(self):
        self.configure()
        wrapper = RoutedTranscriber(self.services, lambda: FakeSTT())
        wrapper.preload()
        self.assertEqual(self.calls, [])
        self.reply = httpx.Response(500)
        def fail(utterance):
            raise RuntimeError('Local model unavailable')
        with self.assertRaisesRegex(RuntimeError, 'Local model unavailable'):
            self.services.transcribe(self.utterance, fail)
        self.assertEqual(self.services.public()['stt']['last_provider'], 'local_error')

    def test_settings_require_auth_and_validation_does_not_echo_keys(self):
        app = create_app(Path(self.temp.name)/'clips', provider_factory=FakeSTT, device=FakeDevice(),
                         testing=True, auth_required=True)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/models/settings').status_code, 401)
            client.post('/api/auth/setup', json=dict(username='admin', password='testing-password'))
            client.post('/api/auth/login', json=dict(username='admin', password='testing-password'))
            self.assertEqual(client.get('/api/models/settings').status_code, 200)
            response = client.put('/api/models/settings', json={'stt':{'api_key':'sensitive', 'url':'invalid'}})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('sensitive', response.text)
            self.assertEqual(client.put('/api/models/settings', json={}, headers={'Origin':'http://evil.test'}).status_code, 403)
            self.assertEqual(client.get('/models').status_code, 200)


if __name__ == '__main__':
    unittest.main()
