"""Measure the same central screen region across captured color/brightness tests."""
import json
from pathlib import Path
import cv2
import numpy as np

base = Path('artifacts')
white = cv2.imread(str(base / 'colors/white.jpg'))
gray = cv2.cvtColor(white, cv2.COLOR_BGR2GRAY)
contours, _ = cv2.findContours((gray > 30).astype('uint8'), cv2.RETR_EXTERNAL,
                              cv2.CHAIN_APPROX_SIMPLE)
(x, y), radius = cv2.minEnclosingCircle(max(contours, key=cv2.contourArea))
yy, xx = np.ogrid[:gray.shape[0], :gray.shape[1]]
mask = (xx-x)**2 + (yy-y)**2 < (radius*0.55)**2
report = {'roi': [x, y, radius*0.55], 'channel_order': 'RGB', 'samples': {}}
for path in sorted((base / 'colors').glob('*.jpg')) + sorted((base / 'brightness').glob('*.jpg')):
    frame = cv2.imread(str(path))
    report['samples'][path.as_posix()] = [round(float(v), 2) for v in frame[mask].mean(axis=0)[::-1]]
(base / 'color-brightness-metrics.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
