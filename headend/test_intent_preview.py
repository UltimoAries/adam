"""Preview uses model routing and capability validation, but never dispatches."""
import json
import unittest
from unittest.mock import patch

import httpx

from . import test_fleet, test_ha_inventory
from .conversation import ConversationSettings


def proposal(kind='steps', steps=None, category=None, clarification=''):
    if kind == 'steps':
        return dict(kind=kind, steps=steps if steps is not None else [dict(entity_id='light.lounge', action='brightness', value=100)])
    if kind == 'summary':
        return dict(kind=kind, category=category)
    if kind == 'clarify':
        return dict(kind=kind, clarification=clarification)
    return dict(kind=kind)


class IntentPreviewTests(unittest.TestCase):
    setUp = test_ha_inventory.InventoryTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    save = test_ha_inventory.InventoryTests.save
    refresh = test_ha_inventory.InventoryTests.refresh

    def install_model(self, result=None):
        self.calls = []
        self.output = result if result is not None else proposal()
        self.chat = self.app.state.fleet.conversation
        # Preview is available even when general conversation is disabled.
        self.chat.save(ConversationSettings(enabled=False, remote_url='http://model.test'))
        def handle(request):
            self.calls.append(request)
            return httpx.Response(200, json=dict(done=True, message=dict(content=json.dumps(self.output))))
        self.chat.transport = httpx.MockTransport(handle)

    def preview(self, text='Can we make the Living Room fully bright?', **kwargs):
        return self.client.post('/api/home-assistant/intent-preview', json=dict(text=text, **kwargs))

    def test_structured_preview_cannot_execute_speak_or_read_live_states(self):
        self.install_model()
        with (patch.object(self.service.client, 'control', side_effect=AssertionError('control')),
             patch.object(self.service.client, 'entities', side_effect=AssertionError('live read')),
             patch.object(self.app.state.fleet.queries, 'deliver', side_effect=AssertionError('speech'))):
            result = self.preview().json()
        self.assertEqual(result['status'], 'preview', result)
        self.assertFalse(result['executed'])
        self.assertEqual(result['steps'][0]['value'], 100)
        self.assertEqual(result['steps'][0]['name'], 'Living Room')
        self.assertEqual(self.actions, [])
        self.assertEqual(self.app.state.auth.pages(), [])
        with self.app.state.auth.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM home_assistant_commands').fetchone()[0], 0)
        body = json.loads(self.calls[0].content)
        self.assertIn('format', body)
        self.assertNotIn('tools', body)
        self.assertNotIn('test-token', str(body))
        self.assertNotIn('http://ha.local', str(body))
        catalog = json.loads(body['messages'][-1]['content'])
        self.assertNotIn('sensor.battery', str(catalog))
        self.assertNotIn('service', str(catalog['devices']))

    def test_explicit_preview_context_is_not_shared_with_live_dialogue(self):
        self.install_model()
        result = self.preview('Can we set it to 100%?', context_entity_id='light.lounge')
        self.assertEqual(result.json()['status'], 'preview')
        self.assertEqual(json.loads(json.loads(self.calls[0].content)['messages'][-1]['content'])['previous_entity_id'], 'light.lounge')
        self.assertEqual(self.app.state.fleet.dialogue.contexts, {})
        self.preview('Can we set it to 100%?')
        self.assertIsNone(json.loads(json.loads(self.calls[-1].content)['messages'][-1]['content'])['previous_entity_id'])

    def test_invalid_controls_values_negation_and_inventory_are_rejected(self):
        self.install_model()
        for step in (dict(entity_id='light.lounge', action='brightness', value=101),
                     dict(entity_id='light.lounge', action='unlock', value=None),
                     dict(entity_id='sensor.battery', action='status', value=None),
                     dict(entity_id='light.unknown', action='on', value=None),
                     dict(entity_id='light.lounge', action='status', value=42),
                     dict(entity_id='climate.house', action='temperature', value=68)):
            self.output = proposal(steps=[step])
            result = self.preview().json()
            self.assertEqual(result['status'], 'rejected', result)
            self.assertNotIn('proposal', result)
        self.output = proposal()
        self.assertEqual(self.preview('Can we not brighten Living Room?').json()['status'], 'rejected')
        self.save('light.lounge', enabled=False)
        self.assertEqual(self.preview().json()['status'], 'rejected')
        self.assertEqual(self.preview(context_entity_id='light.lounge').status_code, 400)
        self.assertEqual(self.actions, [])

    def test_summary_clarification_and_general_questions(self):
        self.install_model()
        for kind, category, clarification in (('summary', 'lights', ''), ('clarify', None, 'Which light?'), ('conversation', None, '')):
            self.output = proposal(kind, steps=[], category=category, clarification=clarification)
            result = self.preview().json()
            self.assertEqual(result['status'], 'preview', result)
            self.assertEqual(result['kind'], kind)
            self.assertEqual(result['steps'], [])
            self.assertFalse(result['executed'])

    def test_duplicate_and_conflicting_names_rejected(self):
        self.install_model(proposal(steps=[dict(entity_id='light.lounge', action='on', value=None)]*2))
        self.assertEqual(self.preview().json()['status'], 'rejected')
        self.output = proposal()
        self.save('light.bedroom', name='Living Room')
        self.assertEqual(self.preview().json()['status'], 'rejected')

    def test_model_schema_errors_and_outage_use_existing_fallback(self):
        self.install_model()
        def handle(request):
            self.calls.append(request)
            value = dict(self.output, service='arbitrary') if request.url.host == 'model.test' else self.output
            return httpx.Response(200, json=dict(done=True, message=dict(content=json.dumps(value))))
        self.chat.transport = httpx.MockTransport(handle)
        self.assertEqual(self.preview().json()['provider'], 'local')
        self.assertEqual(len(self.calls), 2)
        self.calls.clear()
        self.assertEqual(self.preview().json()['provider'], 'local')
        self.assertEqual(len(self.calls), 1)
        self.chat.transport = httpx.MockTransport(lambda request: httpx.Response(503))
        self.assertEqual(self.preview().json()['status'], 'unavailable')
        self.assertEqual(self.actions, [])

    def test_settings_change_during_model_request_invalidates_preview(self):
        self.install_model()
        def handle(request):
            self.save('light.lounge', enabled=False)
            return httpx.Response(200, json=dict(done=True, message=dict(content=json.dumps(self.output))))
        self.chat.transport = httpx.MockTransport(handle)
        result = self.preview().json()
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('steps', result)

    def test_preview_auth_csrf_input_and_queue_limits(self):
        self.install_model()
        self.assertEqual(self.preview(' ').status_code, 400)
        self.assertEqual(self.preview('x'*301).status_code, 422)
        self.assertEqual(self.preview(execute=True).status_code, 422)
        response = self.client.post('/api/home-assistant/intent-preview', json=dict(text='hello'), headers={'Origin':'http://elsewhere.test'})
        self.assertEqual(response.status_code, 403)
        for _ in range(4):
            self.chat.slots.acquire()
        try:
            self.assertEqual(self.preview().status_code, 429)
        finally:
            for _ in range(4):
                self.chat.slots.release()
        self.client.post('/api/auth/logout')
        self.assertEqual(self.preview().status_code, 401)
        self.assertEqual(self.calls, [])
