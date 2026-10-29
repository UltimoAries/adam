"""Capture boot/runtime output without toggling DTR or RTS; reconnect if USB resets."""
import argparse
import time
from pathlib import Path
import serial

p = argparse.ArgumentParser()
p.add_argument('output', type=Path)
p.add_argument('--seconds', type=float, default=30)
p.add_argument('--port', default='COM6')
p.add_argument('--status', action='store_true')
a = p.parse_args()
end = time.monotonic()+a.seconds
port = None
next_status = 0
a.output.parent.mkdir(parents=True, exist_ok=True)
with a.output.open('wb') as log:
    while time.monotonic() < end:
        try:
            if port is None:
                port = serial.Serial(port=None, baudrate=115200, timeout=0.2)
                port.dtr = port.rts = False
                port.port = a.port
                port.open()
            if a.status and time.monotonic() >= next_status:
                port.write(b'status\nui status\n')
                next_status = time.monotonic()+5
            data = port.read(port.in_waiting or 1)
            if data:
                log.write(data)
                log.flush()
        except (serial.SerialException, OSError) as error:
            log.write(f'\nHOST serial reconnect: {error}\n'.encode())
            if port is not None:
                port.close()
            port = None
            time.sleep(0.5)
    if port is not None:
        port.close()
data = a.output.read_bytes().decode('utf-8', errors='replace')
print(data[-16000:])
print(f'Captured {len(data)} characters in {a.output}')
