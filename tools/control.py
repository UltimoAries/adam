"""Send a newline-terminated display command without intentionally resetting ESP32."""
import argparse
import time
import serial

parser = argparse.ArgumentParser()
parser.add_argument('command', nargs='+')
parser.add_argument('--port', default='COM6')
parser.add_argument('--wait', type=float, default=3)
args = parser.parse_args()
port = serial.Serial(port=None, baudrate=115200, timeout=0.1)
port.dtr = False
port.rts = False
port.port = args.port
port.open()
try:
    time.sleep(0.2)
    port.reset_input_buffer()
    port.write((' '.join(args.command) + '\n').encode('ascii'))
    deadline = time.monotonic() + args.wait
    response = bytearray()
    while time.monotonic() < deadline:
        response.extend(port.read(port.in_waiting or 1))
    print(response.decode('utf-8', errors='replace'), end='')
    if b'OK ' not in response or b'ERR ' in response:
        raise SystemExit('Device did not acknowledge the command successfully.')
finally:
    port.close()
