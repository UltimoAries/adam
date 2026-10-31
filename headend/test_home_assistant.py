"""Connection credentials, legacy-target migration and wake-to-reply contracts."""
import json
import time
import unittest
from unittest.mock import patch

import httpx
import numpy as np

from .home_assistant import HomeAssistantClient, HomeAssistantSettings, HomeAssistantService
from .clips import wav_bytes
from . import test_fleet
from .stt import Transcript


class FakeHomeAssistant:
    def __init__(self):
        self.calls = []
        self.state = 'off'
        self.failure = None
        self.update = True

    def handle(self, request):
        self.calls.append(request)
        if self.failure:
            return self.failure(request)
        path = request.url.path
        if path == '/api/config':
            return httpx.Response(200, json=dict(version='test', components=['switch']))
        if path == '/api/states':
            return httpx.Response(200, json=[
                dict(entity_id='switch.living_room', state=self.state, attributes={'friendly_name': 'Living Room'}),
                dict(entity_id='switch.bedroom', state='off', attributes={'friendly_name': 'Bedroom'}),
                dict(entity_id='lock.front_door', state='locked', attributes={})])
        if path == '/api/states/switch.living_room':
            return httpx.Response(200, json=dict(entity_id='switch.living_room', state=self.state))
        if path in ('/api/services/switch/turn_on', '/api/services/switch/turn_off'):
            if json.loads(request.content) != {'entity_id': 'switch.living_room'}:
                raise AssertionError('Wrong target')
            if self.update:
                self.state = path.rsplit('_', 1)[-1]
            return httpx.Response(200, json=[])
        raise AssertionError('Unexpected request: ' + str(request.url))

    def client(self):
        return HomeAssistantClient(httpx.MockTransport(self.handle))

    def actions(self):
        return [r for r in self.calls if r.method == 'POST']


class HomeAssistantTests(unittest.TestCase):
    tearDown = test_fleet.FleetTests.tearDown
    headers = test_fleet.FleetTests.headers
    poll = test_fleet.FleetTests.poll
    stream = test_fleet.FleetTests.stream

    def setUp(self):
        test_fleet.FleetTests.setUp(self)
        self.service = self.app.state.fleet.home_assistant
        self.remote = FakeHomeAssistant()
        self.service.client = self.remote.client()
        self.config = dict(enabled=True, url='http://ha.local:8123', token='test-secret-token',
                           entity_id='switch.living_room', name='Living Room')
        self.poll()

    def enable(self):
        result = self.client.put('/api/home-assistant/settings', json=self.config)
        self.assertEqual(result.status_code, 200, result.text)
        return result

    def command(self, text, **extra):
        return self.client.post('/api/home-assistant/commands', json=dict(text=text, **extra))

    def wait_reply(self, session_id):
        for _ in range(200):
            reply = self.app.state.fleet.sessions[session_id].get('reply', {})
            if reply.get('status') not in (None, 'working'):
                return reply
            time.sleep(.01)
        self.fail('Reply did not finish')

    def test_disabled_by_default_and_protected_routes(self):
        self.assertFalse(self.client.get('/api/home-assistant/settings').json()['enabled'])
        self.assertEqual(self.command('Turn on Living Room').status_code, 409)
        self.assertEqual(self.remote.calls, [])
        self.client.post('/api/auth/logout')
        for method, path in [('GET', 'settings'), ('PUT', 'settings'), ('GET', 'entities'), ('POST', 'check'), ('POST', 'commands')]:
            response = self.client.request(method, '/api/home-assistant/' + path, json={})
            self.assertEqual(response.status_code, 401)

    def test_secret_persistence_rotation_clear_and_no_validation_echo(self):
        result = self.enable()
        self.assertNotIn('test-secret-token', result.text)
        self.assertTrue(result.json()['has_token'])
        restored = HomeAssistantService(self.app.state.auth, self.service.deliver)
        try:
            self.assertEqual(restored.settings().token.get_secret_value(), 'test-secret-token')
        finally:
            restored.close()
        config = dict(result.json());config.pop('has_token')
        self.assertEqual(self.client.put('/api/home-assistant/settings', json=config).status_code, 200)
        self.assertEqual(self.service.settings().token.get_secret_value(), 'test-secret-token')
        config['url'] = 'http://another.local'
        self.assertEqual(self.client.put('/api/home-assistant/settings', json=config).status_code, 400)
        for change in ({'url': 'http://user:test-secret-token@ha.local'}, {'token': {'secret': 'test-secret-token'}},
                       {'token': 'test-secret-token\ninvalid'}, {'entity_id': 'lock.front_door'}, {'extra': 'test-secret-token'}):
            response = self.client.put('/api/home-assistant/settings', json={**self.config, **change})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('test-secret-token', response.text)
        config.update(enabled=False, token='')
        cleared = self.client.put('/api/home-assistant/settings', json=config).json()
        self.assertFalse(cleared['enabled']);self.assertFalse(cleared['has_token'])
        self.assertEqual(self.client.post('/api/home-assistant/check').status_code, 503)

    def test_connection_discovery_and_status_are_read_only(self):
        self.enable()
        self.assertTrue(self.client.post('/api/home-assistant/check').json()['connected'])
        entities = self.client.get('/api/home-assistant/entities').json()
        self.assertEqual({row['entity_id'] for row in entities}, {'switch.living_room', 'switch.bedroom'})
        result = self.command('Hey Adam, is Living Room on?').json()
        self.assertEqual(result['text'], 'Living Room is off.')
        self.assertFalse(self.remote.actions())
        self.assertTrue(all(r.headers['authorization'] == 'Bearer test-secret-token' for r in self.remote.calls))

    def test_exact_target_on_off_confirmed_and_already_on(self):
        self.enable()
        for text in ('Turn on Bedroom', 'Turn on all lights', 'Do not turn on Living Room',
                     'Turn on Living Room and Bedroom', 'Living Room', 'Hey Adam'):
            self.assertEqual(self.command(text).json()['status'], 'ignored', text)
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(self.command('Unlock Living Room').json()['status'], 'unsupported')
        for phrase in ('Hey Adam, turn on Living Room.', 'Please switch the Living Room switch on.'):
            self.assertEqual(self.command(phrase).json()['state'], 'on')
        self.assertEqual(len(self.remote.actions()), 1)
        self.assertEqual(self.command('Turn Living Room off please').json()['state'], 'off')
        self.assertEqual(len(self.remote.actions()), 2)

    def test_discovery_keeps_off_dimmer_capability_on_one_entry(self):
        self.enable()
        self.remote.failure = lambda request: httpx.Response(200, json=[
            dict(entity_id='light.living_room', state='off', attributes=dict(
                friendly_name='Living Room', supported_color_modes=['brightness'], color_mode=None, brightness=None)),
            dict(entity_id='switch.porch', state='on', attributes=dict(friendly_name='Porch')),
            dict(entity_id='light.color_lamp', state='off', attributes=dict(
                friendly_name='Color Lamp', supported_color_modes=['rgb', 'color_temp'])),
        ])
        result = self.client.get('/api/home-assistant/entities')
        self.assertEqual(result.status_code, 200)
        entities = {row['entity_id']: row for row in result.json()}
        self.assertEqual(len(result.json()), 3)
        self.assertEqual(entities['light.living_room']['capabilities'], ['on_off', 'brightness'])
        self.assertEqual(entities['switch.porch']['capabilities'], ['on_off'])
        self.assertEqual(entities['light.color_lamp']['capabilities'], ['on_off', 'brightness', 'color_temperature', 'color'])
        self.assertFalse(self.remote.actions())

    def test_unavailable_or_unconfirmed_never_claims_success(self):
        self.enable()
        self.remote.state = 'unavailable'
        self.assertEqual(self.command('Turn on Living Room').json()['status'], 'unavailable')
        self.assertFalse(self.remote.actions())
        self.remote.state = 'off';self.remote.update = False
        self.assertEqual(self.command('Turn on Living Room').json()['status'], 'unconfirmed')
        self.assertEqual(len(self.remote.actions()), 1)

    def test_transport_failure_after_action_never_retries_or_leaks(self):
        self.enable()
        original = self.remote.handle
        def failing(request):
            if request.method == 'POST':
                self.remote.calls.append(request)
                raise httpx.ReadTimeout('test-secret-token', request=request)
            return original(request)
        self.service.client = HomeAssistantClient(httpx.MockTransport(failing))
        response = self.command('Turn on Living Room')
        self.assertEqual(response.json()['status'], 'unavailable')
        self.assertIn('Check the device state', response.json()['text'])
        self.assertNotIn('test-secret-token', response.text)
        self.assertEqual(len(self.remote.actions()), 1)

    def test_delayed_light_state_is_confirmed_without_repeating_action(self):
        self.config['entity_id'] = 'light.living_room'
        self.enable()
        requests = []
        reads_after_action = 0
        acted = False
        def delayed(request):
            nonlocal reads_after_action, acted
            requests.append(request)
            if request.url.path == '/api/services/light/turn_on':
                self.assertEqual(json.loads(request.content), {'entity_id': 'light.living_room'})
                acted = True
                return httpx.Response(200, json=[])
            self.assertEqual(request.url.path, '/api/states/light.living_room')
            if acted:
                reads_after_action += 1
            return httpx.Response(200, json=dict(entity_id='light.living_room', state='on' if reads_after_action >= 3 else 'off'))
        self.service.client = HomeAssistantClient(httpx.MockTransport(delayed))
        with patch('headend.home_assistant.time.sleep'):
            result = self.command('Turn on Living Room').json()
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['state'], 'on')
        self.assertEqual(sum(r.method == 'POST' for r in requests), 1)
        self.assertEqual(reads_after_action, 3)

    def test_redirect_auth_errors_malformed_and_oversized_responses(self):
        self.enable()
        for response in (httpx.Response(302, headers={'Location': 'http://other.local'}),
                         httpx.Response(401, text='test-secret-token'), httpx.Response(200, text='not json'),
                         httpx.Response(200, json=[]), httpx.Response(200, content=b' ' * 256001)):
            with self.subTest(status=response.status_code):
                self.remote.failure = lambda request: response
                before = len(self.remote.calls)
                result = self.client.post('/api/home-assistant/check')
                self.assertEqual(result.status_code, 503)
                self.assertNotIn('test-secret-token', result.text)
                self.assertEqual(len(self.remote.calls), before + 1)

    def test_wake_targets_origin_once_and_diagnostic_retranscription_does_not_act(self):
        self.enable()
        class SwitchSTT:
            def transcribe(inner, utterance):
                return Transcript(utterance.session_id, 'Hey Adam, turn on Living Room')
        self.app.state.jobs.factory = SwitchSTT
        other = self.client.post('/api/units', json={'name': 'Other'}).json()
        result = self.stream(**{'X-Trigger': 'wake', 'X-Wake-Model': 'hey_adam-test'}).json()
        reply = self.wait_reply(result['session_id'])
        self.assertEqual(reply['status'], 'queued', reply)
        self.assertEqual(self.poll().json()['id'], reply['page_id'])
        self.assertEqual(self.poll(other).json()['type'], 'idle')
        self.assertEqual(len(self.remote.actions()), 1)
        entry = self.app.state.fleet.sessions[result['session_id']]
        self.service.submit(entry, dict(status='done', text='Turn off Living Room'))
        for _ in range(3):self.client.get('/api/fleet')
        self.assertEqual(len(self.remote.actions()), 1)
        # The durable claim also rejects a retry after the in-memory session is gone.
        del self.app.state.fleet.sessions[result['session_id']]
        retry = self.stream(**{'X-Session-ID': result['session_id'], 'X-Trigger': 'wake', 'X-Wake-Model': 'hey_adam-test'})
        self.assertEqual(retry.status_code, 409)
        job = self.client.post('/api/clips/' + result['clip_id'] + '/transcriptions', json={}).json()
        for _ in range(100):
            if self.app.state.jobs.get(job['id'])['status'] == 'done':break
            time.sleep(.01)
        self.assertEqual(len(self.remote.actions()), 1)

    def test_stale_offline_changed_settings_touch_and_failed_jobs_do_not_act(self):
        self.enable()
        base = dict(id='test-session', unit_id=self.unit['id'], trigger='wake', started=time.time())
        for status in ('cancelled', 'error', 'no_speech'):
            self.service.submit(dict(base), dict(status=status, text='Turn on Living Room'))
        self.service.submit(dict(base, trigger='touch'), dict(status='done', text='Turn on Living Room'))
        for entry, revision in [(dict(base, started=time.time() - 31), self.service.revision),
                                (dict(base, unit_id='0'*16), self.service.revision),
                                (dict(base), self.service.revision - 1)]:
            self.service.slots.acquire()
            self.service._run(entry, 'Turn on Living Room', revision)
            self.assertEqual(entry['reply']['status'], 'ignored')
        self.assertEqual(self.remote.calls, [])

    def test_full_clip_archive_does_not_block_live_switch_command(self):
        self.enable()
        archive = self.app.state.store
        for index in range(48):
            archive.add(wav_bytes(16000, np.full(1600, index+1, dtype='<i2')), f'saved-{index}.wav')
        saved_ids = {clip['id'] for clip in archive.list()}
        class SwitchSTT:
            def transcribe(inner, utterance):
                self.assertEqual(utterance.pcm, bytes(3200))
                return Transcript(utterance.session_id, 'Hey Adam, turn on Living Room')
        self.app.state.jobs.factory = SwitchSTT
        response = self.stream(**{'X-Trigger': 'wake', 'X-Wake-Model': 'hey_adam-test'})
        self.assertEqual(response.status_code, 202, response.text)
        result = response.json()
        self.assertIsNone(result['clip_id'])
        self.assertEqual(self.wait_reply(result['session_id'])['status'], 'queued')
        self.assertEqual(len(self.remote.actions()), 1)
        self.assertEqual(self.remote.state, 'on')
        entry = self.app.state.fleet.sessions[result['session_id']]
        self.assertIn('clip library is full', entry['recording_warning'])
        self.assertEqual({clip['id'] for clip in archive.list()}, saved_ids)
        self.assertIsNone(self.app.state.jobs.get(result['job_id'])['clip_id'])
        upload = self.client.post('/api/clips', content=wav_bytes(16000, np.zeros(1600, dtype='<i2')))
        self.assertEqual(upload.status_code, 400)
        self.assertIn('Clip storage is full', upload.text)

    def test_storage_write_failure_does_not_block_transcription_or_control(self):
        self.enable()
        class SwitchSTT:
            def transcribe(inner, utterance):
                return Transcript(utterance.session_id, 'Turn on Living Room')
        self.app.state.jobs.factory = SwitchSTT
        with patch.object(self.app.state.store, 'add', side_effect=OSError('disk unavailable')):
            response = self.stream(**{'X-Trigger': 'wake', 'X-Wake-Model': 'hey_adam-test'})
        self.assertEqual(response.status_code, 202, response.text)
        result = response.json()
        self.assertEqual(self.wait_reply(result['session_id'])['status'], 'queued')
        self.assertEqual(len(self.remote.actions()), 1)
        self.assertIn('storage is unavailable', self.app.state.fleet.sessions[result['session_id']]['recording_warning'])

    def test_disabling_during_state_lookup_prevents_action(self):
        self.enable()
        original = self.service.client.device_state
        def state(settings, entity_id):
            result = original(settings, entity_id)
            self.service.save(HomeAssistantSettings(**{**self.config, 'enabled': False}))
            return result
        with patch.object(self.service.client, 'device_state', side_effect=state):
            self.assertEqual(self.command('Turn on Living Room').json()['status'], 'ignored')
        self.assertFalse(self.remote.actions())

    def test_queue_bound_and_reply_failure_preserve_action_result(self):
        self.enable()
        for _ in range(4):self.service.slots.acquire()
        try:self.assertEqual(self.command('Turn on Living Room').status_code, 429)
        finally:
            for _ in range(4):self.service.slots.release()
        with patch.object(self.service, 'deliver', side_effect=ValueError('too long')):
            result = self.command('Turn on Living Room', unit_id=self.unit['id']).json()
        self.assertEqual(result['state'], 'on')
        self.assertIn('delivery_error', result)
        self.assertEqual(len(self.remote.actions()), 1)

    def test_weather_still_routes_to_existing_handler(self):
        self.enable()
        with patch.object(self.app.state.fleet.queries, 'submit') as weather:
            self.app.state.fleet.transcription_done(dict(trigger='wake'), dict(status='done', text="What's the weather?"))
            weather.assert_called_once()
        self.assertFalse(self.remote.calls)


if __name__ == '__main__':
    unittest.main()
