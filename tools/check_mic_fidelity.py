"""Test real I2S reception and acoustic capture for periodic missing samples.

Run with nobody using the box. Plays two brief 700 Hz tones at volume 30,
saves local diagnostic WAVs, restores volume/input routing and clears the
device's test clip. Requires numpy and pyserial (py -3.12 works here).
"""
import json
import re
import time
import wave
from pathlib import Path

import numpy as np
import serial


def fields(line):
    return dict(re.findall(r'(\w+)=([^ ]+)', line))


port = serial.Serial(port=None, baudrate=115200, timeout=.2, write_timeout=2)
port.dtr = port.rts = False
port.port = 'COM6'
for attempt in range(20):
    try:
        port.open()
        break
    except serial.SerialException:
        if attempt == 19:
            raise
        time.sleep(.25)

log, results = [], []
original = None
original_gain = None


def command(text):
    port.write((text+'\n').encode('ascii'))
    end = time.monotonic()+5
    while time.monotonic() < end:
        line = port.readline().decode(errors='replace').strip()
        if line:
            log.append(line)
        if line.startswith('ERR '):
            raise RuntimeError(line)
        if line.startswith('OK '):
            return line
    raise RuntimeError('No response: '+text)


def download():
    port.write(b'mic download\n')
    header = port.readline().strip()
    match = re.fullmatch(rb'AUDIO (\d+) 16000 2 16', header)
    assert match, header
    size = int(match[1])
    assert size == 128000, size  # 2 seconds, stereo, signed 16-bit.
    data = bytearray()
    end = time.monotonic()+10
    while len(data) < size and time.monotonic() < end:
        data.extend(port.read(min(4096, size-len(data))))
    assert len(data) == size
    return bytes(data)


try:
    time.sleep(.2)
    port.reset_input_buffer()
    original = fields(command('ui status'))
    state = fields(command('mic status'))
    assert state['recording'] == '0'
    original_gain = state['gain_db']
    command('audio stop')
    command('volume 30')
    # The enclosure's own speaker is much closer than an arm's-length voice.
    command('mic gain 18')
    command('menu eye')
    clock = fields(command('audio clocks'))
    for key, expected in [('mclk_hz',4096000),('bclk_hz',1024000),('ws_hz',16000)]:
        assert abs(float(clock[key])/expected-1) < .01, clock
    assert clock['rx_channels'] == clock['tx_channels'] == '2', clock

    for mode in ('digital', 'acoustic'):
        command('audio loopback '+str(int(mode == 'digital')))
        command('audio tone 700 4000')
        time.sleep(.4)
        command('mic record 2000')
        end = time.monotonic()+4
        while fields(command('mic status'))['recording'] == '1':
            assert time.monotonic() < end
            time.sleep(.2)
        diagnostic = fields(command('mic diagnose'))
        state = fields(command('mic status'))
        assert state['session_errors'] == state['session_overflows'] == '0', state
        assert state['clipped'] == '0,0', state
        raw = download()
        data = np.frombuffer(raw, dtype='<i2').reshape(-1,2)
        report = {'mode':mode,'diagnostic':diagnostic,'mic':state,'channels':[]}
        for ch in range(2):
            values = data[:,ch].astype(np.float64)
            spectrum = abs(np.fft.rfft((values-values.mean())*np.hanning(len(values))))**2
            hz = np.fft.rfftfreq(len(values),1/16000)
            dominant = float(hz[np.argmax(spectrum[1:])+1])
            zeros = float(np.mean(values == 0))
            tone_fraction = float(spectrum[(hz>695)&(hz<705)].sum()/max(spectrum.sum(),1e-9))
            report['channels'].append(dict(zero_fraction=zeros,dominant_hz=dominant,
                tone_power_fraction=tone_fraction,rms=float(np.sqrt(np.mean(values*values)))))
        results.append(report)
        output = Path(f'artifacts/recordings/mic-v8-{mode}-700hz.wav')
        output.parent.mkdir(parents=True,exist_ok=True)
        with wave.open(str(output),'wb') as target:
            target.setparams((2,2,16000,0,'NONE','not compressed'))
            target.writeframes(raw)
        for channel in report['channels']:
            assert channel['zero_fraction'] < .05, report
            assert abs(channel['dominant_hz']-700) <= 2, report
            assert channel['tone_power_fraction'] > (.95 if mode == 'digital' else .5), report
        if mode == 'digital':
            assert np.array_equal(data[:,0],data[:,1]), 'Digital stereo data differs'
        command('audio stop')
        time.sleep(.25)
finally:
    try:
        command('mic stop')
        command('audio stop')
        time.sleep(.3)
        command('audio loopback 0')
        command('mic clear')
        if original_gain is not None:
            command('mic gain '+original_gain)
        if original:
            command('volume '+original['volume'])
        final = fields(command('mic diagnose'))
    finally:
        port.close()
        Path('artifacts/mic-fidelity-v8.json').write_text(json.dumps(
            dict(results=results,log=log),indent=2)+'\n')
print(json.dumps(results,indent=2))
print('PASS: digital and acoustic 700 Hz capture without periodic sample gaps.')
