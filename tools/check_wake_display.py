"""Verify the real listening cue never interrupts green with blue. Uses a
cancelled remote/touch capture, muted output, and preserves the original volume.
"""
import argparse
import json
import os
import re
import time
from pathlib import Path
import serial

DEFAULT_PORT=os.environ.get('VOICE_USB_PORT','COM6' if os.name=='nt' else '/dev/ttyACM0')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port',default=DEFAULT_PORT)
args=parser.parse_args()

port=serial.Serial(port=None,baudrate=115200,timeout=.1)
port.dtr=port.rts=False;port.port=args.port;port.open()

def command(text):
    expected='OK '+text.split()[0]+' '
    port.write((text+'\n').encode('ascii'))
    until=time.monotonic()+4
    while time.monotonic()<until:
        line=port.readline().decode('utf-8',errors='replace').strip()
        if line.startswith(expected):return line
        if line.startswith('ERR '):raise RuntimeError(line)
    raise RuntimeError('No response: '+text)

samples=[]
original=command('ui status')
volume=re.search(r'volume=(\d+)',original)[1]
try:
    for _ in range(30):
        if 'state=READY' in command('headend status'):break
        time.sleep(.5)
    else:raise RuntimeError('Device did not become ready')
    command('volume 0');command('menu home');command('headend listen')
    started=time.monotonic()
    while time.monotonic()-started<2.5:
        ui=command('ui status');audio=command('audio status')
        # Multi-line headend diagnostics can still be in the serial input.
        if not ui.startswith('OK ui ') or not audio.startswith('OK audio '):continue
        samples.append(dict(ms=round((time.monotonic()-started)*1000),
                            scene=re.search(r'scene=(\w+)',ui)[1],playing='playing=1' in audio))
        time.sleep(.02)
    first=next(i for i,s in enumerate(samples) if s['scene']=='listening')
    assert all(s['scene']=='listening' for s in samples[first:]),samples
    assert any(s['playing'] and s['scene']=='listening' for s in samples), 'Cue was not sampled'
finally:
    command('headend stop');command('audio stop');command('volume '+volume);command('menu home');port.close()
Path('artifacts/wake-display-v15.json').write_text(json.dumps(samples,indent=2)+'\n')
print('PASS: listening remains green through the real cue; output volume restored.')
