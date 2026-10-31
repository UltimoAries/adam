"""Countdown timer parsing, persistence, multi-speaker scoping, and device poll reconciliation."""
import tempfile
import time
import unittest

from . import test_fleet
from .state import State
from .stt import Transcript
from .timers import TimerService, format_duration_spoken, parse_duration_seconds, timer_intent


class TimerParsingAndServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 1_761_868_800.0
        self.state = State(self.temp.name)
        self.kitchen = self.state.add_unit('Kitchen')
        self.office = self.state.add_unit('Office')
        self.service = TimerService(self.state, lambda res, uid: res, clock=lambda: self.now)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_duration_parsing_spoken_words_and_formatting(self):
        self.assertEqual(parse_duration_seconds('10 minutes')[0], 600)
        self.assertEqual(parse_duration_seconds('two and a half minutes')[0], 150)
        self.assertEqual(parse_duration_seconds('half an hour')[0], 1800)
        self.assertEqual(parse_duration_seconds('an hour and fifteen minutes')[0], 4500)
        self.assertEqual(parse_duration_seconds('forty-five seconds')[0], 45)
        self.assertEqual(format_duration_spoken(3665), '1 hour, 1 minute, and 5 seconds')
        self.assertEqual(timer_intent('Hey Adam, set a 12 minute pasta timer'), dict(action='create', seconds=720, label='Pasta'))
        self.assertIsNone(timer_intent('Do not set a timer for 10 minutes'))

    def test_multi_speaker_panel_scoping_fleet_lookup_extend_and_cancel(self):
        r1 = self.service.answer('Hey Adam, set a 10 minute timer for pasta', self.kitchen['id'])
        self.assertEqual(r1['status'], 'answered')
        self.assertIn('Pasta timer set for 10 minutes', r1['text'])

        r2 = self.service.answer('Set a 25 minute laundry timer', self.office['id'])
        self.assertEqual(r2['status'], 'answered')

        # Display/panel timers stay strictly scoped to their originating speaker unit.
        kitchen_panel = self.service.reconcile_poll(self.kitchen['id'])
        office_panel = self.service.reconcile_poll(self.office['id'])
        self.assertEqual([t['label'] for t in kitchen_panel], ['PASTA'])
        self.assertEqual([t['label'] for t in office_panel], ['LAUNDRY'])

        # Unnamed lookup prefers local unit; named lookup works across the fleet.
        self.assertIn('Pasta timer has 10 minutes left', self.service.answer('How much time is left?', self.kitchen['id'])['text'])
        cross = self.service.answer('How much time is left on the laundry timer?', self.kitchen['id'])
        self.assertIn('On Office, the Laundry timer has 25 minutes left', cross['text'])

        # Extend by name and verify updated remaining time.
        ext = self.service.answer('Add two minutes to the pasta timer', self.kitchen['id'])
        self.assertEqual(ext['status'], 'answered')
        self.assertIn('12 minutes left', ext['text'])

        # Add a second timer on Kitchen so unnamed cancel is ambiguous, while named cancel is exact.
        self.service.answer('Set a 45 second tea timer', self.kitchen['id'])
        amb = self.service.answer('Cancel the timer', self.kitchen['id'])
        self.assertEqual(amb['status'], 'ambiguous')
        exact = self.service.answer('Cancel the tea timer', self.kitchen['id'])
        self.assertEqual(exact['status'], 'answered')
        self.assertEqual([t['label'] for t in self.service.reconcile_poll(self.kitchen['id'])], ['PASTA'])

        # Fleet-wide cancel by name from another room works cleanly.
        cancel_remote = self.service.answer('Cancel the laundry timer', self.kitchen['id'])
        self.assertIn('Cancelled the Laundry timer', cancel_remote['text'])
        self.assertEqual(self.service.reconcile_poll(self.office['id']), [])

    def test_bare_stop_only_claims_when_ringing_and_persistence_survives_restart(self):
        created = self.service.create_timer(self.kitchen['id'], 30, 'Eggs')
        # While still counting down, bare "stop" must NOT claim wake utterances.
        self.assertFalse(self.service.matches_utterance('Hey Adam, stop', self.kitchen['id']))

        # Persistence across restart before expiry:
        restored = TimerService(self.state, lambda res, uid: res, clock=lambda: self.now + 10)
        try:
            active = restored.list_timers()
            self.assertEqual(len(active), 1)
            self.assertEqual(active[0]['id'], created['id'])
            self.assertEqual(active[0]['remaining_seconds'], 20)
        finally:
            restored.close()

        # Advance into the 60s ringing window on Kitchen:
        self.now += 35
        self.assertTrue(self.service.matches_utterance('Hey Adam, stop', self.kitchen['id']))
        self.assertFalse(self.service.matches_utterance('Hey Adam, stop', self.office['id']))
        stopped = self.service.answer('Hey Adam, stop', self.kitchen['id'])
        self.assertEqual(stopped['text'], 'Timer stopped.')
        self.assertEqual(self.service.list_timers(), [])

    def test_bounds_and_per_unit_limit(self):
        self.assertEqual(self.service.answer('Set a timer', self.kitchen['id'])['status'], 'unsupported')
        self.assertEqual(self.service.answer('Set a 2 second timer', self.kitchen['id'])['status'], 'unsupported')
        for idx in range(8):
            self.service.create_timer(self.kitchen['id'], 60 + idx, f'T{idx}')
        self.assertEqual(self.service.answer('Set a 1 minute timer', self.kitchen['id'])['status'], 'unsupported')
        # Device poll caps panel payload to the 6 soonest timers.
        self.assertEqual(len(self.service.reconcile_poll(self.kitchen['id'])), 6)


class TimerIntegrationTests(unittest.TestCase):
    tearDown = test_fleet.FleetTests.tearDown
    headers = test_fleet.FleetTests.headers
    poll = test_fleet.FleetTests.poll
    stream = test_fleet.FleetTests.stream

    def setUp(self):
        test_fleet.FleetTests.setUp(self)

    def wait_reply(self, session_id):
        for _ in range(200):
            entry = self.app.state.fleet.sessions[session_id]
            if entry.get('reply', {}).get('status') not in (None, 'working'):
                return entry['reply']
            time.sleep(.01)
        self.fail('Timer reply did not finish')

    def test_wake_timer_creation_device_poll_dismissal_and_dashboard_api(self):
        class TimerSTT:
            def transcribe(inner, utterance):
                return Transcript(utterance.session_id, 'Hey Adam, set a 5 minute pasta timer')

        self.app.state.jobs.factory = TimerSTT
        result = self.stream(**{'X-Trigger': 'wake', 'X-Wake-Model': 'hey_adam-test'}).json()
        reply = self.wait_reply(result['session_id'])
        self.assertEqual(reply['status'], 'queued', reply)
        self.assertIn('Pasta timer set for 5 minutes', reply['text'])

        # Device poll delivers both the spoken page offer and panel.timers countdown metadata.
        polled = self.poll().json()
        self.assertEqual(len(polled['panel']['timers']), 1)
        timer_id = polled['panel']['timers'][0]['id']
        self.assertEqual(polled['panel']['timers'][0]['label'], 'PASTA')

        # Fleet status includes active timers for the dashboard.
        fleet = self.client.get('/api/fleet').json()
        self.assertEqual(len(fleet['timers']), 1)
        self.assertEqual(fleet['timers'][0]['id'], timer_id)

        # Device touch dismissal reconciles back to head-end via ?dismissed=<id>.
        after_touch = self.client.get(
            f"/api/units/{self.unit['id']}/poll?firmware=v28&dismissed={timer_id}",
            headers=self.headers(),
        ).json()
        self.assertEqual(after_touch['panel']['timers'], [])
        self.assertEqual(self.client.get('/api/timers').json()['timers'], [])

        # Dashboard API create + delete + auth checks.
        created = self.client.post('/api/timers', json=dict(unit_id=self.unit['id'], seconds=90, label='Tea'))
        self.assertEqual(created.status_code, 201, created.text)
        tid = created.json()['id']
        self.assertEqual(self.client.delete('/api/timers/not-a-valid-id').status_code, 400)
        self.assertEqual(self.client.delete(f'/api/timers/{tid}').status_code, 200)
        self.assertEqual(self.client.delete(f'/api/timers/{tid}').status_code, 404)

        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/timers').status_code, 401)
        self.assertEqual(self.client.post('/api/timers', json=dict(unit_id=self.unit['id'], seconds=60)).status_code, 401)


if __name__ == '__main__':
    unittest.main()

