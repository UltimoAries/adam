"""Storage delays must not delay finalized wake actions or lose transcripts."""
import threading
import struct
import time
import unittest
from unittest.mock import patch

from . import test_fleet


class LatencyTests(unittest.TestCase):
    setUp = test_fleet.FleetTests.setUp
    tearDown = test_fleet.FleetTests.tearDown
    headers = test_fleet.FleetTests.headers
    stream = test_fleet.FleetTests.stream

    def test_short_silence_endpoint_accepts_complete_audio_but_rejects_truncation(self):
        # Two seconds pre-roll + one second live: the v29 minimum.
        for live, valid in ((50, True), (49, False)):
            count = 100 + live
            packets = b''.join(struct.pack('<I', i) + bytes(640) for i in range(count))
            end = struct.pack('<II', 0xffffffff, count) + bytes([1]) + bytes(635)
            headers = {'X-Audio-Frames':'750', 'X-Audio-Preroll-Ms':'2000',
                       'X-Audio-Mode':'silence-v1', 'X-Trigger':'wake', 'X-Wake-Model':'hey_adam-test'}
            response = self.stream(packets + end, **headers)
            self.assertEqual(response.status_code, 202 if valid else 400, response.text)
        self.assertEqual(self.stream(packets, **headers).status_code, 400)

    def test_async_upload_dispatches_before_storage_finishes_and_links_transcript(self):
        fleet = self.app.state.fleet
        blocked, release, dispatched = threading.Event(), threading.Event(), threading.Event()
        original = fleet.archive.capture

        def capture(raw, entry):
            blocked.set()
            if not release.wait(5):
                raise AssertionError('Test did not release recording storage')
            return original(raw, entry)

        with patch.object(fleet.archive, 'capture', side_effect=capture), patch.object(
                fleet.home_assistant, 'submit', side_effect=lambda *args: dispatched.set() or True):
            try:
                response = self.stream(**{'X-Recording-Async': '1', 'X-Trigger': 'wake', 'X-Wake-Model': 'hey_adam-test'})
                self.assertEqual(response.status_code, 202)
                self.assertTrue(blocked.wait(2))
                self.assertTrue(dispatched.wait(2), 'Wake action waited for recording storage')
                entry = fleet.sessions[response.json()['session_id']]
                self.assertEqual(entry['recording_status'], 'pending')
                self.assertIsNone(entry['archive_id'])
            finally:
                release.set()
            for _ in range(300):
                if entry.get('archive_id') and fleet.archive.get(entry['archive_id'])['transcription_status'] == 'done':
                    break
                time.sleep(.01)
            record = fleet.archive.get(entry['archive_id'])
            self.assertEqual(record['transcript'], 'Fixture transcript')
            self.assertEqual(record['job_id'], response.json()['job_id'])
            self.assertEqual(self.app.state.jobs.get(record['job_id'])['clip_id'], entry['clip_id'])
            self.assertLessEqual(entry['timings_ms']['transcribed'], entry['timings_ms']['recording_saved'])

    def test_full_recording_queue_keeps_recognition_available(self):
        fleet = self.app.state.fleet
        reserved = [fleet.recordings.reserve({}) for _ in range(8)]
        try:
            result = self.stream(**{'X-Recording-Async': '1'}).json()
            self.assertEqual(result['recording_status'], 'skipped')
            self.assertIn('queue is full', result['archive_warning'])
            for _ in range(100):
                job = self.app.state.jobs.get(result['job_id'])
                if job['status'] == 'done':
                    break
                time.sleep(.01)
            self.assertEqual(job['text'], 'Fixture transcript')
            self.assertNotIn('reply', fleet.sessions[result['session_id']])
        finally:
            for work in reserved:
                fleet.recordings.release(work)
