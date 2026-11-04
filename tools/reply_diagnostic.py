"""Download an explicitly armed reply diagnostic; never starts a recording."""
import argparse
from pathlib import Path
import re
import time
import wave

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--port', default='COM6')
    args = parser.parse_args()
    # Match control.py: do not reset the speaker when opening USB.
    port = serial.Serial(port=None, baudrate=115200, timeout=.2)
    port.dtr = False
    port.rts = False
    port.port = args.port
    port.open()
    try:
        port.reset_input_buffer()
        port.write(b'reply download\n')
        deadline = time.monotonic() + 5
        header = None
        while time.monotonic() < deadline:
            line = port.readline().strip()
            header = re.fullmatch(rb'AUDIO (\d+) 16000 3 16', line)
            if header:
                break
            if line.startswith(b'ERR '):
                raise RuntimeError(line.decode('ascii', errors='replace'))
        if not header:
            raise RuntimeError('No diagnostic header received')
        size = int(header[1])
        if not 0 < size <= 16000 * 15 * 3 * 2 or size % 6:
            raise RuntimeError('Invalid diagnostic length')
        raw = bytearray()
        deadline = time.monotonic() + 30
        while len(raw) < size and time.monotonic() < deadline:
            raw.extend(port.read(min(4096, size - len(raw))))
        if len(raw) != size:
            raise RuntimeError(f'Incomplete diagnostic: {len(raw)}/{size} bytes')
    finally:
        port.close()
    args.directory.mkdir(parents=True, exist_ok=True)
    for channel, name in enumerate(('mic', 'reference', 'clean')):
        mono = bytearray(size // 3)
        mono[0::2] = raw[channel * 2::6]
        mono[1::2] = raw[channel * 2 + 1::6]
        path = args.directory / f'{name}.wav'
        with wave.open(str(path), 'wb') as output:
            output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            output.writeframes(mono)
        print(path)
    print(f'{size / (16000 * 6):.2f} seconds; clean channel is before wake gain')


if __name__ == '__main__':
    main()
