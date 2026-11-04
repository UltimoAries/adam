"""Topic isolation, contextual routing and reply-authorized microphone windows."""
import json
import time
import unittest
import uuid
from unittest.mock import patch

import httpx

from .dialogue import Dialogue
from . import test_fleet
from .conversation import ConversationSettings


class DialogueTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.service = Dialogue(clock=lambda:self.now)

    def turn(self, text, key='one', reply=None):
        entry = dict(id=uuid.uuid4().hex, trigger='text', context_key=key, started=time.time())
        job = self.service.prepare(entry, dict(status='done', text=text))
        if reply is not None:
            entry['reply'] = reply
        return entry, job

    def test_weather_day_and_city_followups(self):
        success = dict(status='answered', text='Forecast')
        self.turn('What is the weather tomorrow?', reply=success)
        entry, job = self.turn('Hey Adam, what about Seattle?', reply=success)
        self.assertEqual(job['text'], 'weather tomorrow in seattle')
        self.assertEqual(entry['resolved_text'], job['text'])
        self.assertEqual(self.turn('What about today?', reply=success)[1]['text'], 'weather today in seattle')
        self.assertEqual(self.turn('What about Portland tomorrow?')[1]['text'], 'weather tomorrow in portland')

    def test_no_rewrite_without_context_or_after_failure_action_or_expiry(self):
        good = dict(status='answered', text='Forecast')
        self.turn('What is the weather tomorrow?', reply=good)
        self.assertEqual(self.turn('What about Seattle?', key='two')[1]['text'], 'What about Seattle?')
        self.assertEqual(self.turn('What about turn off the lights?')[1]['text'], 'What about turn off the lights?')
        self.turn('What is the weather tomorrow?', reply=dict(status='unavailable'))
        self.assertEqual(self.turn('What about Seattle?')[1]['text'], 'What about Seattle?')
        self.turn('What is the weather tomorrow?', reply=good)
        self.now += 91
        self.assertEqual(self.turn('What about Seattle?')[1]['text'], 'What about Seattle?')

    def test_model_history_bounded_scoped_and_cleared_by_stop(self):
        for n in range(8):
            self.turn('Explain '+str(n), reply=dict(status='queued', answer_status='answered', provider='remote', text='Answer '+str(n)))
        entry, _ = self.turn('Tell me more')
        self.assertEqual(len(entry['conversation_history']), 6)
        self.assertEqual(entry['conversation_history'][-1]['content'], 'Answer 7')
        self.assertEqual(self.turn('Tell me more', key='other')[0]['conversation_history'], [])
        self.assertTrue(self.turn("That's all")[0]['end_conversation'])
        self.assertEqual(self.turn('Tell me more')[0]['conversation_history'], [])
        for text in ('Okay, thanks.', 'No thanks', "That's all, thank you", 'Thank you Adam'):
            self.assertTrue(self.turn(text)[0]['end_conversation'], text)
        self.assertNotIn('end_conversation', self.turn('Thanks, what is the weather?')[0])

    def test_weather_clarification_keeps_day_and_accepts_region(self):
        self.turn('weather tomorrow in Seattle', reply=dict(status='unavailable',
            location_choices=['Seattle, Washington, US', 'Seattle, Jalisco, MX']))
        self.assertEqual(self.turn('Washington')[1]['text'], 'weather tomorrow in Seattle, Washington')
        self.turn('weather tomorrow in Seattle', reply=dict(status='unavailable',
            location_choices=['Seattle, Washington, US', 'Seattle, Jalisco, MX']))
        self.assertEqual(self.turn('See you at Washington.')[1]['text'], 'weather tomorrow in Seattle, Washington')
        self.turn('weather tomorrow in Seattle', reply=dict(status='unavailable',
            location_choices=['Seattle, Washington, US', 'Seattle, Jalisco, MX']))
        self.assertEqual(self.turn('turn on Washington light')[1]['text'], 'turn on Washington light')

    def test_grants_require_delivery_same_unit_expire_and_cannot_replay(self):
        entry = dict(trigger='wake', started=time.time())
        self.assertTrue(self.service.offer('one', 'page', entry))
        with self.assertRaises(ValueError): self.service.consume('one', 'page')
        self.service.ack('two', 'page', 'delivered')
        with self.assertRaises(ValueError): self.service.consume('one', 'page')
        self.service.ack('one', 'page', 'delivered')
        with self.assertRaises(ValueError): self.service.consume('two', 'page')
        info = self.service.consume('one', 'page')
        self.assertEqual(info['followup_count'], 1)
        self.service.ack('one', 'page', 'delivered')
        with self.assertRaises(ValueError): self.service.consume('one', 'page')
        self.service.offer('one', 'next', dict(entry, **info))
        self.service.ack('one', 'next', 'delivered')
        self.now += 13
        with self.assertRaises(ValueError): self.service.consume('one', 'next')
        self.assertFalse(self.service.offer('one', 'stop', dict(entry, end_conversation=True)))
        self.assertFalse(self.service.offer('one', 'cap', dict(entry, followup_count=6)))
        self.assertFalse(self.service.offer('one', 'expired', dict(entry, conversation_deadline=99)))
        self.assertFalse(self.service.offer('one', 'text', dict(entry, trigger='text')))

    def test_adaptive_windows_and_interruption_revoke_grants(self):
        entry = dict(trigger='wake', started=time.time())
        self.assertEqual(self.service.offer('one', 'clarify', dict(entry, reply=dict(needs_clarification=True)), adaptive=True), 12)
        self.assertEqual(self.service.offer('one', 'action', dict(entry, reply=dict(action='off')), adaptive=True), 5)
        self.service.ack('one', 'clarify', 'interrupted')
        with self.assertRaises(ValueError): self.service.consume('one', 'clarify')
        self.assertEqual(self.service.offer('one', 'old', dict(entry, reply=dict(action='off'))), 8)


class FollowupProtocolTests(unittest.TestCase):
    setUp = test_fleet.FleetTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    headers = test_fleet.FleetTests.headers
    stream = test_fleet.FleetTests.stream

    def offer_reply(self, trigger='wake', **extra):
        fleet = self.app.state.fleet
        page = self.app.state.auth.enqueue([self.unit['id']], bytes(3200), 'text', 'Hello')[0]['id']
        fleet.sessions[uuid.uuid4().hex] = dict(unit_id=self.unit['id'], trigger=trigger,
            started=time.time(), reply=dict(status='queued', page_id=page), **extra)
        offered = self.client.get(f"/api/units/{self.unit['id']}/poll?followup=1", headers=self.headers()).json()
        return page, offered

    def ack(self, page, status):
        return self.client.post(f"/api/units/{self.unit['id']}/pages/{page}/ack", headers=self.headers(), json=dict(status=status))

    def test_followup_requires_scoped_one_use_grant(self):
        self.assertEqual(self.stream(**{'X-Trigger':'followup'}).status_code, 400)
        page, offer = self.offer_reply()
        self.assertEqual(offer['followup_seconds'], 8)
        self.assertEqual(self.stream(**{'X-Trigger':'followup', 'X-Followup-Page':page}).status_code, 400)
        self.assertEqual(self.ack(page, 'playing').status_code, 200)
        self.assertEqual(self.ack(page, 'delivered').status_code, 200)
        with patch.object(self.app.state.fleet, 'dispatch_command') as dispatch:
            response = self.stream(**{'X-Trigger':'followup', 'X-Followup-Page':page})
            self.assertEqual(response.status_code, 202, response.text)
            for _ in range(100):
                if dispatch.called: break
                time.sleep(.01)
            self.assertTrue(dispatch.called)
            self.assertEqual(dispatch.call_args.args[0]['trigger'], 'followup')
        self.ack(page, 'delivered')
        self.assertEqual(self.stream(**{'X-Trigger':'followup', 'X-Followup-Page':page}).status_code, 400)

    def test_announcements_text_diagnostics_and_disabled_setting_do_not_arm(self):
        for trigger in ('text', 'touch'):
            page, offer = self.offer_reply(trigger=trigger)
            self.assertNotIn('followup_seconds', offer)
            self.ack(page, 'failed')
        self.app.state.fleet.conversation.options.followup_listening = False
        _, offer = self.offer_reply()
        self.assertNotIn('followup_seconds', offer)

    def test_model_receives_short_history_but_test_endpoint_does_not(self):
        service = self.app.state.fleet.conversation
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=dict(done=True, message=dict(content='A short answer.')))
        service.transport = httpx.MockTransport(handler)
        service.save(ConversationSettings(enabled=True, mode='local'))
        history = [dict(role='user', content='Why is the sky blue?'), dict(role='assistant', content='Scattering.')]
        answer = service.answer('What about sunsets?', history)
        self.assertEqual(answer['status'], 'answered')
        self.assertEqual(requests[-1]['messages'][1:3], history)
        service.answer('New question')
        self.assertEqual(len(requests[-1]['messages']), 2)

    def test_text_weather_followup_reaches_weather_not_status(self):
        context = uuid.uuid4().hex
        questions = []
        def answer(text, unit_id):
            questions.append(text)
            return dict(status='answered', text='Weather answer.')
        with patch.object(self.app.state.fleet.queries, 'answer', side_effect=answer):
            for text in ('What is the weather tomorrow?', 'What about Seattle?', 'What about today?'):
                rid = uuid.uuid4().hex
                response = self.client.post('/api/commands/text', json=dict(request_id=rid, context_id=context, text=text))
                self.assertEqual(response.status_code, 202)
                for _ in range(100):
                    reply = self.client.get('/api/commands/text/'+rid).json()['reply']
                    if reply['status'] != 'working': break
                    time.sleep(.01)
                self.assertEqual(reply['text'], 'Weather answer.')
        self.assertEqual(questions, ['What is the weather tomorrow?', 'weather tomorrow in seattle', 'weather today in seattle'])

    def test_limit_capture_does_not_dispatch_partial_command(self):
        entry = dict(id=uuid.uuid4().hex, unit_id=self.unit['id'], trigger='followup', endpoint_reason='limit')
        with patch.object(self.app.state.fleet, 'dispatch_command', side_effect=AssertionError('Partial command dispatched')):
            self.app.state.fleet.transcription_done(entry, dict(status='done', text='turn off'))
        self.assertTrue(entry['end_conversation'])
        self.assertIn('continuous audio', entry['reply']['text'])

    def test_interrupted_page_is_terminal_and_audio_is_released(self):
        page, _ = self.offer_reply()
        self.ack(page, 'playing')
        self.assertEqual(self.ack(page, 'interrupted').status_code, 200)
        self.assertEqual(self.ack(page, 'interrupted').status_code, 200)
        self.assertEqual(self.ack(page, 'delivered').status_code, 400)
        self.assertEqual(self.stream(**{'X-Trigger':'followup', 'X-Followup-Page':page}).status_code, 400)
        with self.app.state.auth.db() as db:
            self.assertIsNone(db.execute('SELECT pcm FROM pages WHERE id=?',(page,)).fetchone()[0])
