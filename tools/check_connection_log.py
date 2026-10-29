"""Check a serial stability capture for crashes, frame resets, and Wi-Fi loss."""
import argparse
import json
import re
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('log', type=Path)
args = parser.parse_args()
text = args.log.read_text(encoding='utf-8', errors='replace')
for failure in ('stack overflow', 'Guru Meditation', 'Rebooting...', 'Brownout',
                'HOST serial reconnect', 'abort() was called'):
    assert failure.lower() not in text.lower(), f'Failure in log: {failure}'
assert text.count('READY eye auto') <= 1, 'Unexpected repeated boots'
frames = [int(n) for n in re.findall(r'OK status .*?\bframes=(\d+)', text)]
assert len(frames) >= 3, 'Insufficient frame samples'
assert all(b > a for a,b in zip(frames, frames[1:])), 'Frame counter stopped or reset'
ui = [line for line in text.splitlines() if line.startswith('OK ui ')]
connected = [i for i,line in enumerate(ui) if 'wifi=CONNECTED ' in line]
assert len(connected) >= 3, 'Insufficient connected samples'
assert all('wifi=CONNECTED ' in line for line in ui[connected[0]:]), 'Connection was lost'
headroom = [int(n) for n in re.findall(r'event_stack_min=(\d+)', text) if int(n)>0]
assert headroom and min(headroom)>=1024, 'Insufficient event-task stack headroom'
report = {'log': str(args.log), 'frame_samples': len(frames),
          'frames_first': frames[0], 'frames_last': frames[-1],
          'connected_samples': len(connected),
          'minimum_event_stack_free_bytes': min(headroom),
          'crashes': 0, 'unexpected_resets': 0, 'result': 'PASS'}
args.log.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(report, indent=2))
