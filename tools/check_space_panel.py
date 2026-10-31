"""Capture the real sky panel in idle/listening/playback states over USB.

Temporarily mutes output, records a volatile local mic clip (never uploaded),
and plays a muted tone. Previews a clear midday sky to check that its sun stays
visible through activity mist. Restores live sky/weather and volume. Run when idle.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORT = os.environ.get('VOICE_USB_PORT', 'COM6' if os.name == 'nt' else '/dev/ttyACM0')
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port', default=DEFAULT_PORT)
cli_args = parser.parse_args()
OUT = ROOT / 'artifacts' / 'space-panel'
OUT.mkdir(parents=True, exist_ok=True)
report = {}


def command(*args):
    result = subprocess.run([sys.executable, str(ROOT/'tools/control.py'), *args, '--port', cli_args.port, '--wait', '0.5'],
                            check=True, capture_output=True, text=True)
    return result.stdout


def capture(name):
    path = OUT / (name+'.png')
    subprocess.run([sys.executable, str(ROOT/'tools/snapshot.py'), str(path), '--port', cli_args.port], check=True)
    return Image.open(path).convert('RGB')


def mean_background(frame):
    # Empty sky beside the clock, clear of illustrated clouds and lettering.
    return mean_rgb(frame, (25, 155, 60, 210))


def mean_rgb(frame, box):
    patch = frame.crop(box)
    pixels = [patch.getpixel((x,y)) for y in range(patch.height) for x in range(patch.width)]
    return tuple(sum(pixel[c] for pixel in pixels)/len(pixels) for c in range(3))


def sun_contrast(frame):
    # The yellow disc retains red contrast against the adjacent blue sky,
    # including when the green/blue mist tints both. Avoid the face and rim.
    return mean_rgb(frame, (278, 67, 288, 73))[0]-mean_rgb(frame, (251, 67, 261, 73))[0]


status = command('ui', 'status')
volume = re.search(r'volume=(\d+)', status)[1]
assert 'playing=0' in command('audio', 'status'), 'Wait for playback to finish'
assert 'recording=0' in command('mic', 'status'), 'Wait for capture to finish'
try:
    command('menu', 'home')
    command('volume', '0')
    command('sky', '12', '0')
    command('sky', 'weather', '0')
    idle = capture('idle')
    time.sleep(2)
    second = capture('idle-sky-motion')
    sky_boxes=((40,10,320,60),(60,288,300,340))
    assert any(idle.crop(box).tobytes()!=second.crop(box).tobytes() for box in sky_boxes), 'Sky clouds/stars are static'
    report['panel'] = command('headend', 'status')
    assert 'panel clock=1 weather=ready' in report['panel'], report['panel']
    command('mic', 'record', '15000')
    assert 'recording=1' in command('mic', 'status')
    report['listening_status'] = command('status')
    listening = capture('listening')
    r,g,b = mean_background(listening)
    ir,ig,ib = mean_background(idle)
    assert g-b>ig-ib+12, (r,g,b,ir,ig,ib)
    assert sun_contrast(listening)>35, 'Listening replaced or obscured the sky sun'
    command('ui', 'tap', '180', '180')
    assert 'recording=0' in command('mic', 'status'), 'First center tap did not stop capture'
    command('menu', 'home')
    command('audio', 'tone', '440', '30000')
    assert 'playing=1' in command('audio', 'status')
    report['playing_status'] = command('status')
    playing = capture('playing')
    r,g,b = mean_background(playing)
    assert b-g>ib-ig+6, (r,g,b,ir,ig,ib)
    assert sun_contrast(playing)>35, 'Playback replaced or obscured the sky sun'
    command('ui', 'tap', '180', '180')
    assert 'playing=0' in command('audio', 'status'), 'First center tap did not stop playback'
    command('menu', 'home')
    command('ui', 'tap', '33', '130')
    assert 'volume=0' in command('ui', 'status'), 'First tap changed volume'
    capture('controls')
    report['background_rgb'] = {name:mean_background(frame) for name,frame in
                                [('idle',idle),('listening',listening),('playing',playing)]}
    report['sun_contrast'] = {name:sun_contrast(frame) for name,frame in
                              [('idle',idle),('listening',listening),('playing',playing)]}
    report['status'] = command('status')
    report['result'] = 'PASS'
finally:
    command('audio', 'stop')
    command('mic', 'stop')
    command('volume', volume)
    command('sky', 'live')
    command('sky', 'weather', 'live')
    command('menu', 'home')
(OUT/'check.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS: animated sky remains beneath green/blue mist, live clock/weather, touch stop; live sky and original volume restored.')
