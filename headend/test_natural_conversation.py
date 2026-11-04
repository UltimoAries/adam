"""Contextual commands must retain identity, scope and existing safety checks."""
import unittest
import time
import uuid
from unittest.mock import patch

from . import test_status_queries, test_ha_inventory, test_fleet


class NaturalConversationTests(unittest.TestCase):
    setUp = test_ha_inventory.InventoryTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    refresh = test_ha_inventory.InventoryTests.refresh
    save = test_ha_inventory.InventoryTests.save
    ask = test_status_queries.StatusTests.ask

    def test_single_reference_controls_and_name_correction(self):
        self.rows[0]['state'] = 'on'
        self.ask('Which lights are on?')
        result = self.ask('Turn that one off')
        self.assertEqual(result['entity_id'], 'light.lounge')
        self.assertEqual(self.actions[-1][1]['entity_id'], 'light.lounge')
        self.rows[1]['state'] = 'on'
        result = self.ask('Actually, the bedroom')
        self.assertEqual(result['entity_id'], 'light.bedroom')
        self.assertIn('not undone', result['text'])
        self.assertEqual(self.rows[0]['state'], 'off')
        self.rows[1]['state'] = 'unavailable'
        self.assertIn('Bedroom', self.ask("Why is it unavailable?")['text'])

    def test_ambiguous_reference_asks_and_explicit_selection_resolves(self):
        self.rows[0]['state'] = self.rows[1]['state'] = 'on'
        self.ask('Which lights are on?')
        answer = self.ask('Turn that one off')
        self.assertTrue(answer['needs_clarification'])
        self.assertEqual(self.actions, [])
        self.assertEqual(self.ask('Bedroom')['entity_id'], 'light.bedroom')
        self.assertEqual(len(self.actions), 1)

    def test_summary_with_active_and_unavailable_devices_is_ambiguous_for_actions(self):
        self.rows[0]['state'] = 'on'
        self.rows[1]['state'] = 'unavailable'
        self.ask('Which lights are on?')
        self.assertTrue(self.ask('Turn that one off')['needs_clarification'])
        self.assertEqual(self.actions, [])

    def test_temperature_correction_preserves_heat_side_and_bounds(self):
        self.rows[3]['attributes'].update(target_temp_low=70, target_temp_high=76, current_temperature=66)
        self.refresh()
        self.assertEqual(self.ask('Set the heat to 65')['status'], 'answered')
        result = self.ask('Make that 68')
        self.assertEqual(result['action'], 'heating_temperature')
        self.assertEqual(self.actions[-1][1]['target_temp_low'], 68)
        self.assertEqual(self.actions[-1][1]['target_temp_high'], 76)
        count = len(self.actions)
        self.assertEqual(self.ask('Make that 80')['status'], 'unsupported')
        self.assertEqual(len(self.actions), count)

    def test_reference_does_not_cross_tabs_expiry_disable_or_new_topic(self):
        self.rows[0]['state'] = 'on'
        self.ask('Which lights are on?')
        self.assertTrue(self.ask('Turn it off', context='b'*32)['needs_clarification'])
        self.assertEqual(self.actions, [])
        self.save('light.lounge', enabled=False)
        self.ask('Turn it off')
        self.assertEqual(self.actions, [])
        self.save('light.lounge', enabled=True)
        self.ask('Which lights are on?')
        self.ask('What time is it?')
        self.ask('Turn it off')
        self.assertEqual(self.actions, [])

    def test_outage_explanation_uses_live_diagnostics_without_guessing(self):
        self.rows[1].update(state='unavailable', last_changed='2026-11-02T12:00:00+00:00')
        self.rows[1]['attributes'].update(battery_level=12, error='secret fixture-token', restored=True)
        with patch.object(self.app.state.fleet.conversation, 'submit', side_effect=AssertionError('LLM used')):
            self.ask('Which lights are on?')
            self.ask('Which one is unavailable?')
            answer = self.ask("Do we know why it's unavailable?")
            self.assertIn('Bedroom', answer['text'])
            self.assertIn('not provided a cause', answer['text'])
            self.assertIn('State last changed', str(answer['details']))
            self.assertNotIn('fixture-token', str(answer))
            self.rows[1]['state'] = 'off'
            self.assertIn('now reports off', self.ask('Why is that?')['text'])
        self.assertEqual(self.actions, [])

    def test_natural_endings_clear_context(self):
        for ending in ("No, that's it", "I'm good", "We're good", 'Never mind'):
            self.ask('Which lights are on?')
            self.assertEqual(self.ask(ending)['text'], 'Okay.')
            self.assertEqual(self.ask('Which one is unavailable?')['status'], 'unsupported')

    def test_pending_action_cannot_survive_settings_change(self):
        self.rows[0]['state'] = self.rows[1]['state'] = 'on'
        self.ask('Which lights are on?')
        self.ask('Turn it off')
        self.save('light.bedroom', enabled=False)
        self.ask('Bedroom')
        self.assertEqual(self.actions, [])

    def test_recorded_polite_brightness_requests_and_followup_percent(self):
        self.save('light.lounge', name='Living Room Light')
        with patch.object(self.app.state.fleet.conversation, 'submit', side_effect=AssertionError('LLM used')):
            self.ask('Hey Adam, turn on the living room light.')
            for phrase in ('Can we set it to 100%?', 'Could you please set it to 45 percent?',
                           'Can we set the living room light to 100%?'):
                self.ask(phrase)
                self.assertEqual(self.actions[-1][1]['entity_id'], 'light.lounge')
                self.assertIn('brightness_pct', self.actions[-1][1])
        self.assertEqual([a[1].get('brightness_pct') for a in self.actions], [None, 100, 45, 100])

    def test_combined_light_request_keeps_identity_for_next_followup(self):
        with patch.object(self.app.state.fleet.conversation, 'submit', side_effect=AssertionError('LLM used')):
            result = self.ask('Hey Adam turn on the living room light and set it to 30%')
            self.assertEqual(result['entity_id'], 'light.lounge')
            self.assertEqual(result['status'], 'sent')
            result = self.ask('Can we set that to 50%?')
            self.assertEqual(result['entity_id'], 'light.lounge')
        self.assertEqual([a[1] for a in self.actions], [dict(entity_id='light.lounge',brightness_pct=30),
                                                      dict(entity_id='light.lounge',brightness_pct=50)])

    def test_polite_context_commands_retain_negation_limits_and_isolation(self):
        self.ask('Turn on Living Room')
        count = len(self.actions)
        for phrase in ('Can we not set it to 100%?', 'Could you please not turn it off?',
                       'Can we set it to -10%?', 'Can we set it to 101%?'):
            self.ask(phrase)
            self.assertEqual(len(self.actions), count)
        self.ask('Turn on Living Room')
        self.assertTrue(self.ask('Can we set it to 100%?', context='b'*32)['needs_clarification'])
        self.assertEqual(len(self.actions), count)

    def test_recorded_light_questions_and_recheck_read_live_state(self):
        self.rows[0]['state'] = 'on'
        with patch.object(self.app.state.fleet.conversation, 'submit', side_effect=AssertionError('LLM used')):
            for phrase in ('Now which lights are on?', 'Hey Adam did I leave the lights on?',
                           'Have we left any lights on?'):
                self.assertIn('Living Room', self.ask(phrase)['text'])
            self.rows[0]['state'] = 'off'
            self.assertIn('off', self.ask('Yeah, check.')['text'])
            self.assertTrue(self.ask('Yeah, check.', context='b'*32)['needs_clarification'])
            self.ask('Never mind')
            self.assertTrue(self.ask('Check again')['needs_clarification'])
        self.assertEqual(self.actions, [])

    def test_backyard_spelling_keeps_exact_identity_and_detects_collisions(self):
        self.save('light.lounge', name='Garage Back Yard Light', use_default_name=False)
        self.ask('Turn on the garage backyard light')
        self.assertEqual(self.actions[-1][1]['entity_id'], 'light.lounge')
        self.save('light.bedroom', name='Garage Backyard Light', use_default_name=False)
        self.assertEqual(self.ask('Turn off the garage backyard light')['status'], 'ambiguous')
        self.assertEqual(len(self.actions), 1)

    def light_group(self, count=3):
        self.rows[0]['state'] = self.rows[1]['state'] = 'on'
        for i in range(max(0, count-2)):
            self.rows.append(dict(entity_id=f'light.group_{i}', state='on',
                                 attributes=dict(friendly_name=f'Group Light {i}', supported_color_modes=['brightness'])))
        self.refresh()

    def test_three_light_summary_names_every_light_and_plural_controls_exact_group(self):
        self.light_group()
        self.rows.append(dict(entity_id='light.other', state='off', attributes=dict(friendly_name='Other Light')))
        self.refresh()
        with patch.object(self.app.state.fleet.conversation, 'submit', side_effect=AssertionError('LLM used')):
            summary = self.ask('Hey Adam, did I leave the lights on?')
            self.assertEqual(summary['text'], '3 lights are on: Bedroom, Group Light 0, and Living Room.')
            # A light switched on after the summary must not join "those".
            self.rows[-1]['state'] = 'on'
            result = self.ask('Can we turn those off?')
        self.assertEqual(result['status'], 'answered', result)
        self.assertEqual({a[1]['entity_id'] for a in self.actions}, {'light.lounge','light.bedroom','light.group_0'})
        self.assertEqual(len(self.actions), 3)
        self.assertEqual(self.rows[-1]['state'], 'on')

    def test_plural_forms_use_authorized_wake_followup_and_do_not_replay(self):
        self.light_group()
        fleet = self.app.state.fleet
        self.app.state.auth.heartbeat(self.unit['id'], 'fixture', 'wake')
        for phrase in ('Can we turn those off?', 'Turn them off', 'Please switch off those lights'):
            for row in self.rows:
                if row['entity_id'] in ('light.lounge','light.bedroom','light.group_0'): row['state']='on'
            first = dict(id=uuid.uuid4().hex, trigger='wake', unit_id=self.unit['id'], started=time.time())
            follow = dict(id=uuid.uuid4().hex, trigger='followup', unit_id=self.unit['id'], started=time.time())
            for entry, text in ((first, 'Did I leave the lights on?'), (follow, phrase)):
                fleet.transcription_done(entry, dict(status='done', text=text))
                for _ in range(200):
                    if entry.get('reply', {}).get('status') != 'working': break
                    time.sleep(.01)
                self.assertEqual(entry['reply']['status'], 'queued', entry)
            count = len(self.actions)
            # Replaying the internal worker after a restart still hits its durable claim.
            self.service.slots.acquire()
            self.service._run(follow, phrase, self.service.revision)
            self.assertEqual(len(self.actions), count)
        self.assertEqual(len(self.actions), 9)

    def test_plural_context_is_scoped_expires_and_requires_successful_light_summary(self):
        self.light_group()
        self.ask('Which lights are on?')
        self.assertTrue(self.ask('Turn those off', context='b'*32)['needs_clarification'])
        fleet = self.app.state.fleet
        for item in fleet.status_queries.contexts.values(): item['time'] -= 91
        self.assertTrue(self.ask('Turn those off')['needs_clarification'])
        self.ask('Which lights are on?')
        self.ask('What time is it?')
        self.assertTrue(self.ask('Turn those off')['needs_clarification'])
        self.ask('Which lights are on?')
        self.ask('Never mind')
        self.assertTrue(self.ask('Turn those off')['needs_clarification'])
        self.ask('Are all locks locked?')
        self.assertTrue(self.ask('Turn them off')['needs_clarification'])
        self.assertEqual(self.actions, [])

    def test_plural_context_rejects_unavailable_empty_oversized_and_changed_groups(self):
        self.light_group(5)
        self.ask('Which lights are on?')
        self.assertIn('up to 4', self.ask('Turn those off')['text'])
        for row in self.rows:
            if row['entity_id'].startswith('light.'): row['state']='off'
        self.ask('Which lights are on?')
        self.assertIn('No lights were reported on', self.ask('Turn those off')['text'])
        self.rows[0]['state']='on'; self.rows[1]['state']='unavailable'
        self.ask('Which lights are on?')
        self.assertIn('unavailable', self.ask('Turn those off')['text'])
        self.rows[1]['state']='on'
        self.ask('Which lights are on?')
        self.save('light.bedroom', enabled=False)
        self.assertIn('settings changed', self.ask('Turn those off')['text'])
        self.save('light.bedroom', enabled=True)
        self.ask('Which lights are on?')
        self.rows.pop(1)
        self.refresh()
        self.assertTrue(self.ask('Turn those off')['needs_clarification'])
        self.assertEqual(self.actions, [])

    def test_plural_negation_singular_ambiguity_and_diagnostic_captures_do_not_act(self):
        self.light_group()
        self.ask('Which lights are on?')
        self.ask('Can we not turn those off?')
        self.ask('Which lights are on?')
        self.assertTrue(self.ask('Turn that one off')['needs_clarification'])
        self.ask('Which lights are on?')
        entry = dict(id=uuid.uuid4().hex, trigger='touch', unit_id=self.unit['id'], started=time.time())
        self.app.state.fleet.transcription_done(entry, dict(status='done', text='Turn those off'))
        self.assertNotIn('reply', entry)
        self.assertEqual(self.actions, [])
