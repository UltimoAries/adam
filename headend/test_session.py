import tempfile
import unittest
import wave
from pathlib import Path
from .__main__ import read_clip
from .session import AudioSession, FRAME_BYTES, MAX_BYTES, SessionError, Trigger
from .stt import FasterWhisperSTT


class AudioContractTests(unittest.TestCase):
    def test_wake_phrase_does_not_change_audio_contract(self):
        for trigger, model in [(Trigger.TOUCH, None), (Trigger.WAKE, 'hey_adam_v1'),
                               (Trigger.WAKE, 'stock_bringup_v1')]:
            with self.subTest(trigger=trigger, model=model):
                session = AudioSession('test-session', trigger, model)
                frame = b'\x01\x00' * 320
                session.append(0, frame)
                utterance = session.finish()
                self.assertEqual(utterance.pcm, frame)
                self.assertEqual(utterance.seconds, 0.02)
                self.assertTrue(session.closed)
                self.assertEqual(session.buffered_bytes, 0)

    def test_gap_or_duplicate_invalidates_session(self):
        for wrong_sequence in (0, 2, True):
            session = AudioSession('sequence-test', Trigger.TOUCH)
            session.append(0, bytes(FRAME_BYTES))
            with self.assertRaises(SessionError):
                session.append(wrong_sequence, bytes(FRAME_BYTES))
            self.assertTrue(session.closed)
            self.assertEqual(session.buffered_bytes, 0)
            with self.assertRaises(SessionError):
                session.finish()

    def test_limit_enforced_and_cancel_releases_buffer(self):
        session = AudioSession('limit-test', Trigger.TOUCH)
        for sequence in range(MAX_BYTES // FRAME_BYTES):
            session.append(sequence, bytes(FRAME_BYTES))
        self.assertEqual(session.buffered_bytes, MAX_BYTES)
        with self.assertRaises(SessionError):
            session.append(MAX_BYTES // FRAME_BYTES, b'\x00\x00')
        self.assertEqual(session.buffered_bytes, 0)

    def test_invalid_frames_cannot_be_transcribed(self):
        for frame in (b'', b'\x00', bytes(FRAME_BYTES + 2)):
            session = AudioSession('frame-test', Trigger.TOUCH)
            with self.assertRaises(SessionError):
                session.append(0, frame)
            self.assertTrue(session.closed)
        with self.assertRaises(SessionError):
            AudioSession('empty-test', Trigger.TOUCH).finish()

    def test_cancel_and_wake_metadata(self):
        with self.assertRaises(SessionError):
            AudioSession('wake-test', Trigger.WAKE)
        session = AudioSession('cancel-test', Trigger.TOUCH)
        session.append(0, bytes(FRAME_BYTES))
        session.cancel()
        with self.assertRaises(SessionError):
            session.finish()

    def test_wav_format_validation_and_missing_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for rate in (16000, 24000):
                path = root / f'{rate}.wav'
                with wave.open(str(path), 'wb') as target:
                    target.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
                    target.writeframes(bytes(FRAME_BYTES))
                if rate == 16000:
                    self.assertEqual(read_clip(path).seconds, 0.02)
                else:
                    with self.assertRaises(SessionError):
                        read_clip(path)
            # A missing model fails before any optional backend import or download.
            with self.assertRaises(ValueError):
                FasterWhisperSTT(root / 'missing-model')
            (root / 'model.bin').write_bytes(b'placeholder')
            (root / 'config.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'tokenizer.json'):
                FasterWhisperSTT(root)


if __name__ == '__main__':
    unittest.main()
