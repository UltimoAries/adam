"""Typed command routing, browser authentication and duplicate action safety."""
import time
import unittest
import uuid
from unittest.mock import patch

from . import test_fleet
from .test_home_assistant import FakeHomeAssistant


class TextCommandTests(unittest.TestCase):
    setUp = test_fleet.FleetTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    headers = test_fleet.FleetTests.headers
    poll = test_fleet.FleetTests.poll
    stream = test_fleet.FleetTests.stream

    def send(self, text, **extra):
        body = dict(request_id=uuid.uuid4().hex, text=text, **extra)
        response = self.client.post('/api/commands/text', json=body)
        self.assertEqual(response.status_code, 202, response.text)
        return body, self.finished(body['request_id'])

    def finished(self, key):
        for _ in range(200):
            response = self.client.get('/api/commands/text/' + key)
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            if result['reply']['status'] != 'working':
                return result
            time.sleep(.01)
        self.fail('Command did not finish')

    def test_timer_context_silent_reply_and_duplicate_restart(self):
        body, result = self.send('set a timer for ten minutes')
        self.assertIn('Choose a speaker', result['reply']['text'])
        self.assertEqual(self.app.state.fleet.timers.list_timers(), [])
        body, result = self.send('set a timer for ten minutes', unit_id=self.unit['id'])
        self.assertIn('timer set', result['reply']['text'].lower())
        self.assertEqual(self.app.state.auth.pages(), [])
        self.assertEqual(len(self.app.state.fleet.timers.list_timers()), 1)
        self.client.post('/api/commands/text', json=body)
        # Eviction/restart uses the durable record and never re-dispatches.
        self.app.state.fleet.text_commands.entries.clear()
        replay = self.client.post('/api/commands/text', json=body)
        self.assertEqual(replay.json(), result)
        self.assertEqual(len(self.app.state.fleet.timers.list_timers()), 1)
        body['text'] = 'set a timer for twenty minutes'
        self.assertEqual(self.client.post('/api/commands/text', json=body).status_code, 409)

    def test_ha_without_speaker_and_with_offline_context(self):
        remote = FakeHomeAssistant()
        service = self.app.state.fleet.home_assistant
        service.client = remote.client()
        response = self.client.put('/api/home-assistant/settings', json=dict(
            enabled=True, url='http://ha.local:8123', token='test-only-token',
            entity_id='switch.living_room', name='Living Room'))
        self.assertEqual(response.status_code, 200, response.text)
        body, result = self.send('turn on Living Room')
        self.assertEqual(len(remote.actions()), 1, result)
        self.assertIn('on', result['reply']['text'].lower())
        self.client.post('/api/commands/text', json=body)
        self.assertEqual(len(remote.actions()), 1)
        _, result = self.send('turn off Living Room', unit_id=self.unit['id'])
        self.assertEqual(len(remote.actions()), 2, result)
        self.assertEqual(self.app.state.auth.pages(), [])

    def test_spoken_option_and_no_stt_or_training(self):
        self.poll()
        fleet = self.app.state.fleet
        with patch.object(fleet.jobs, 'submit', side_effect=AssertionError('STT called')):
            _, result = self.send('set a timer for five minutes', unit_id=self.unit['id'], speak_reply=True)
        self.assertEqual(result['reply']['status'], 'queued')
        self.assertEqual(len(self.app.state.auth.pages()), 1)
        self.assertEqual(fleet.sessions, {})
        self.assertEqual(self.client.get('/api/clips').json(), [])

    def test_auth_origin_validation_and_diagnostic_isolation(self):
        body = dict(request_id=uuid.uuid4().hex, text='set a timer for ten minutes')
        self.assertEqual(self.client.post('/api/commands/text', json=dict(body, text='   ')).status_code, 422)
        self.assertEqual(self.client.post('/api/commands/text', json=dict(body, text='x'*501)).status_code, 422)
        self.assertEqual(self.client.post('/api/commands/text', json=dict(body, speak_reply=True)).status_code, 409)
        self.assertEqual(self.client.post('/api/commands/text', json=body, headers={'Origin':'http://evil.invalid'}).status_code, 403)
        fleet = self.app.state.fleet
        with patch.object(fleet, 'dispatch_command') as dispatch:
            for trigger in ('touch', 'text', 'bench'):
                fleet.transcription_done(dict(trigger=trigger), dict(status='done', text=body['text']))
            dispatch.assert_not_called()
        self.assertEqual(self.stream(**{'X-Trigger':'text'}).status_code, 400)
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.post('/api/commands/text', json=body, headers=self.headers()).status_code, 401)
        self.assertEqual(self.client.get('/api/commands/text/'+body['request_id']).status_code, 401)

    def test_unhandled_intercom_and_unknown_restart(self):
        _, result = self.send('call Kitchen')
        self.assertIn('Choose a speaker', result['reply']['text'])
        _, result = self.send('tell me a story')
        self.assertEqual(result['reply']['status'], 'unsupported')
        body = dict(request_id=uuid.uuid4().hex, text='set a timer for ten minutes', unit_id=self.unit['id'])
        fleet = self.app.state.fleet
        with patch.object(fleet, 'dispatch_command', side_effect=lambda entry, job: entry.update(reply={'status':'working'})):
            self.client.post('/api/commands/text', json=body)
        fleet.text_commands.entries.clear()
        result = self.client.post('/api/commands/text', json=body).json()
        self.assertEqual(result['reply']['status'], 'unknown')
        self.assertEqual(fleet.timers.list_timers(), [])

    def test_information_conversation_and_voice_failure_keep_text(self):
        fleet = self.app.state.fleet
        with patch.object(fleet.queries, 'answer', return_value={'status':'answered', 'text':'It is noon.'}):
            _, result = self.send('what time is it')
        self.assertEqual(result['reply']['text'], 'It is noon.')
        fleet.conversation.options.enabled = True
        with patch.object(fleet.conversation, 'answer', return_value={'status':'answered', 'text':'A fixture answer.'}):
            _, result = self.send('tell me a story')
        self.assertEqual(result['reply']['text'], 'A fixture answer.')
        self.poll()
        with patch.object(fleet.speech, 'synthesize', side_effect=RuntimeError('fixture voice failure')):
            body, result = self.send('set a timer for ten minutes', unit_id=self.unit['id'], speak_reply=True)
        self.assertIn('timer set', result['reply']['text'].lower())
        self.assertIn('not retried', result['reply']['delivery_error'])
        self.client.post('/api/commands/text', json=body)
        self.assertEqual(len(fleet.timers.list_timers()), 1)
