"""Short, serialized USB operations against the current diagnostic firmware."""
import re
import threading
import time
import numpy as np
import serial
from .clips import wav_bytes


class DeviceError(RuntimeError):
    pass


class DeviceBridge:
    def __init__(self, port='COM6'):
        self.port = port
        self.lock = threading.Lock()

    @staticmethod
    def fields(line):
        return dict(re.findall(r'(\w+)=([^ ]+)', line))

    @staticmethod
    def command(port, command):
        port.write((command + '\n').encode('ascii'))
        end = time.monotonic() + 4
        while time.monotonic() < end:
            line = port.readline().decode('utf-8', errors='replace').strip()
            if line.startswith('OK '):
                return line
            if line.startswith('ERR '):
                raise DeviceError(line[4:])
        raise DeviceError('The device did not respond. Check its USB connection.')

    def run(self, operation, value=None):
        if not self.lock.acquire(blocking=False):
            raise DeviceError('USB is busy. Wait for the current operation to finish.')
        port = None
        try:
            port = serial.Serial(port=None, baudrate=115200, timeout=0.15, write_timeout=2)
            port.dtr = port.rts = False
            port.port = self.port
            port.open()
            time.sleep(0.12)
            port.reset_input_buffer()
            if operation == 'status':
                return dict(port=self.port, mic=self.fields(self.command(port, 'mic status')),
                    audio=self.fields(self.command(port, 'audio status')),
                    ui=self.fields(self.command(port, 'ui status')))
            if operation == 'provision':
                self.command(port,f"headend config {value['url']} {value['id']} {value['token']}")
                return {'provisioned':True}
            if operation == 'record':
                self.command(port, f'mic record {value}')
                return {'recording': True}
            if operation == 'stop':
                self.command(port, 'mic stop')
                self.command(port, 'audio stop')
                return {'stopped': True}
            if operation == 'replay':
                self.command(port, f'mic play {value}')
                return {'playing': True}
            if operation == 'fetch':
                state = self.fields(self.command(port, 'mic status'))
                if state['recording'] != '0' or int(state['bytes']) == 0:
                    raise DeviceError('Record a clip on the box first, then wait for it to finish.')
                if state['session_errors'] != '0' or state['session_overflows'] != '0':
                    raise DeviceError('That recording had capture errors. Record a new clip.')
                port.write(b'mic download\n')
                header = port.readline().strip()
                match = re.fullmatch(rb'AUDIO (\d+) 16000 2 16', header)
                if not match or not 0 < int(match[1]) <= 960000 or int(match[1]) % 4:
                    raise DeviceError('Invalid device audio header')
                size = int(match[1])
                if size != int(state['bytes']):
                    raise DeviceError('The device clip changed. Fetch it again.')
                raw = bytearray()
                end = time.monotonic() + 12
                while len(raw) < size and time.monotonic() < end:
                    raw.extend(port.read(min(4096, size - len(raw))))
                if len(raw) != size:
                    raise DeviceError('Audio download was incomplete. Try again.')
                samples = np.frombuffer(raw, dtype='<i2').reshape(-1, 2)
                return wav_bytes(16000, samples)
            raise DeviceError('Unsupported device operation')
        except (serial.SerialException, OSError) as exc:
            raise DeviceError(f'Cannot open {self.port}. Close other serial tools and check the USB cable.') from exc
        finally:
            if port is not None:
                port.close()
            self.lock.release()
