"""Inspect a local voice clip, or transcribe with an explicitly installed model."""
import argparse
import json
from pathlib import Path
import uuid
import wave
from .session import AudioSession, FRAME_BYTES, RATE, SessionError, Trigger
from .stt import FasterWhisperSTT


def read_clip(path: Path):
    session = AudioSession(uuid.uuid4().hex, Trigger.TOUCH)
    try:
        with wave.open(str(path), 'rb') as source:
            if (source.getframerate(), source.getnchannels(), source.getsampwidth(),
                    source.getcomptype()) != (RATE, 1, 2, 'NONE'):
                raise SessionError('WAV must be uncompressed 16 kHz, mono, 16-bit PCM')
            expected_bytes = source.getnframes() * 2
            sequence = 0
            while pcm := source.readframes(FRAME_BYTES // 2):
                session.append(sequence, pcm)
                sequence += 1
            if session.buffered_bytes != expected_bytes:
                raise SessionError('WAV payload is truncated')
        return session.finish()
    finally:
        session.cancel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('wav', type=Path)
    parser.add_argument('--model-dir', type=Path,
                        help='Transcribe using this local faster-whisper model; otherwise inspect only')
    args = parser.parse_args()
    try:
        utterance = read_clip(args.wav)
        result = {'mode': 'inspect', 'seconds': utterance.seconds, 'bytes': len(utterance.pcm),
                  'sample_rate': RATE, 'channels': 1, 'sample_bits': 16}
        if args.model_dir:
            transcript = FasterWhisperSTT(args.model_dir).transcribe(utterance)
            result.update(mode='transcribe', text=transcript.text, language=transcript.language)
        print(json.dumps(result))
    except (OSError, EOFError, wave.Error, ValueError, RuntimeError) as exc:
        parser.exit(2, f'Error: {exc}\n')


if __name__ == '__main__':
    main()
