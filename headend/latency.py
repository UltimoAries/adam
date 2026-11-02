"""Per-session timings relative to receipt of the completed utterance.

Server intervals use a monotonic clock. trigger_* estimates add the device's
captured duration; they do not require synchronized device/server clocks.
"""
import time


def mark(entry, stage):
    origin = entry.get('processing_started_monotonic')
    if origin is not None:
        elapsed = round((time.monotonic() - origin) * 1000)
        entry.setdefault('timings_ms', {}).setdefault(stage, elapsed)
        entry['timings_ms'].setdefault('trigger_to_' + stage, entry.get('capture_ms', 0) + elapsed)
