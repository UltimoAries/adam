"""Exercise UI hit regions over USB. Run before entering real Wi-Fi credentials.

Does not connect, save or erase Wi-Fi credentials. Restores original levels
and leaves the home page active. Physical touch mapping is a separate check.
"""
import argparse
import json
import os
import re
import time
from pathlib import Path
import serial

DEFAULT_PORT = os.environ.get('VOICE_USB_PORT', 'COM6' if os.name == 'nt' else '/dev/ttyACM0')

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', default=DEFAULT_PORT)
args = parser.parse_args()

port = serial.Serial(port=None, baudrate=115200, timeout=0.1)
port.dtr = port.rts = False
port.port = args.port
port.open()
results = []

def command(text):
    time.sleep(0.13)  # Each synthetic press is distinct from debounce.
    port.reset_input_buffer()
    port.write((text+'\n').encode('ascii'))
    end = time.monotonic()+4
    while time.monotonic() < end:
        line = port.readline().decode('utf-8', errors='replace').strip()
        if line.startswith('OK '):
            return line
        if line.startswith('ERR '):
            raise RuntimeError(line)
    raise RuntimeError(f'Timeout: {text}')

def status(**expected):
    line = command('ui status')
    for key, value in expected.items():
        assert re.search(rf'\b{key}={re.escape(str(value))}(?: |$)', line), (expected, line)
    results.append({'expected': expected, 'response': line})
    return line

def tap(x, y):
    command(f'ui tap {x} {y}')

def restore(v, b):
    command('menu home')
    tap(180,180)  # Reveal controls before adjusting levels.
    line = status()
    for target, field, x in [(v,'volume',33), (b,'brightness',326)]:
        current = int(re.search(rf'{field}=(\d+)', line)[1])
        for _ in range(abs(target-current)//10):
            tap(x, 130 if target>current else 230)
    status(page='home', volume=v, brightness=b)


def settings():
    command('menu home')
    tap(180,180)
    tap(180,35)
    status(page='settings')

original = command('ui status')
v = int(re.search(r'volume=(\d+)', original)[1])
b = int(re.search(r'brightness=(\d+)', original)[1])
try:
    command('menu home')
    status(page='home',controls=0)
    tap(33,130)
    status(volume=v,brightness=b,controls=1)  # First tap must not change levels.
    time.sleep(8.2)
    status(controls=0)
    tap(326,230)
    status(volume=v,brightness=b,controls=1)
    for _ in range(12): tap(33,130)
    status(volume=100)
    for _ in range(12): tap(33,230)
    status(volume=0)
    for _ in range(12): tap(326,130)
    status(brightness=100)
    for _ in range(12): tap(326,230)
    status(brightness=10)
    restore(v,b)
    tap(180,35); status(page='settings')
    tap(180,325); status(page='home',controls=0)
    settings(); tap(120,210); status(page='home',controls=0)
    settings(); tap(180,140); status(page='wifi')
    tap(240,168); status(page='keyboard', field='ssid', keyboard=0)
    tap(49,165)  # q
    tap(80,280); status(keyboard=1)
    tap(49,165)  # Q
    tap(80,280); status(keyboard=2)
    tap(49,165)  # 1
    tap(80,280); status(keyboard=3)
    tap(77,165)  # ?
    tap(212,280)  # delete
    tap(147,280)  # space
    tap(180,35); status(page='wifi')  # cancel leaves original SSID untouched
    tap(180,220); status(page='keyboard', field='password')
    tap(49,165)
    tap(180,35); status(page='wifi')
    tap(180,302); status(page='forget')
    tap(180,200); status(page='wifi')  # keep, never erase real credentials
    tap(180,35); status(page='settings')
finally:
    try:
        restore(v,b)
        tap(180,180)
        status(page='home',controls=0)
    finally:
        port.close()
Path('artifacts/ui-check-v14.json').write_text(json.dumps(results, indent=2)+'\n')
print(f'PASS: {len(results)} UI assertions; original levels restored; no Wi-Fi credentials changed.')
