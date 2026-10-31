"""Measure acoustic tone onset, including amplifier/codec startup; restores controls."""
import argparse
import json
import os
import re
import time
import wave
from pathlib import Path
import numpy as np
import serial

DEFAULT_PORT = os.environ.get('VOICE_USB_PORT', 'COM6' if os.name == 'nt' else '/dev/ttyACM0')

p = argparse.ArgumentParser()
p.add_argument('--label', default='baseline')
p.add_argument('--port', default=DEFAULT_PORT)
args = p.parse_args()
port = serial.Serial(port=None, baudrate=115200, timeout=.2, write_timeout=2)
port.dtr = port.rts = False
port.port = args.port
port.open()

def command(text):
    port.write((text+'\n').encode())
    end = time.monotonic()+5
    while time.monotonic() < end:
        line = port.readline().decode(errors='replace').strip()
        if line.startswith('ERR '):
            raise RuntimeError(line)
        if line.startswith('OK '):
            return dict(re.findall(r'(\w+)=([^ ]+)', line))
    raise RuntimeError('No response: '+text)

original = gain = None
reports = []
folder = Path('artifacts/audio-start')
folder.mkdir(parents=True, exist_ok=True)
try:
    port.reset_input_buffer()
    original = command('ui status')['volume']
    gain = command('mic status')['gain_db']
    command('volume 30')
    command('mic gain 18')
    for attempt in range(3):
        command('mic record 2500')
        time.sleep(.15)
        command('audio tone 700 1600')
        time.sleep(2.6)
        port.write(b'mic download\n')
        end = time.monotonic()+5
        match = None
        while time.monotonic() < end:
            header = port.readline().strip()
            match = re.fullmatch(rb'AUDIO (\d+) 16000 2 16', header)
            if match: break
        assert match, header
        size = int(match[1]); raw = bytearray()
        end = time.monotonic()+12
        while len(raw) < size and time.monotonic() < end:
            raw.extend(port.read(min(4096, size-len(raw))))
        assert len(raw) == size
        data = np.frombuffer(raw, '<i2').reshape(-1, 2)[:, 0].astype(float)
        frames = data[:len(data)//320*320].reshape(-1, 320)
        # Coherent energy at the commanded frequency rejects ordinary room noise.
        tone = np.abs(frames @ np.exp(-2j*np.pi*700*np.arange(320)/16000))*2/320
        steady = float(np.median(np.sort(tone)[-30:]))
        active = np.flatnonzero(tone > max(steady*.5, 10))
        report = dict(attempt=attempt, steady_amplitude=steady,
                      first_half_volume_ms=int(active[0]*20) if len(active) else None,
                      audible_ms=int(len(active)*20), amplitudes=tone.round(1).tolist())
        reports.append(report)
        with wave.open(str(folder/f'{args.label}-{attempt}.wav'), 'wb') as f:
            f.setparams((2, 2, 16000, 0, 'NONE', 'not compressed')); f.writeframes(raw)
        command('mic clear')
        time.sleep(.5)
finally:
    try:
        # Drain an interrupted binary download before looking for command ACKs.
        port.timeout=.1
        while port.read(4096): pass
        for text in ['mic stop','audio stop','mic clear']+(['volume '+original] if original else [])+(['mic gain '+gain] if gain else []):
            try: command(text)
            except RuntimeError as error: print('Cleanup:', error)
    finally: port.close()
    (folder/f'{args.label}.json').write_text(json.dumps(reports, indent=2)+'\n')
print(json.dumps([{k:v for k,v in r.items() if k!='amplitudes'} for r in reports], indent=2))
