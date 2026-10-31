"""Check speech through the running local head-end using a short-lived admin session.

Run as the runtime owner on the head-end. Does not control household devices or
add synthetic speech to the training archive. If diagnostics are full, reuses
an existing clip without changing it. Removes only its own diagnostic clip and
temporary session on completion; the session also expires after two minutes.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONTAINER_HEADEND = Path('/app/headend')
DEFAULT_HEADEND = Path(os.environ.get('ADAM_HEADEND_DIR', CONTAINER_HEADEND if CONTAINER_HEADEND.exists() else ROOT / 'headend'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--headend-dir', type=Path, default=DEFAULT_HEADEND)
    parser.add_argument('--data-dir', type=Path, help='Override data directory (defaults to ADAM_DATA_DIR or HEADEND_DIR/data)')
    args = parser.parse_args()
    directory = args.data_dir or Path(os.environ.get('ADAM_DATA_DIR', args.headend_dir / 'data'))
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    clip_id = None
    with closing(sqlite3.connect(directory/'fleet.sqlite3')) as db:
        if not db.execute('SELECT 1 FROM account').fetchone():
            raise RuntimeError('The head-end must already have an administrator account')
        with db:
            db.execute('INSERT INTO logins VALUES (?,?)', (digest, time.time()+120))
        try:
            with httpx.Client(base_url='http://127.0.0.1:8765', trust_env=False,
                              cookies={'voice_session': token}, timeout=45) as client:
                def request(method, path, **options):
                    response = client.request(method, path, **options)
                    response.raise_for_status()
                    return response
                before = request('GET', '/api/training/status').json()
                voice = request('GET', '/api/voices').json()['default_voice']
                request('GET', '/api/voices/guide')
                start = time.monotonic()
                response = request('POST', '/api/voices/preview', json={
                    'voice_id': voice, 'text': 'Hey Adam, turn off the kitchen lights.'})
                speech_seconds = time.monotonic()-start
                print(json.dumps({'stage': 'speech_generated', 'seconds': round(speech_seconds, 3)}), flush=True)
                existing = request('GET', '/api/clips').json()
                existing_ids = {item['id'] for item in existing}
                reused = len(existing)>=48 or sum(item['bytes'] for item in existing)+len(response.content)>64*1024**2
                if reused:
                    candidates = [item for item in existing if 2<=item['seconds']<=8
                                  and item['levels'][0]['rms']>100]
                    if not candidates:
                        raise RuntimeError('Diagnostic library is full and has no suitable speech check clip')
                    clip = candidates[0]
                else:
                    clip = request('POST', '/api/clips', content=response.content,
                                   headers={'X-Filename': 'pi-deployment-check.wav',
                                            'Content-Type': 'audio/wav'}).json()
                    if clip['id'] not in existing_ids:
                        clip_id = clip['id']
                start = time.monotonic()
                job = request('POST', '/api/clips/'+clip['id']+'/transcriptions',
                              json={'channel': 1, 'boost': reused}).json()
                deadline = time.monotonic()+45
                while job['status'] in ('queued', 'running') and time.monotonic()<deadline:
                    time.sleep(.25)
                    job = request('GET', '/api/jobs/'+job['id']).json()
                if job['status'] != 'done':
                    raise RuntimeError('Speech check did not complete: '+job['status'])
                if not reused:
                    assert 'kitchen lights' in job['text'].lower(), 'Expected command was not recognized'
                after = request('GET', '/api/training/status').json()
                print(json.dumps({'stage': 'complete', 'tts_seconds': round(speech_seconds, 3),
                    'stt_seconds': round(time.monotonic()-start, 3), 'reused_existing_clip': reused,
                    'transcript': '(existing recording transcribed)' if reused else job['text'],
                    'archive_records_before': before['records'], 'archive_records_after': after['records']}, indent=2), flush=True)
                if clip_id:
                    request('DELETE', '/api/clips/'+clip_id)
                clip_id = None
        finally:
            if clip_id:
                # Only this check's new clip may be removed, never an existing recording.
                try:
                    with httpx.Client(trust_env=False, timeout=5) as client:
                        client.delete('http://127.0.0.1:8765/api/clips/'+clip_id,
                                      cookies={'voice_session': token})
                except httpx.HTTPError:
                    pass
            with db:
                db.execute('DELETE FROM logins WHERE token=?', (digest,))


if __name__ == '__main__':
    main()
