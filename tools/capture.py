"""Capture a local webcam frame after allowing exposure to settle."""
import argparse
from pathlib import Path
import time
import cv2

parser = argparse.ArgumentParser()
parser.add_argument('output', type=Path)
parser.add_argument('--camera', type=int, default=0)
parser.add_argument('--exposure', type=float, help='Manual DirectShow exposure, e.g. -9')
args = parser.parse_args()
cap = cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
original_exposure = cap.get(cv2.CAP_PROP_EXPOSURE)
original_auto = cap.get(cv2.CAP_PROP_AUTO_EXPOSURE)
try:
    if not cap.isOpened():
        raise SystemExit('Cannot open webcam; check Windows camera access and other camera apps.')
    if args.exposure is not None:
        auto = cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0)
        changed = cap.set(cv2.CAP_PROP_EXPOSURE, args.exposure)
        print(f'Manual exposure: auto_set={auto}, exposure_set={changed}, '
              f'value={cap.get(cv2.CAP_PROP_EXPOSURE)}')
    deadline = time.monotonic() + 3
    frame = None
    while time.monotonic() < deadline:
        ok, candidate = cap.read()
        if ok:
            frame = candidate
    if frame is None:
        raise SystemExit('No frame received.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), frame):
        raise SystemExit('Could not save frame.')
    print(args.output.resolve())
finally:
    if cap.isOpened() and args.exposure is not None:
        cap.set(cv2.CAP_PROP_EXPOSURE, original_exposure)
        # DirectShow may not expose the current mode (get returns -1).
        # In that case return to automatic exposure after the temporary test.
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, original_auto if original_auto >= 0 else 1)
    cap.release()
