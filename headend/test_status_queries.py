"""Live status, context isolation/expiry and truthful model-routing answers."""
import time
import uuid
import unittest
from unittest.mock import patch

from . import test_ha_inventory, test_fleet
from .ha_devices import summary_intent
from .status_queries import status_intent


class StatusTests(unittest.TestCase):
    setUp = test_ha_inventory.InventoryTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    refresh = test_ha_inventory.InventoryTests.refresh
    save = test_ha_inventory.InventoryTests.save

    def ask(self, text, context='a'*32, **extra):
        body = dict(request_id=uuid.uuid4().hex, text=text, context_id=context, **extra)
        response = self.client.post('/api/commands/text', json=body)
        self.assertEqual(response.status_code, 202, response.text)
        for _ in range(200):
            result = self.client.get('/api/commands/text/'+body['request_id']).json()
            if result['reply']['status'] != 'working':
                return result['reply']
            time.sleep(.01)
        self.fail('Status answer timed out')

    def test_flexible_phrases_and_action_boundaries(self):
        for text in ('Hey, Adam, what lights are on right now?', 'Which lights are currently on?',
                     'Can you please tell me which of the lights are on at the moment?',
                     'Show me what lights are on please'):
            self.assertEqual(summary_intent(text), 'lights', text)
            self.assertEqual(status_intent(text)['kind'], 'summary')
        for text in ('turn on the lights', 'what lights are on and turn them off',
                     'restart the GPU', 'set the model to local', 'do not turn off the lights'):
            self.assertIsNone(status_intent(text), text)

    def test_live_summary_named_unavailable_followup_and_recovery(self):
        self.rows[0]['state'] = 'on'; self.rows[1]['state'] = 'unavailable'
        with patch.object(self.app.state.fleet.conversation, 'submit', side_effect=AssertionError('LLM used')):
            answer = self.ask('What lights are on right now?')
            self.assertIn('Living Room', answer['text'])
            self.assertIn('Unavailable: Bedroom', answer['text'])
            self.assertIn('Bedroom', self.ask("What's the unavailable light?")['text'])
            self.rows[1]['state'] = 'off'
            self.assertEqual(self.ask('Is it back online?')['text'], 'Bedroom is available again and is off.')
        self.assertEqual(self.actions, [])
        self.assertEqual(self.app.state.auth.pages(), [])

    def test_tabs_users_expiry_and_unrelated_commands_do_not_share_context(self):
        self.rows[1]['state'] = 'unavailable'
        self.ask('Which lights are on?')
        self.assertEqual(self.ask('Which one is unavailable?', context='b'*32)['status'], 'unsupported')
        service = self.app.state.fleet.status_queries
        for value in service.contexts.values(): value['time'] -= 91
        self.assertEqual(self.ask('Which one is unavailable?')['status'], 'unsupported')
        self.ask('Which lights are on?')
        self.ask('what time is it')
        self.assertEqual(self.ask('Which one is unavailable?')['status'], 'unsupported')
        self.ask('Which lights are on?')
        self.client.post('/api/auth/logout')
        self.client.post('/api/auth/login',json={'username':'admin','password':'test-only-password-123'})
        self.assertEqual(self.ask('Which one is unavailable?')['status'], 'unsupported')

    def test_named_followup_and_removed_device_do_not_retarget(self):
        self.ask('Which lights are on?')
        self.assertEqual(self.ask('What about Bedroom?')['text'], 'Bedroom is off.')
        self.save('light.bedroom', imported=False)
        self.assertEqual(self.ask('Is it back online?')['status'], 'unsupported')
        self.assertEqual(self.actions, [])

    def test_unavailable_followup_does_not_invent_a_problem(self):
        self.rows[0]['state'] = 'on'
        self.ask('Which lights are on?')
        self.assertIn('None of the devices', self.ask('Which one is unavailable?')['text'])
        self.ask('Which lights are on?')
        self.assertIn('No devices were reported', self.ask('Which device was offline?')['text'])
        self.ask('Which lights are on?')
        self.rows[1]['state'] = 'unknown'
        self.assertEqual(self.ask('Which one is unavailable?')['text'], 'Bedroom is unknown.')

    def test_speaker_offline_status_and_scope(self):
        self.app.state.auth.heartbeat(self.unit['id'], 'fixture', 'wake')
        other = self.client.post('/api/units',json={'name':'Den ADAM'}).json()
        result = self.ask('Which speakers are offline?')
        self.assertIn('Den ADAM is offline', result['text'])
        self.app.state.auth.heartbeat(other['id'], 'fixture', 'wake')
        self.assertIn('Den ADAM is online', self.ask('Is it back online?')['text'])
        self.assertIn('Kitchen is online', self.ask('Is Kitchen online?')['text'])
        fleet = self.app.state.fleet
        entry = dict(id=uuid.uuid4().hex, trigger='wake', unit_id=self.unit['id'], started=time.time())
        fleet.transcription_done(entry, dict(status='done', text='Which speakers are offline?'))
        for _ in range(100):
            if entry.get('reply',{}).get('status')!='working':break
            time.sleep(.01)
        self.assertEqual(entry['reply']['status'], 'queued')
        self.assertNotIn('speaker:'+other['id'], fleet.status_queries.contexts)

    def test_ha_failure_and_missing_history_never_guess(self):
        self.assertIn('do not have an availability history', self.ask('Which device was offline?')['text'])
        with patch.object(self.service.client, 'entities', side_effect=RuntimeError('secret URL must not leak')):
            result = self.ask('Which devices are unavailable?')
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('secret', str(result))
        self.client.put('/api/home-assistant/settings',json=dict(self.config, enabled=False))
        self.assertIn('disabled', self.ask('What lights are on?')['text'])

    def test_model_alias_last_route_and_no_generation(self):
        service = self.app.state.fleet.conversation
        service.options.enabled = True; service.last_provider = 'remote'
        service.options.remote_model = 'adam:latest'
        async def metadata(url, transport=None):
            if url.endswith('/api/tags'):
                return {'models':[{'name':'adam:latest','digest':'same'}, {'name':'qwen3.5:9b','digest':'same'}]}
            return {'models':[{'name':'adam:latest','digest':'same'}]}
        with patch('headend.status_queries.catalog_json',side_effect=metadata), \
             patch.object(service, 'answer',side_effect=AssertionError('LLM generated status')):
            result = self.ask('Which brain are you using?')
            self.assertIn('last conversation used the GPU', result['text'])
            self.assertIn('qwen3.5:9b', result['text'])
            service.last_provider = 'local'
            self.assertIn('last conversation used the Pi', self.ask('Which brain are you using?')['text'])
        self.assertNotIn('gpu-host.local', str(result))
        self.assertIn('GPU loaded models: adam:latest', result['details'])

    def test_model_host_failure_does_not_claim_loaded(self):
        service = self.app.state.fleet.conversation
        service.options.enabled = True; service.last_provider = None
        with patch('headend.status_queries.catalog_json', side_effect=RuntimeError('network unavailable')):
            result = self.ask('What models are loaded?')
        self.assertIn('cannot confirm loaded models', result['text'])
        self.assertTrue(any('check unavailable' in x for x in result['details']))
        self.assertEqual(result['status'], 'answered')
