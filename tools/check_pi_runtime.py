"""Run inside the ADAM container or local environment: read-only migration checks and isolated speech timing.

Uses a known synthetic phrase, never uploads it to the training archive, and
does not send Home Assistant control commands. No credentials are printed.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import urllib.request

from headend.clips import mono_16k, parse_wav
from headend.home_assistant import HomeAssistantClient, HomeAssistantSettings
from headend.session import Trigger, Utterance
from headend.stt import FasterWhisperSTT
from headend.voices import VoiceService

ROOT = Path(__file__).resolve().parents[1]
CONTAINER_HEADEND = Path('/app/headend')
DEFAULT_HEADEND = Path(os.environ.get('ADAM_HEADEND_DIR', CONTAINER_HEADEND if CONTAINER_HEADEND.exists() else ROOT / 'headend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headend-dir', type=Path, default=DEFAULT_HEADEND)
    parser.add_argument('--data-dir', type=Path, help='Override data directory (defaults to ADAM_DATA_DIR or HEADEND_DIR/data)')
    args = parser.parse_args()
    root = args.headend_dir
    data_dir = args.data_dir or Path(os.environ.get('ADAM_DATA_DIR', root / 'data'))
    result = {}
    with urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=5) as response:
        result['health'] = json.load(response)
    with closing(sqlite3.connect((data_dir/'fleet.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        result['units'] = [dict(r) for r in db.execute('SELECT id,name,firmware,wake_model FROM units')]
        row = db.execute('SELECT * FROM home_assistant_settings WHERE id=1').fetchone()
        options = HomeAssistantSettings(enabled=bool(row['enabled']), url=row['url'], token=row['token'],
            entity_id=row['entity_id'], name=row['name'], auto_import=bool(row['auto_import']))
    client = HomeAssistantClient()
    result['home_assistant'] = client.check(options)
    result['home_assistant']['entities'] = len(client.entities(options, all_domains=True))
    directory = data_dir/'training_archive'
    with closing(sqlite3.connect((directory/'archive.sqlite3').as_uri()+'?mode=ro', uri=True)) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        rows = db.execute("SELECT id,sha256 FROM interactions WHERE audio_status='ready'").fetchall()
        for record_id, digest in rows:
            raw = (directory/'audio'/record_id[:2]/(record_id+'.wav')).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == digest
        result['archive'] = dict(verified_audio=len(rows), settings=json.loads(
            db.execute('SELECT data FROM settings WHERE id=1').fetchone()[0]))
    print(json.dumps(result, indent=2), flush=True)
    phrase = 'Hey Adam, turn off the kitchen lights.'
    with tempfile.TemporaryDirectory(prefix='adam-pi-speech-') as temporary:
        voices = VoiceService(Path(temporary)/'voices', builtin=root/'models/voices/basic-english/1.0.0')
        measurements = []
        try:
            started = time.monotonic()
            model = FasterWhisperSTT(root/'models/stt/base.en')
            print(json.dumps(dict(stt_model_load_seconds=round(time.monotonic()-started, 3))), flush=True)
            for attempt in range(3):
                started = time.monotonic()
                raw, details = voices.generate(phrase)
                generation = time.monotonic()-started
                rate, samples = parse_wav(raw)
                pcm = mono_16k(rate, samples, 1)[0]
                started = time.monotonic()
                transcript = model.transcribe(Utterance('pi-smoke', Trigger.TOUCH, None, pcm))
                elapsed = time.monotonic()-started
                assert 'kitchen lights' in transcript.text.lower(), transcript.text
                measurements.append(dict(attempt=attempt+1, audio_seconds=round(len(pcm)/32000, 3),
                    tts_seconds=round(generation, 3), stt_seconds=round(elapsed, 3),
                    transcript=transcript.text, model_cached=details.get('model_cached')))
            print(json.dumps(dict(synthetic_speech_measurements=measurements), indent=2), flush=True)
        finally:
            voices.close()


if __name__ == '__main__':
    main()
