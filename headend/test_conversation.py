"""Conversation routing, outage recovery, bounded speech and wake-session integration."""
import asyncio
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

import httpx

from .conversation import (ConversationService, ConversationSettings, control_request,
                           question, spoken_text)
from .state import State
from . import test_fleet


def response(text='The sky scatters blue light more strongly than red light.'):
    return httpx.Response(200, json={'done': True, 'message': {'role': 'assistant', 'content': text}})


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.calls = []
        self.now = [100.0]
        self.remote_down = False
        self.deliver = Mock(side_effect=lambda result, unit: dict(result, status='queued', unit=unit))

        def handle(request):
            self.calls.append(request)
            if request.url.host == 'model.test' and self.remote_down:
                raise httpx.ConnectError('private error detail must not reach the UI', request=request)
            return response()

        self.service = ConversationService(State(self.temp.name), self.deliver,
            transport=httpx.MockTransport(handle), clock=lambda: self.now[0])
        self.addCleanup(self.service.close)
        self.options = ConversationSettings(enabled=True, remote_url='http://model.test')
        self.service.save(self.options)

    def test_preferred_model_receives_only_system_and_current_question(self):
        result = self.service.answer('Hey Adam, why is the sky blue?')
        self.assertEqual(result['provider'], 'remote')
        self.assertEqual(len(self.calls), 1)
        data = json.loads(self.calls[0].content)
        self.assertEqual([m['role'] for m in data['messages']], ['system', 'user'])
        self.assertEqual(data['messages'][1]['content'], 'why is the sky blue?')
        self.assertFalse(data['think'])
        self.assertNotIn('tools', data)
        self.assertEqual(self.service.public()['last_provider'], 'remote')

    def test_outage_uses_local_skips_remote_during_cooldown_and_recovers(self):
        self.remote_down = True
        self.assertEqual(self.service.answer('Why is the sky blue?')['provider'], 'local')
        self.assertEqual([r.url.host for r in self.calls], ['model.test', '127.0.0.1'])
        self.assertNotIn('private error', self.service.public()['last_error'])
        self.calls.clear()
        self.assertEqual(self.service.answer('What makes rain?')['provider'], 'local')
        self.assertEqual([r.url.host for r in self.calls], ['127.0.0.1'])
        self.now[0] += 31
        self.remote_down = False
        self.calls.clear()
        self.assertEqual(self.service.answer('What is a rainbow?')['provider'], 'remote')
        self.assertIsNone(self.service.public()['last_error'])

    def test_both_models_down_returns_a_speakable_failure(self):
        def down(request):
            raise httpx.ConnectError('offline', request=request)
        self.service.transport = httpx.MockTransport(down)
        result = self.service.answer('What is a comet?')
        self.assertEqual(result['status'], 'unavailable')
        self.assertLessEqual(len(result['text']), 200)
        self.assertEqual(self.service.public()['last_provider'], 'unavailable')

    def test_disabled_and_empty_wake_phrase_make_no_requests(self):
        self.assertEqual(self.service.answer('Hey Adam.')['status'], 'ignored')
        self.options.enabled = False
        self.service.save(self.options)
        self.assertEqual(self.service.answer('Tell me a joke')['status'], 'ignored')
        self.assertEqual(self.calls, [])

    def test_unmatched_device_commands_never_go_to_the_model(self):
        for command in ['Hey Adam, turn on the kitchen', 'Could you please unlock the front door',
                        'Set bedroom brightness to 10 percent', 'Please close the garage']:
            self.assertTrue(control_request(command), command)
            self.assertEqual(self.service.answer(command)['status'], 'unsupported')
        self.assertEqual(self.calls, [])
        self.assertFalse(control_request('Why do leaves turn yellow?'))

    def test_incomplete_or_tool_responses_fall_back_instead_of_being_spoken(self):
        for message in ({'content': 'Done', 'tool_calls': [{'name': 'unlock'}]}, {'content': '<think>unfinished'}):
            def handle(request):
                return httpx.Response(200, json={'done': True, 'message': message}) if request.url.host == 'model.test' else response()
            self.service.transport = httpx.MockTransport(handle)
            self.service.save(self.options)
            self.assertEqual(self.service.answer('Tell me a joke')['provider'], 'local')

    def test_total_timeout_includes_the_whole_stream(self):
        class SlowBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(.6)
                yield b'{'
                await asyncio.sleep(.6)
                yield b'}'

        async def handle(request):
            return httpx.Response(200, stream=SlowBody()) if request.url.host == 'model.test' else response()
        self.options.remote_timeout_seconds = 1
        self.service.save(self.options)
        self.service.transport = httpx.MockTransport(handle)
        self.assertEqual(self.service.answer('Why is the sky blue?')['provider'], 'local')
        self.assertIn('Timeout', self.service.public()['last_error'])

    def test_manual_and_failed_transcriptions_do_not_generate_replies(self):
        for trigger, status in [('touch', 'done'), ('wake', 'no_speech'), ('wake', 'error')]:
            entry = dict(trigger=trigger, started=time.time(), unit_id='unit')
            self.service.submit(entry, dict(status=status, text='Tell me a joke'))
            self.assertNotIn('conversation_submitted', entry)
        self.assertEqual(self.calls, [])
        self.deliver.assert_not_called()

    def test_session_is_submitted_once_and_obsolete_settings_do_not_deliver(self):
        entry = dict(trigger='wake', started=time.time(), unit_id='unit')
        job = dict(status='done', text='Tell me a joke')
        self.service.submit(entry, job)
        self.service.submit(entry, job)
        for _ in range(100):
            if entry['reply']['status'] != 'working':
                break
            time.sleep(.01)
        self.assertEqual(entry['reply']['status'], 'queued')
        self.assertEqual(len(self.calls), 1)
        self.deliver.assert_called_once()
        self.deliver.reset_mock()
        self.service.slots.acquire()
        self.service._run(dict(started=time.time(), unit_id='unit'), 'Old question', self.service.generation-1)
        self.deliver.assert_not_called()

    def test_settings_survive_restart_and_fallback_is_really_local(self):
        reopened = ConversationService(State(self.temp.name), self.deliver)
        try:
            self.assertEqual(reopened.public()['remote_url'], 'http://model.test')
            self.assertTrue(reopened.public()['enabled'])
        finally:
            reopened.close()
        for key, value in [('local_url', 'http://192.168.1.20:11434'),
                           ('remote_url', 'http://user:password@model.test'),
                           ('remote_url', 'http://model.test/api/chat')]:
            with self.assertRaises(ValueError):
                ConversationSettings(**{**self.options.model_dump(), key: value})

    def test_speech_has_no_thinking_markup_and_stays_within_voice_limit(self):
        self.assertEqual(question('Hey Adam, hello!'), 'hello!')
        self.assertEqual(question('Hey, Adam, why is the sky blue?'), 'why is the sky blue?')
        self.assertEqual(spoken_text('<think>private reasoning</think> **Hello**, friend.'), 'Hello, friend.')
        self.assertLessEqual(len(spoken_text('A long answer. ' * 100)), 200)
        with self.assertRaises(ValueError):
            spoken_text('<think>unfinished reasoning')

    def monitor(self, local_ready=True, remote_ready=True, started=None, release=None):
        def handle(request):
            if started:
                started.set()
                release.wait(2)
            remote = request.url.host == 'model.test'
            if remote and self.remote_down:
                raise httpx.ConnectError('offline', request=request)
            name = self.options.remote_model if remote else self.options.local_model
            # Loaded source name can differ from the alias used by ADAM.
            loaded = remote_ready if remote else local_ready
            model = dict(name=name if request.url.path.endswith('/tags') else 'source:latest', digest=name)
            return httpx.Response(200, json={'models': [model] if loaded else []})
        self.service.transport = httpx.MockTransport(handle)

    def finish_probe(self):
        for _ in range(200):
            with self.service.lock:
                done = not self.service.probe_pending
            if done:
                return self.service.panel_provider()
            time.sleep(.005)
        self.fail('Status probe did not complete')

    def test_idle_status_follows_gpu_stop_and_recovery_without_questions(self):
        self.monitor()
        self.assertEqual(self.service.panel_provider(), 'unknown')
        self.assertEqual(self.finish_probe(), 'remote')
        self.remote_down = True
        self.now[0] += 6
        self.service.panel_provider()
        self.assertEqual(self.finish_probe(), 'local')
        self.remote_down = False
        self.now[0] += 6
        self.service.panel_provider()
        self.assertEqual(self.finish_probe(), 'remote')
        self.assertIsNone(self.service.last_provider)  # No generated answer needed.
        self.assertEqual(self.calls, [])

    def test_idle_status_respects_cooldown_unknown_disabled_and_model_readiness(self):
        self.monitor()
        self.service.panel_provider(); self.finish_probe()
        self.service.retry_at = self.now[0]+30
        self.assertEqual(self.service.panel_provider(), 'local')
        self.now[0] += 31
        self.service.panel_provider(); self.finish_probe()
        self.assertEqual(self.service.panel_provider(), 'remote')
        self.monitor(local_ready=False, remote_ready=False)
        self.now[0] += 6
        self.service.panel_provider()
        self.assertEqual(self.finish_probe(), 'unavailable')
        self.options.enabled = False
        self.service.save(self.options)
        self.assertEqual(self.service.panel_provider(), 'disabled')

    def test_idle_poll_does_not_wait_for_slow_probe_or_accept_old_settings_result(self):
        started, release = threading.Event(), threading.Event()
        self.monitor(started=started, release=release)
        try:
            self.service.panel_provider()
            self.assertTrue(started.wait(1))
            start = time.monotonic()
            self.assertEqual(self.service.panel_provider(), 'unknown')
            self.assertLess(time.monotonic()-start, .1)
            self.service.save(self.options)
        finally:
            release.set()
        for _ in range(200):
            if not self.service.probe_pending:
                break
            time.sleep(.005)
        self.assertIsNone(self.service.checked_at)
        self.assertEqual(self.service.availability, {})


class ConversationIntegrationTests(unittest.TestCase):
    setUp = test_fleet.FleetTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    headers = test_fleet.FleetTests.headers
    poll = test_fleet.FleetTests.poll
    stream = test_fleet.FleetTests.stream
    def test_wake_question_generates_one_targeted_spoken_page(self):
        service = self.app.state.fleet.conversation
        service.transport = httpx.MockTransport(lambda request: response('Hello from the preferred model.'))
        service.save(ConversationSettings(enabled=True, remote_url='http://model.test'))
        uploaded = self.stream(**{'X-Trigger': 'wake', 'X-Wake-Model': 'test-model'})
        self.assertEqual(uploaded.status_code, 202)
        for _ in range(100):
            entry = self.client.get('/api/fleet').json()['sessions'][0]
            if entry.get('reply', {}).get('status') == 'queued':
                break
            time.sleep(.01)
        self.assertEqual(entry['reply']['provider'], 'remote')
        self.assertEqual(entry['reply']['text'], 'Hello from the preferred model.')
        offered = self.poll().json()
        self.assertEqual(offered['type'], 'page')
        self.assertEqual(offered['id'], entry['reply']['page_id'])

    def test_idle_busy_and_page_poll_share_cached_llm_route_metadata(self):
        service = self.app.state.fleet.conversation
        service.panel_provider = Mock(return_value='remote')
        self.assertEqual(self.poll().json()['panel']['llm_provider'], 'remote')
        path = '/api/units/'+self.unit['id']+'/poll?busy=true'
        self.assertEqual(self.client.get(path, headers=self.headers()).json()['panel']['llm_provider'], 'remote')
        self.client.post('/api/pages/text', json={'units':[self.unit['id']], 'text':'Hello.'})
        service.panel_provider.return_value = 'local'
        offered = self.poll().json()
        self.assertEqual(offered['type'], 'page')
        self.assertEqual(offered['panel']['llm_provider'], 'local')

    def test_home_control_and_weather_have_priority_over_conversation(self):
        fleet = self.app.state.fleet
        fleet.queries.submit = Mock()
        fleet.home_assistant.submit = Mock(return_value=True)
        fleet.conversation.submit = Mock()
        entry = dict(trigger='wake', archive_id=None)
        fleet.transcription_done(entry, dict(status='done', text='Hey Adam, turn on Living Room'))
        fleet.home_assistant.submit.assert_called_once()
        fleet.conversation.submit.assert_not_called()
        fleet.transcription_done(entry, dict(status='done', text='Hey Adam, what is the weather?'))
        fleet.queries.submit.assert_called_once()
        fleet.conversation.submit.assert_not_called()

    def test_settings_and_test_endpoint_require_dashboard_authentication(self):
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/conversation/settings').status_code, 401)
        self.assertEqual(self.client.post('/api/conversation/test', json={'text': 'Hello'}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
