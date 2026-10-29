"""Exercise real capture/playback concurrency, clip ownership, and UI paths.

Run while nobody is operating the display. Restores output levels, leaves
the microphone test page open, and clears its diagnostic recording at the end.
Does not save raw audio or change network credentials.
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
measurements = []


def command(text, lines=1, error=False):
    time.sleep(0.13)
    port.write((text+'\n').encode('ascii'))
    reply = []
    end = time.monotonic()+5
    while time.monotonic() < end:
        line = port.readline().decode('utf-8', errors='replace').strip()
        if not line:
            continue
        log.append(line)
        assert not any(s in line for s in ('Guru Meditation', 'stack overflow', 'ESP-ROM:', 'Rebooting', 'Brownout')), line
        if line.startswith('ERR '):
            if error:
                return line
            raise RuntimeError(line)
        if line.startswith('OK '):
            assert not error, f'Unexpected success: {text}'
            reply.append(line)
            if len(reply) == lines:
                return reply[0] if lines == 1 else reply
    raise RuntimeError(f'Timeout: {text}')


def fields(text):
    return dict(re.findall(r'(\w+)=([^ ]+)', text))


def state():
    value = fields(command('mic status'))
    assert value['ready'] == '1' and value['read_errors'] == '0' and value['queue_overflows'] == '0', value
    assert value['session_errors'] == '0' and value['session_overflows'] == '0', value
    return value


original = fields(command('ui status'))
gain = state()['gain_db']
try:
    command('audio stop')
    command('volume 0')  # Longer tests run I2S/sample generation without speaker sound.
    command('menu eye')
    for invalid in ('mic record 99', 'mic record 15001', 'mic record 101',
                    'mic gain 37', 'mic gain 35', 'mic play 0', 'mic play 3'):
        command(invalid, error=True)
    command('mic clear')
    command('mic download', error=True)
    command('mic play 1', error=True)
    command('audio tone 440 30000')
    before = fields(command('status'))
    started = time.monotonic()
    command('mic record 15000')
    command('mic record 1000', error=True)
    command('mic gain 30', error=True)
    command('mic clear', error=True)
    command('mic download', error=True)
    command('mic play 1', error=True)
    for _ in range(3):
        time.sleep(2.2)
        measurements.append(dict(mic=state(), display=fields(command('status')),
                                 resources=[fields(x) for x in command('resources', lines=3)]))
    while state()['recording'] == '1':
        assert time.monotonic()-started < 19
        time.sleep(0.2)
    final = state()
    assert final['frames'] == '240000' and final['bytes'] == '960000'
    after = fields(command('status'))
    assert int(after['frames'])-int(before['frames']) > 200, (before, after)
    command('audio stop')

    # Both microphone channels can be replayed. Clip storage is reserved while
    # queued/playing, so a new recording or Clear cannot overwrite its samples.
    for channel in (1, 2):
        command(f'mic play {channel}')
        command('mic record 1000', error=True)
        command('mic clear', error=True)
        assert fields(command('audio status'))['playing'] == '1'
        command('audio stop')
        time.sleep(0.3)
    command('mic gain 30')
    assert state()['gain_db'] == '30'
    command('mic gain '+gain)

    # Start via the UI, then stop by tapping the eye, and replay via the UI.
    command('menu settings')
    command('ui tap 180 255')
    command('ui tap 180 195')
    assert fields(command('ui status'))['page'] == 'microphone'
    command('ui tap 180 205')
    assert fields(command('ui status'))['page'] == 'eye'
    assert state()['recording'] == '1'
    command('ui tap 180 180')
    partial = state()
    assert partial['recording'] == '0' and 0 < int(partial['frames']) < 128000
    command('menu settings')
    command('ui tap 180 255')
    command('ui tap 180 195')
    command('ui tap 125 255')
    assert fields(command('ui status'))['page'] == 'eye'
    command('audio stop')
    time.sleep(0.3)
    # Stop/restart never changes the I2S timebase or loses microphone blocks.
    assert int(state()['blocks']) > int(final['blocks'])
finally:
    try:
        command('audio stop')
        time.sleep(0.3)
        command('mic stop')
        command('mic clear')
        command('volume '+original['volume'])
        command('menu settings')
        command('ui tap 180 255')
        command('ui tap 180 195')
        final = dict(mic=state(), audio=fields(command('audio status')),
                     ui=fields(command('ui status')),
                     resources=[fields(x) for x in command('resources', lines=3)])
        assert final['audio']['write_errors'] == '0' and final['audio']['queue_overflows'] == '0', final
    finally:
        port.close()
        Path('artifacts/microphone-check-v5.json').write_text(json.dumps(dict(samples=measurements, final=final), indent=2)+'\n')
        Path('artifacts/microphone-check-v5.log').write_text('\n'.join(log)+'\n')
print('PASS: 15-second concurrent capture, PCM replay, bounds, clip ownership, UI recording/stop, and resource checks.')
