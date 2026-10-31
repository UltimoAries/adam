"""Check the running Pi's conversation API without audio playback or training uploads.

Run as the runtime owner on the Pi. --enable saves the selected LAN/local routing;
otherwise this only sends a text question and reads status. An owner session is
created for five minutes and deleted on completion. No credentials are printed.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import sqlite3
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(os.environ.get('ADAM_DATA_DIR', ROOT / 'headend/data')))
    parser.add_argument('--enable', action='store_true')
    parser.add_argument('--remote-url', help='Remote Ollama URL (e.g. http://192.168.1.20:11434); required when --enable is passed')
    parser.add_argument('--remote-model', default='adam:latest')
    parser.add_argument('--local-url', default='http://127.0.0.1:11434')
    parser.add_argument('--local-model', default='qwen3.5:0.8b')
    parser.add_argument('--expect-provider', choices=['remote', 'local'])
    parser.add_argument('--question', default='What is a comet?')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.enable and not args.remote_url:
        parser.error('--enable requires an explicit --remote-url (e.g. http://192.168.1.20:11434)')
    directory = args.data_dir
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode()).hexdigest()
    with closing(sqlite3.connect(directory/'fleet.sqlite3')) as db:
        assert db.execute('SELECT 1 FROM account').fetchone(), 'Administrator account missing'
        with db:
            db.execute('INSERT INTO logins VALUES (?,?)', (digest, time.time()+300))
        try:
            def request(path, data=None, method=None):
                req = urllib.request.Request('http://127.0.0.1:8765'+path,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Cookie': 'voice_session='+token, 'Content-Type': 'application/json'}, method=method)
                with urllib.request.urlopen(req, timeout=70) as response:
                    return json.load(response)
            if args.enable:
                request('/api/conversation/settings', dict(enabled=True,
                    remote_url=args.remote_url, remote_model=args.remote_model,
                    local_url=args.local_url, local_model=args.local_model,
                    remote_timeout_seconds=6, local_timeout_seconds=20, cooldown_seconds=30), 'PUT')
            before = request('/api/training/status')['records']
            start = time.monotonic()
            answer = request('/api/conversation/test', {'text': args.question})
            elapsed = time.monotonic()-start
            assert answer['status'] == 'answered', answer
            if args.expect_provider:
                assert answer['provider'] == args.expect_provider, answer
            assert len(answer['text']) <= 200
            fleet = request('/api/fleet')
            archive = directory/'training_archive'
            with closing(sqlite3.connect((archive/'archive.sqlite3').as_uri()+'?mode=ro', uri=True)) as recordings:
                assert recordings.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                rows = recordings.execute("SELECT id,sha256 FROM interactions WHERE audio_status='ready'").fetchall()
                for record, sha in rows:
                    assert hashlib.sha256((archive/'audio'/record[:2]/(record+'.wav')).read_bytes()).hexdigest() == sha
            power_flags = (subprocess.check_output(['vcgencmd', 'get_throttled'], text=True).strip()
                           if shutil.which('vcgencmd') else None)
            result = dict(answer=answer, seconds=round(elapsed, 3), routing=request('/api/conversation/settings'),
                archive_records_before=before, archive_records_after=request('/api/training/status')['records'],
                verified_recordings=len(rows), units=[{k: unit[k] for k in ('id','name','online')} for unit in fleet['units']],
                power_flags=power_flags)
            if args.output:
                args.output.write_text(json.dumps(result, indent=2))
            print(json.dumps(result, indent=2), flush=True)
        finally:
            with db:
                db.execute('DELETE FROM logins WHERE token=?', (digest,))


if __name__ == '__main__':
    main()
