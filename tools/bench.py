"""Run display checks with serial acknowledgements and webcam evidence."""
import argparse
import json
import os
from pathlib import Path
import time
import cv2
import serial

DEFAULT_PORT = os.environ.get('VOICE_USB_PORT', 'COM6' if os.name == 'nt' else '/dev/ttyACM0')

parser = argparse.ArgumentParser()
parser.add_argument('suite', choices=['colors', 'brightness', 'mapping', 'eye'])
parser.add_argument('--port', default=DEFAULT_PORT)
parser.add_argument('--exposure', type=float, default=-6)
args = parser.parse_args()
out = Path('artifacts') / args.suite
out.mkdir(parents=True, exist_ok=True)
port = serial.Serial(port=None, baudrate=115200, timeout=0.1)
port.dtr = False
port.rts = False
port.port = args.port
cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
report = []

def command(text):
    port.reset_input_buffer()
    port.write((text + '\n').encode('ascii'))
    deadline = time.monotonic() + 5
    data = bytearray()
    while time.monotonic() < deadline:
        data.extend(port.read(port.in_waiting or 1))
        lines = data.decode(errors='replace').replace('\r', '').split('\n')[:-1]
        if any(line.startswith(('OK ', 'ERR ')) for line in lines):
            break
    response = data.decode(errors='replace').strip()
    print(text, '=>', response, flush=True)
    if not any(line.startswith('OK ') for line in response.splitlines()) or 'ERR ' in response:
        raise RuntimeError(response or f'No acknowledgement for {text}')
    return response

def capture(name, seconds=1.5):
    deadline = time.monotonic() + seconds
    frame = None
    while time.monotonic() < deadline:
        ok, candidate = cap.read()
        if ok:
            frame = candidate
    if frame is None or not cv2.imwrite(str(out / (name + '.jpg')), frame):
        raise RuntimeError('Camera capture failed')
    return frame

try:
    if not cap.isOpened():
        raise RuntimeError('Camera unavailable')
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
    cap.set(cv2.CAP_PROP_EXPOSURE, args.exposure)
    port.open()
    command('status')
    if args.suite == 'colors':
        command('brightness 50')
        steps = [(name, f'fill {rgb}') for name, rgb in
                 [('red', 'FF0000'), ('green', '00FF00'), ('blue', '0000FF'),
                  ('white', 'FFFFFF'), ('black', '000000')]]
    elif args.suite == 'brightness':
        command('fill FFFFFF')
        steps = [(f'level-{n:03}', f'brightness {n}') for n in [0, 25, 50, 80, 100]]
    elif args.suite == 'mapping':
        command('brightness 50')
        command('pattern grid')
        steps = [(f'rotation-{n}', f'rotation {n}') for n in [0, 90, 180, 270, 0]]
        steps += [('mirror-x', 'mirror 1 0'), ('mirror-y', 'mirror 0 1'),
                  ('normal', 'mirror 0 0'), ('bars', 'pattern colors')]
    else:
        command('brightness 50')
        command('eye hold')
        steps = [('center', 'look 0 0'), ('left', 'look -70 0'),
                 ('right', 'look 70 0'), ('up', 'look 0 -70'),
                 ('down', 'look 0 70'), ('auto', 'eye auto')]
    for name, text in steps:
        response = command(text)
        capture(name)
        report.append({'name': name, 'command': text, 'response': response})
    if args.suite == 'eye':
        for i in range(6):
            capture(f'animation-{i}', seconds=0.5)
        report.append({'status': command('status')})
finally:
    if port.is_open:
        try:
            if args.suite in ('colors', 'brightness'):
                command('red')
                command('brightness 80')
            else:
                command('rotation 0')
                command('mirror 0 0')
                command('eye auto')
        finally:
            port.close()
    if cap.isOpened():
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)
        cap.release()
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
