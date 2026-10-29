"""Inspect saved capture continuity without changing audio or firmware."""
import json
from pathlib import Path
import numpy as np
from headend.clips import parse_wav

results = []
for path in Path('headend/data/clips').glob('*.json'):
    metadata = json.loads(path.read_text())
    if metadata['source'] != 'device':
        continue
    rate, samples = parse_wav(path.with_suffix('.wav').read_bytes())
    report = dict(name=metadata['name'], id=metadata['id'], created=metadata['created'], channels=[])
    for channel in range(samples.shape[1]):
        values = samples[:, channel].astype(np.float64)
        derivative = np.abs(np.diff(values))
        at_boundary = derivative[159::160]
        fft = np.fft.rfft((values-values.mean())*np.hanning(len(values)))
        power = abs(fft)**2
        frequencies = np.fft.rfftfreq(len(values), 1/rate)
        bands = {f'{low}-{high}':float(power[(frequencies>=low)&(frequencies<high)].sum()/max(power.sum(),1))
                 for low,high in [(0,100),(100,500),(500,2000),(2000,4000),(4000,8000)]}
        report['channels'].append(dict(channel=channel+1, rms=float(np.sqrt(np.mean(values*values))),
            dc=float(values.mean()), zero_fraction=float(np.mean(values==0)),
            repeated_adjacent_fraction=float(np.mean(derivative==0)),
            boundary_difference=float(at_boundary.mean()),all_difference=float(derivative.mean()),
            boundary_ratio=float(at_boundary.mean()/max(derivative.mean(),1e-9)),
            bands=bands))
    results.append(report)
results.sort(key=lambda item:item['created'])
Path('artifacts/capture-integrity-analysis.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
