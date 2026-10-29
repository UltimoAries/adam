"""Profile the real board with Wi-Fi connected, eye animation, and muted I2S output.

No credentials are read or changed. Restores volume and the eye page on exit.
The muted test still synthesizes samples and runs the I2S/DMA playback task.
"""
import json
import re
import time
from pathlib import Path
import serial

port = serial.Serial(port=None, baudrate=115200, timeout=0.1)
port.dtr = port.rts = False
port.port = 'COM6'
port.open()
log = []
samples = []


def command(text, lines=1, error=False):
    time.sleep(0.15)
    port.write((text + '\n').encode('ascii'))
    reply = []
    end = time.monotonic() + 5
    while time.monotonic() < end:
        line = port.readline().decode('utf-8', errors='replace').strip()
        if not line:
            continue
        log.append(line)
        assert not any(marker in line for marker in
                       ('Guru Meditation', 'stack overflow', 'Brownout', 'ESP-ROM:', 'Rebooting')), line
        if line.startswith('ERR '):
            if error:
                return line
            raise RuntimeError(line)
        if line.startswith('OK '):
            assert not error, f'Invalid command accepted: {text}'
            reply.append(line)
            if len(reply) == lines:
                return reply if lines > 1 else line
    raise RuntimeError(f'Timeout: {text}')


def fields(text):
    return dict(re.findall(r'(\w+)=([^ ]+)', text))


def measure(scenario, count=3):
    # Let one full two-second CPU sampling window settle after each change.
    time.sleep(4.2)
    for _ in range(count):
        memory, cpu, storage = map(fields, command('resources', lines=3))
        display = fields(command('status'))
        audio = fields(command('audio status'))
        ui = fields(command('ui status'))
        assert cpu['valid'] == '1'
        assert 'wifi=CONNECTED' in command('ui status')
        assert int(audio['write_errors']) == 0 and int(audio['queue_overflows']) == 0, audio
        assert audio['last_error'] == 'ESP_OK'
        samples.append(dict(scenario=scenario, memory=memory, cpu=cpu, storage=storage,
                            display=display, audio=audio, ui=ui))
        time.sleep(2.1)
    print(f'{scenario}: {count} samples recorded', flush=True)


original = fields(command('ui status'))
try:
    command('volume 0')
    command('menu eye')
    measure('eye_wifi_idle')
    command('audio tone 440 30000')
    measure('eye_wifi_audio_muted')
    assert fields(command('audio status'))['playing'] == '1'
    command('audio stop')
    time.sleep(0.3)
    assert fields(command('audio status'))['playing'] == '0'

    command('menu settings')
    measure('settings_wifi_idle', count=2)
    command('ui tap 180 255')
    assert fields(command('ui status'))['page'] == 'audio'
    before = int(fields(command('audio status'))['completed'])
    command('ui tap 125 145')  # CHIME
    time.sleep(1.6)
    assert int(fields(command('audio status'))['completed']) == before + 1
    command('ui tap 33 130')
    assert fields(command('audio status'))['volume'] == '10'
    command('ui tap 33 230')
    assert fields(command('audio status'))['volume'] == '0'
    command('ui tap 235 145')  # TONE 10S
    assert fields(command('audio status'))['playing'] == '1'
    command('ui tap 180 255')  # STOP
    time.sleep(0.3)
    assert fields(command('audio status'))['playing'] == '0'
    command('ui tap 180 35')
    assert fields(command('ui status'))['page'] == 'settings'

    # Exercise shared I2C, Wi-Fi scan events, and I2S output concurrently.
    command('audio tone 440 30000')
    command('ui tap 180 140')  # Wi-Fi starts an asynchronous scan.
    end = time.monotonic() + 16
    while time.monotonic() < end:
        state = fields(command('ui status'))
        assert state['scan'] != 'failed'
        if state['scan'] == 'ready':
            break
        time.sleep(0.2)
    else:
        raise RuntimeError('Concurrent Wi-Fi scan timed out')
    assert fields(command('audio status'))['playing'] == '1'
    measure('wifi_scan_finished_audio_muted', count=1)
    command('audio stop')
    for invalid in ('audio tone 99 1000', 'audio tone 4001 1000',
                    'audio tone 440 99', 'audio tone 440 30001', 'volume -1', 'volume 101'):
        command(invalid, error=True)
finally:
    try:
        command('audio stop')
        command('volume ' + original['volume'])
        command('menu eye')
    finally:
        port.close()
        Path('artifacts/audio-resource-profile.json').write_text(json.dumps(samples, indent=2) + '\n')
        Path('artifacts/audio-resource-profile.log').write_text('\n'.join(log) + '\n')
print('PASS: audio start/stop, UI controls, bounds, concurrent scan, and resource samples.')
