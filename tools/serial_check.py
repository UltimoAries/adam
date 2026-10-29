"""Exercise display command bounds and recovery, then leave the eye running."""
import json
from pathlib import Path
import time
import serial

port = serial.Serial(port=None, baudrate=115200, timeout=0.1)
port.dtr = False
port.rts = False
port.port = 'COM6'
report = []

def send(command, expected='OK '):
    port.reset_input_buffer()
    port.write((command + '\n').encode())
    response = bytearray()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        response.extend(port.read(port.in_waiting or 1))
        lines = response.decode(errors='replace').replace('\r', '').split('\n')[:-1]
        reply = next((line for line in lines if line.startswith(('OK ', 'ERR '))), None)
        if reply is not None:
            break
    else:
        raise RuntimeError('No complete response to ' + command)
    print(command[:80], '=>', reply, flush=True)
    report.append({'command': command, 'response': reply})
    if not reply.startswith(expected):
        raise RuntimeError('Unexpected response: ' + reply)
    return reply

port.open()
try:
    send('status')
    for command in ['brightness 101', 'brightness -1', 'rotation 45', 'mirror 2 0',
                    'look 101 0', 'pupil 0', 'fps 0', 'fill NOTHEX',
                    'rect 0 0 0 10 FFFFFF', 'circle 0 0 999 FFFFFF 1', 'x'*220]:
        send(command, 'ERR ')
    send('fill 080C14')
    for command in ['rect 330 330 360 360 FF00FF',
                    'circle 0 0 360 152940 0', 'circle 180 180 65 00BBDC 1',
                    'line 0 0 359 359 FFFFFF', 'text 102 65 3 FFFFFF DRAW OK']:
        send(command)
    for command in ['display off', 'display on', 'rotation 90', 'rotation 180',
                    'rotation 270', 'rotation 0', 'mirror 1 0', 'mirror 0 1',
                    'mirror 0 0', 'eye hold', 'look -100 -100', 'look 100 100',
                    'look 0 0', 'pupil 12', 'pupil 50', 'pupil 29', 'blink', 'eye auto']:
        send(command)
    start = send('status')
    time.sleep(4)
    end = send('status')
    def field(line, name):
        return next(word.split('=', 1)[1] for word in line.split() if word.startswith(name+'='))
    assert int(field(end, 'frames')) > int(field(start, 'frames')) + 20
    assert field(start, 'psram_free') == field(end, 'psram_free')
    assert float(field(end, 'fps')) >= 10
    print('PASS: invalid inputs rejected, drawing/lifecycle commands acknowledged, '
          'frames advancing and PSRAM stable.', flush=True)
finally:
    try:
        send('display on')
        send('rotation 0')
        send('mirror 0 0')
        send('brightness 50')
        send('eye auto')
    finally:
        port.close()
        Path('artifacts/serial-check-v2.json').write_text(json.dumps(report, indent=2)+'\n')
