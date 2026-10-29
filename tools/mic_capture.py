"""Download the box's completed stereo clip; optionally start a timed recording.

Saves a stereo WAV, per-channel mono WAVs, and numeric analysis. No network
service or speech model is used. USB download briefly occupies the UI task;
the capture itself runs independently while the eye animates.
"""
import argparse
import json
import math
import re
import time
import wave
from pathlib import Path
import numpy as np
import serial

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
parser.add_argument('--record', type=float, metavar='SECONDS', help='Start a new 0.1..15 second recording first')
parser.add_argument('--port', default='COM6')
args = parser.parse_args()
if args.record is not None and (not 0.1 <= args.record <= 15 or round(args.record*1000) % 10):
    parser.error('--record requires 0.1..15 seconds in 0.01-second increments')
port = serial.Serial(port=None, baudrate=115200, timeout=0.2)
port.dtr = port.rts = False
port.port = args.port
port.open()


def command(text):
    port.write((text+'\n').encode('ascii'))
    end = time.monotonic()+5
    while time.monotonic() < end:
        line = port.readline().decode('utf-8', errors='replace').strip()
        if line.startswith('OK '):
            return line
        if line.startswith('ERR '):
            raise RuntimeError(line)
    raise RuntimeError(f'Timeout: {text}')


def fields(text):
    return dict(re.findall(r'(\w+)=([^ ]+)', text))


try:
    time.sleep(0.2)
    port.reset_input_buffer()
    if args.record is not None:
        command('mic record '+str(round(args.record*1000)))
        print(f'Recording for {args.record:g} seconds now.', flush=True)
        end = time.monotonic()+args.record+3
        while time.monotonic() < end:
            state = fields(command('mic status'))
            if state['recording'] == '0':
                break
            time.sleep(0.2)
        else:
            raise RuntimeError('Recording did not finish')
    status = command('mic status')
    state = fields(status)
    if state.get('recording') != '0' or int(state.get('bytes', '0')) == 0:
        raise RuntimeError('No completed recording available')
    if state['session_errors'] != '0' or state['session_overflows'] != '0':
        raise RuntimeError('Recording had capture errors: '+status)
    before = command('status')
    port.write(b'mic download\n')
    end = time.monotonic()+5
    while time.monotonic() < end:
        header = port.readline().strip()
        if header.startswith((b'AUDIO ', b'ERR ')):
            break
    match = re.fullmatch(rb'AUDIO (\d+) 16000 2 16', header)
    if not match:
        raise RuntimeError(f'Invalid audio header: {header!r}')
    size = int(match[1])
    if size != int(state['bytes']) or not 0 < size <= 960000 or size % 4:
        raise RuntimeError('Invalid audio length')
    raw = bytearray()
    end = time.monotonic()+20
    while len(raw) < size and time.monotonic() < end:
        raw.extend(port.read(min(4096, size-len(raw))))
    if len(raw) != size:
        raise RuntimeError(f'Incomplete recording: {len(raw)}/{size} bytes')
    after = command('status')
finally:
    port.close()

data = np.frombuffer(raw, dtype='<i2').reshape(-1, 2)
report = dict(mic_status=state, before=fields(before), after=fields(after),
              frames=len(data), seconds=len(data)/16000, channels=[])
args.output.parent.mkdir(parents=True, exist_ok=True)


def write_wav(path, pcm, channels):
    with wave.open(str(path), 'wb') as target:
        target.setparams((channels, 2, 16000, 0, 'NONE', 'not compressed'))
        target.writeframes(pcm)


write_wav(args.output, raw, 2)
for ch in range(2):
    values = data[:, ch].astype(np.float64)
    rms = float(np.sqrt(np.mean(values*values)))
    peak = int(np.max(np.abs(values)))
    # Dominant FFT bin is a diagnostic for the generated-tone loopback test.
    centered = values-np.mean(values)
    spectrum = np.abs(np.fft.rfft(centered*np.hanning(len(centered))))
    dominant = float(np.argmax(spectrum[1:])+1)*16000/len(centered) if len(spectrum)>1 else 0
    report['channels'].append(dict(channel=ch+1, rms=rms, peak=peak,
        dbfs=20*math.log10(rms/32768) if rms else None,
        clipped=int(np.count_nonzero(np.abs(values)>=32760)), dominant_hz=dominant))
    path = args.output.with_name(args.output.stem+f'-mic{ch+1}.wav')
    write_wav(path, data[:, ch].tobytes(), 1)
args.output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
print(f'Saved stereo and per-microphone WAVs beside {args.output}')
