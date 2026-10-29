"""Wait for one physical recording, replay it on the box, and save it locally."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path
import serial

port = serial.Serial(port=None, baudrate=115200, timeout=0.1)
port.dtr = port.rts = False
port.port = 'COM6'
port.open()


def command(text, lines=1):
    time.sleep(0.13)
    port.write((text+'\n').encode('ascii'))
    reply = []
    end = time.monotonic()+4
    while time.monotonic() < end:
        line = port.readline().decode('utf-8', errors='replace').strip()
        if line.startswith('ERR '):
            raise RuntimeError(line)
        if line.startswith('OK '):
            reply.append(line)
            if len(reply) == lines:
                return reply[0] if lines == 1 else reply
    raise RuntimeError(f'Timeout: {text}')


def fields(text):
    return dict(re.findall(r'(\w+)=([^ ]+)', text))


original = fields(command('ui status'))['volume']
changed = False
measurements = []
try:
    started = False
    end = time.monotonic()+180
    print('Waiting for the physical RECORD 8S tap.', flush=True)
    while time.monotonic() < end:
        state = fields(command('mic status'))
        if state['recording'] == '1':
            if not started:
                print('Recording started.', flush=True)
            started = True
        if started:
            measurements.append(dict(mic=state, display=fields(command('status')),
                resources=[fields(x) for x in command('resources', lines=3)]))
            if state['recording'] == '0':
                break
        time.sleep(0.4)
    else:
        raise RuntimeError('No completed physical recording within three minutes')
    assert int(state['frames']) > 0 and state['session_errors'] == '0' and state['session_overflows'] == '0', state
    command('volume 50'); changed = True
    command('mic play 1')
    print('Replaying microphone 1 on the box at volume 50.', flush=True)
    end = time.monotonic()+18
    while time.monotonic() < end:
        active = fields(command('audio status'))
        if active['playing'] == '0':
            break
        time.sleep(0.3)
    else:
        raise RuntimeError('Replay did not finish')
    assert active['write_errors'] == '0' and active['queue_overflows'] == '0', active
    print('Replay finished.', flush=True)
finally:
    try:
        if changed:
            command('volume '+original)
    finally:
        port.close()
        Path('artifacts/microphone-human-test-v5.json').write_text(json.dumps(measurements, indent=2)+'\n')
subprocess.run([sys.executable, 'tools/mic_capture.py', 'artifacts/recordings/mic-speech-36db-v5.wav'], check=True)
