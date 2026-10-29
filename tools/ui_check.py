"""Exercise UI hit regions over USB. Run before entering real Wi-Fi credentials.

Does not connect, save or erase Wi-Fi credentials. Restores original levels
and leaves the eye page active. Physical touch mapping is a separate check.
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
    command('menu eye')
    line = status()
    for target, field, x in [(v,'volume',33), (b,'brightness',326)]:
        current = int(re.search(rf'{field}=(\d+)', line)[1])
        for _ in range(abs(target-current)//10):
            tap(x, 130 if target>current else 230)
    status(page='eye', volume=v, brightness=b)

original = command('ui status')
v = int(re.search(r'volume=(\d+)', original)[1])
b = int(re.search(r'brightness=(\d+)', original)[1])
try:
    command('menu eye')
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
    tap(180,325); status(page='eye')
    tap(180,35); tap(180,210); status(page='eye')
    tap(180,35); tap(180,140); status(page='wifi')
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
    finally:
        port.close()
Path('artifacts/ui-check-v3.json').write_text(json.dumps(results, indent=2)+'\n')
print(f'PASS: {len(results)} UI assertions; original levels restored; no Wi-Fi credentials changed.')
