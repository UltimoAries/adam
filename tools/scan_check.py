"""Test real Wi-Fi scans and UI selection without connecting or saving credentials."""
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
    time.sleep(0.13)
    port.reset_input_buffer()
    port.write((text+'\n').encode('ascii'))
    deadline = time.monotonic()+5
    while time.monotonic() < deadline:
        line = port.readline().decode('utf-8', errors='replace').strip()
        if line.startswith('OK '): return line
        if line.startswith('ERR '): raise RuntimeError(line)
    raise RuntimeError(f'Timeout: {text}')

def status(**expected):
    line = command('ui status')
    for key, value in expected.items():
        assert re.search(rf'\b{key}={re.escape(str(value))}(?: |$)', line), (expected, line)
    results.append(line)
    return line

def tap(x,y): command(f'ui tap {x} {y}')

def wait_scan():
    deadline = time.monotonic()+16
    while time.monotonic() < deadline:
        line = command('ui status')
        if 'scan=ready' in line:
            results.append(line)
            return int(re.search(r'networks=(\d+)', line)[1])
        assert 'scan=failed' not in line, line
        time.sleep(0.2)
    raise RuntimeError('Scan never completed')

def frames():
    return int(re.search(r'frames=(\d+)', command('status'))[1])

try:
    command('menu settings')
    tap(180,140)  # Entering Wi-Fi starts automatic scan.
    status(page='wifi')
    count = wait_scan()
    tap(120,168); status(page='networks',list_page=0)
    if count > 3:
        tap(265,270); status(page='networks',list_page=1)
        tap(95,270); status(page='networks',list_page=0)
    for _ in range(2):
        before = frames()
        tap(126,310)
        count = wait_scan()
        assert frames() > before + 2, 'Display did not keep rendering during scan'
    if count:
        tap(180,126); status(page='wifi')  # Selection only; no CONNECT.
        tap(120,168); status(page='networks')
    tap(230,310); status(page='keyboard',field='ssid')
    tap(180,35); status(page='wifi')
    tap(120,168); tap(180,35); status(page='wifi')
    command('menu eye')  # Discard unsaved selection.
    status(page='eye')
finally:
    port.close()
Path('artifacts/scan-check-v3.json').write_text(json.dumps(results, indent=2)+'\n')
print(f'PASS: 3 scans, {count} unique visible networks in final scan; selection/manual entry checked; no connection attempted.')
if count <= 3:
    print('Only one result page available; multi-page navigation was not exercised.')
