"""Unified settings persistence and shared GPU outage behavior."""
import json
import tempfile
import unittest
from unittest.mock import Mock

import httpx
from fastapi.testclient import TestClient

from .conversation import ConversationService, ConversationSettings
from .model_services import ModelServices
from .processing import ProcessingService, ProcessingSettings
from .server import create_app
from .session import Trigger, Utterance
from .state import State
from .stt import Transcript
from .test_server import FakeDevice, FakeSTT


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = State(self.temp.name)
        self.now, self.down, self.calls = 100, False, []
        transport = httpx.MockTransport(self.handle)
        self.speech = ModelServices(self.state, transport, clock=lambda:self.now)
        self.chat = ConversationService(self.state, Mock(), transport=transport, clock=lambda:self.now)
        self.addCleanup(self.chat.close)
        self.service = ProcessingService(self.speech, self.chat)
        self.service.failover.clock = lambda:self.now
        self.options = ProcessingSettings(mode='prefer_remote', gpu_url='http://gpu.test', stt_model='whisper-test')
        self.utterance = Utterance('one', Trigger.WAKE, 'hey_adam', bytes(3200))
        self.local = lambda utterance:Transcript(utterance.session_id, 'local transcript')
        self.service.save(self.options)

    def handle(self, request):
        self.calls.append(request)
        if self.down and request.url.host == 'gpu.test':
            raise httpx.ConnectError('private failure', request=request)
        if request.url.path.endswith('transcriptions'):
            return httpx.Response(200, json={'text':'remote transcript'})
        return httpx.Response(200, json={'done':True, 'message':{'role':'assistant','content':'The Moon.'}})

    def test_atomic_save_persists_both_routes_and_preserves_local_models(self):
        local_before = self.chat.options.local_model
        result = self.service.save(self.options.model_copy(update={'conversation_model':'chosen-model'}))
        restored_speech = ModelServices(self.state)
        restored_chat = ConversationService(self.state, Mock())
        self.addCleanup(restored_chat.close)
        self.assertEqual(restored_speech.settings.stt.url, 'http://gpu.test:8000/v1')
        self.assertEqual(restored_speech.settings.stt.mode, 'prefer_remote')
        self.assertEqual(restored_chat.options.remote_url, 'http://gpu.test:11434')
        self.assertEqual(restored_chat.options.remote_model, 'chosen-model')
        self.assertEqual(restored_chat.options.local_model, local_before)
        self.assertEqual(restored_speech.settings.tts.mode, 'local')
        self.assertEqual(result['mode'], 'prefer_remote')

    def test_key_preserved_only_for_same_address_and_never_exposed(self):
        self.service.save(ProcessingSettings(**dict(self.options.model_dump(), api_key='test-secret')))
        self.service.save(self.options)
        self.assertEqual(self.speech.settings.stt.api_key, 'test-secret')
        self.assertNotIn('test-secret', json.dumps(self.service.public()))
        self.service.save(self.options.model_copy(update={'gpu_url':'http://other.test'}))
        self.assertEqual(self.speech.settings.stt.api_key, '')

    def test_invalid_settings_and_failed_transaction_leave_both_unchanged(self):
        before = self.service.public()
        with self.assertRaises(ValueError):
            self.service.save(self.options.model_copy(update={'stt_model':''}))
        self.assertEqual(self.service.public(), before)
        # A SQLite trigger aborts the second write; the first must roll back too.
        with self.state.db() as db:
            db.execute("CREATE TRIGGER reject_settings BEFORE INSERT ON conversation_settings BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):
            self.service.save(self.options.model_copy(update={'mode':'local'}))
        self.assertEqual(self.service.public(), before)
        self.assertEqual(ModelServices(self.state).settings.stt.mode, 'prefer_remote')

    def test_stt_outage_skips_gpu_conversation_and_recovers(self):
        self.down = True
        self.assertEqual(self.speech.transcribe(self.utterance,self.local).text, 'local transcript')
        self.assertEqual(self.chat.answer('What is the Moon?')['provider'], 'local')
        self.assertEqual([r.url.host for r in self.calls], ['gpu.test','127.0.0.1'])
        self.assertEqual(self.service.public()['retry_in_seconds'], 30)
        self.now += 31
        self.down = False
        self.assertEqual(self.speech.transcribe(self.utterance,self.local).text, 'remote transcript')
        self.assertEqual(self.chat.answer('What is the Moon?')['provider'], 'remote')

    def test_conversation_outage_skips_gpu_stt_and_save_clears_cooldown(self):
        self.down = True
        self.assertEqual(self.chat.answer('What is the Moon?')['provider'], 'local')
        self.calls.clear()
        self.assertEqual(self.speech.transcribe(self.utterance,self.local).text, 'local transcript')
        self.assertEqual(self.calls, [])
        self.service.save(self.options)
        self.assertEqual(self.service.failover.remaining(), 0)

    def test_local_only_never_contacts_gpu(self):
        self.service.save(self.options.model_copy(update={'mode':'local'}))
        self.assertEqual(self.speech.transcribe(self.utterance,self.local).text, 'local transcript')
        self.assertEqual(self.chat.answer('What is the Moon?')['provider'], 'local')
        self.assertTrue(all(r.url.host == '127.0.0.1' for r in self.calls))

    def test_api_requires_login_and_rejects_credentials_without_echo(self):
        app = create_app(self.temp.name, device=FakeDevice(), provider_factory=FakeSTT, testing=True, auth_required=True)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/processing/settings').status_code, 401)
            client.post('/api/auth/setup',json={'username':'admin','password':'long-test-password'})
            client.post('/api/auth/login',json={'username':'admin','password':'long-test-password'})
            result = client.put('/api/processing/settings',json=dict(self.options.model_dump(exclude={'api_key'}),gpu_url='http://user:test-secret@gpu.test'))
            self.assertEqual(result.status_code,400)
            self.assertNotIn('test-secret',result.text)
            good = client.put('/api/processing/settings',json=self.options.model_dump(exclude={'api_key'}))
            self.assertEqual(good.status_code,200)
            self.assertEqual(good.json()['mode'],'prefer_remote')
