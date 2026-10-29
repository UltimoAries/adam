"""Populate the local test UI and verify real model + USB integration."""
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import quote
import requests

base = 'http://127.0.0.1:8765'
checks = []


def request(method, path, **kwargs):
    result = requests.request(method, base + path, timeout=20, **kwargs)
    result.raise_for_status()
    return result


def transcribe(clip, channel=1):
    job = request('POST', f"/api/clips/{clip['id']}/transcriptions", json={'channel':channel,'boost':True}).json()
    end = time.monotonic() + 45
    while time.monotonic() < end:
        job = request('GET', '/api/jobs/'+job['id']).json()
        if job['status'] not in ('queued', 'running'):
            assert job['status'] in ('done', 'no_speech'), job
            return job
        time.sleep(0.2)
    raise RuntimeError('Transcription timeout')


for name, label in [('mic-loopback-v5.wav','440 Hz speaker-to-mic test.wav'),
                    ('mic-speech-v5.wav','First microphone test - 24 dB.wav'),
                    ('speaker-reference.wav','Clean speech reference.wav')]:
    data = (Path('artifacts/recordings') / name).read_bytes()
    clip = request('POST', '/api/clips', data=data,
                   headers={'Content-Type':'audio/wav','X-Filename':quote(label)}).json()
    served = request('GET', f"/api/clips/{clip['id']}/audio").content
    assert hashlib.sha256(served).digest() == hashlib.sha256(data).digest()
    print(json.dumps({'name':clip['name'],'id':clip['id'],'bytes':clip['bytes'],'lossless':True}), flush=True)
    if name == 'speaker-reference.wav':
        result = transcribe(clip)
        assert 'speaker test' in result['text'].lower() and 'living room lights' in result['text'].lower(), result
        checks.append(dict(test='clean_reference_actual_local_stt', result=result))

device = request('GET', '/api/device').json()
assert device['mic']['ready'] == '1'
clip = request('POST', '/api/device/fetch').json()
assert clip['source'] == 'device'
checks.append(dict(test='actual_usb_fetch', clip_id=clip['id'], seconds=clip['seconds'],
                   mic=device['mic'], result=transcribe(clip)))
assert requests.post(base + '/api/clips', data=b'not a wav', timeout=5).status_code == 400
Path('artifacts/headend-live-check.json').write_text(json.dumps(checks, indent=2)+'\n')
print(json.dumps(checks, indent=2), flush=True)
print('PASS: live uploads, byte-exact playback, actual reference transcription, and actual USB capture download.')
