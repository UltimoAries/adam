"""Check a reviewed recording through the live Pi's STT route, without dispatching actions.

Run as the head-end owner. Original WAV, labels and phrase marks are checked for
changes. Retrying updates transcription metadata only, just as the review screen does.
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
import urllib.request

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=Path(os.environ.get('ADAM_DATA_DIR',ROOT/'headend/data')))
    parser.add_argument('--recording',required=True)
    parser.add_argument('--enable',action='store_true',help='Enable prefer_remote STT routing before running the check')
    parser.add_argument('--enable-key-file',type=Path,help='Env file containing API_KEY=... (implies --enable)')
    parser.add_argument('--url',help='Remote OpenAI-compatible STT base URL (e.g. http://192.168.1.20:8000/v1); required with --enable')
    parser.add_argument('--model',default='deepdml/faster-whisper-large-v3-turbo-ct2')
    parser.add_argument('--expect-provider',choices=['remote','local'],required=True)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    enabling=args.enable or args.enable_key_file is not None
    if enabling and not args.url:
        parser.error('--url is required when --enable or --enable-key-file is passed')
    token=secrets.token_urlsafe(32)
    digest=hashlib.sha256(token.encode()).hexdigest()
    with closing(sqlite3.connect(args.data_dir/'fleet.sqlite3')) as db:
        with db: db.execute('INSERT INTO logins VALUES (?,?)',(digest,time.time()+180))
        try:
            def request(path,data=None,method=None,raw=False):
                req=urllib.request.Request('http://127.0.0.1:8765'+path,
                    data=json.dumps(data).encode() if data is not None else None,
                    headers={'Cookie':'voice_session='+token,'Content-Type':'application/json'},method=method)
                with urllib.request.urlopen(req,timeout=60) as response:
                    return response.read() if raw else json.load(response)
            if enabling:
                key=(next(line[8:].strip() for line in args.enable_key_file.read_text().splitlines() if line.startswith('API_KEY='))
                     if args.enable_key_file else os.environ.get('ADAM_STT_API_KEY',''))
                settings=request('/api/models/settings')
                updates=dict(mode='prefer_remote',url=args.url,model=args.model,timeout_seconds=6,cooldown_seconds=30)
                if key:
                    updates['api_key']=key
                settings['stt'].update(updates)
                request('/api/models/settings',settings,'PUT')
            base='/api/training/recordings/'+args.recording
            before=request(base)
            audio_hash=hashlib.sha256(request(base+'/audio',raw=True)).hexdigest()
            start=time.monotonic()
            job_id=request(base+'/transcribe',{},'POST')['job_id']
            while True:
                job=request('/api/jobs/'+job_id)
                if job['status'] not in ('queued','running','cancelling'):
                    break
                if time.monotonic()-start>60:
                    raise RuntimeError('Recognition check timed out')
                time.sleep(.2)
            assert job['status']=='done', job['status']
            route=request('/api/models/settings')['stt']
            assert route['last_provider']==args.expect_provider, route
            after=request(base)
            assert hashlib.sha256(request(base+'/audio',raw=True)).hexdigest()==audio_hash
            for field in ('sha256','label','phrase_start_ms','phrase_end_ms'):
                assert before.get(field)==after.get(field), field
            result=dict(provider=route['last_provider'],seconds=round(time.monotonic()-start,3),
                text=job['text'],original_hash=audio_hash,original_and_review_preserved=True)
            if args.output:
                args.output.write_text(json.dumps(result,indent=2))
            print(json.dumps(result,indent=2),flush=True)
        finally:
            with db: db.execute('DELETE FROM logins WHERE token=?',(digest,))


if __name__=='__main__':
    main()
