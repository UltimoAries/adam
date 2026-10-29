"""Make a clearly labeled, louder review copy of an existing mono test WAV."""
import argparse
import wave
from pathlib import Path
import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('input', type=Path)
parser.add_argument('output', type=Path)
args = parser.parse_args()
with wave.open(str(args.input), 'rb') as source:
    params = source.getparams()
    if params.nchannels != 1 or params.sampwidth != 2 or params.comptype != 'NONE':
        parser.error('Input must be mono 16-bit PCM')
    raw = source.readframes(params.nframes)
data = np.frombuffer(raw, dtype='<i2').astype(np.float64)
peak = np.max(np.abs(data)) if len(data) else 0
gain = min(32, 16384/peak) if peak else 1
with wave.open(str(args.output), 'wb') as target:
    target.setparams(params)
    target.writeframes(np.clip(np.rint(data*gain), -32768, 32767).astype('<i2').tobytes())
print(f'Review copy gain: {gain:.2f}x; original remains unchanged.')
