"""Capture the device's actual RGB565 framebuffer over USB (no camera needed)."""
import argparse
import time
from pathlib import Path
import serial
from PIL import Image, ImageDraw

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
parser.add_argument('--port', default='COM6')
args = parser.parse_args()
port = serial.Serial(port=None, baudrate=115200, timeout=2)
port.dtr = port.rts = False
port.port = args.port
port.open()
try:
    time.sleep(0.2)
    port.reset_input_buffer()
    port.write(b'snapshot\n')
    header = b''
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        header = port.readline().strip()
        if header.startswith(b'FRAME '):
            break
    if header != b'FRAME 259200':
        raise RuntimeError(f'Unexpected snapshot header: {header!r}')
    raw = bytearray()
    deadline = time.monotonic() + 20
    while len(raw) < 259200 and time.monotonic() < deadline:
        raw.extend(port.read(min(4096, 259200-len(raw))))
    if len(raw) != 259200:
        raise RuntimeError(f'Incomplete frame: {len(raw)} bytes')
finally:
    port.close()
rgb = bytearray()
for offset in range(0, len(raw), 2):
    pixel = (raw[offset] << 8) | raw[offset+1]
    rgb.extend((((pixel >> 11) & 31)*255//31, ((pixel >> 5) & 63)*255//63, (pixel & 31)*255//31))
frame = Image.frombytes('RGB', (360, 360), bytes(rgb))
mask = Image.new('L', (360, 360))
ImageDraw.Draw(mask).ellipse((0, 0, 359, 359), fill=255)
frame = Image.composite(frame, Image.new('RGB', frame.size, '#16191d'), mask)
args.output.parent.mkdir(parents=True, exist_ok=True)
frame.save(args.output)
print(f'Saved device framebuffer: {args.output}')
