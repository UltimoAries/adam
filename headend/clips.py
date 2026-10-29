"""Lossless local WAV storage and explicit diagnostic views."""
import hashlib
import io
import json
import math
import re
import threading
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
import av
import numpy as np

MAX_UPLOAD = 4 * 1024 * 1024
MAX_SECONDS = 15


def parse_wav(raw):
    if len(raw) > MAX_UPLOAD:
        raise ValueError('Clip exceeds the 4 MiB upload limit')
    try:
        with wave.open(io.BytesIO(raw), 'rb') as source:
            channels, width, rate, count, compression, _ = source.getparams()
            if compression != 'NONE' or width != 2 or channels not in (1, 2) or not 8000 <= rate <= 48000:
                raise ValueError('Use a 16-bit PCM WAV, mono or stereo, at 8–48 kHz')
            if not 0 < count <= rate * MAX_SECONDS:
                raise ValueError('Use a clip between 0 and 15 seconds long')
            pcm = source.readframes(count)
            if len(pcm) != count * channels * width:
                raise ValueError('The WAV payload is truncated')
    except (wave.Error, EOFError) as exc:
        raise ValueError('This file is not a supported PCM WAV') from exc
    return rate, np.frombuffer(pcm, dtype='<i2').reshape(-1, channels)


def wav_bytes(rate, samples):
    samples = np.asarray(samples, dtype='<i2')
    if samples.ndim == 1:
        samples = samples[:, None]
    target = io.BytesIO()
    with wave.open(target, 'wb') as output:
        output.setparams((samples.shape[1], 2, rate, 0, 'NONE', 'not compressed'))
        output.writeframes(samples.tobytes())
    return target.getvalue()


def metrics(samples):
    result = []
    for ch in range(samples.shape[1]):
        values = samples[:, ch].astype(np.float64)
        rms = float(np.sqrt(np.mean(values * values)))
        peak = float(np.max(np.abs(values)))
        result.append(dict(channel=ch + 1, rms=round(rms, 2), peak=int(peak),
            dbfs=round(20 * math.log10(rms / 32768), 1) if rms else None,
            clipped=int(np.count_nonzero(np.abs(values) >= 32760)),
            waveform=[round(float(np.max(np.abs(part))) / 32768, 5)
                      for part in np.array_split(values, min(240, len(values)))]))
    return result


def mono_16k(rate, samples, channel, boost=False):
    if not 1 <= channel <= samples.shape[1]:
        raise ValueError('That microphone channel is not present in this clip')
    selected = samples[:, channel - 1].copy()
    if rate != 16000:
        frame = av.AudioFrame.from_ndarray(selected.reshape(1, -1), format='s16', layout='mono')
        frame.sample_rate = rate
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        chunks = resampler.resample(frame) + resampler.resample(None)
        selected = np.concatenate([f.to_ndarray().reshape(-1) for f in chunks])
    gain = 1.0
    if boost:
        values = selected.astype(np.float64)
        rms = float(np.sqrt(np.mean(values * values)))
        peak = float(np.max(np.abs(values)))
        if rms and peak:
            gain = max(1, min(32, 1800 / rms, 30000 / peak))
            selected = np.rint(values * gain).astype('<i2')
    return selected.tobytes(), round(gain, 3)


class ClipStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def list(self):
        clips = []
        for path in self.directory.glob('*.json'):
            try:
                clips.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError):
                continue
        return sorted(clips, key=lambda item: item['created'], reverse=True)

    def get(self, clip_id):
        if not re.fullmatch(r'[a-f0-9]{32}', clip_id):
            raise KeyError('Clip not found')
        try:
            metadata = json.loads((self.directory / f'{clip_id}.json').read_text(encoding='utf-8'))
            raw = (self.directory / f'{clip_id}.wav').read_bytes()
        except (OSError, ValueError) as exc:
            raise KeyError('Clip not found') from exc
        return metadata, raw

    def add(self, raw, name, source='upload'):
        rate, data = parse_wav(raw)
        digest = hashlib.sha256(raw).hexdigest()
        with self.lock:
            existing = self.list()
            for item in existing:
                if item['sha256'] == digest:
                    return item
            if len(existing) >= 48 or sum(item['bytes'] for item in existing) + len(raw) > 64 * 1024 * 1024:
                raise ValueError('Clip storage is full. Delete a test clip before uploading another.')
            clip_id = uuid.uuid4().hex
            clean_name = str(name).replace('\\', '/').split('/')[-1][:120] or 'clip.wav'
            metadata = dict(id=clip_id, name=clean_name, source=source, bytes=len(raw),
                created=datetime.now(timezone.utc).isoformat(), sha256=digest, rate=rate,
                channels=data.shape[1], seconds=round(len(data) / rate, 3), bits=16,
                levels=metrics(data))
            (self.directory / f'{clip_id}.wav').write_bytes(raw)
            (self.directory / f'{clip_id}.json').write_text(json.dumps(metadata), encoding='utf-8')
            return metadata

    def delete(self, clip_id):
        with self.lock:
            self.get(clip_id)
            (self.directory / f'{clip_id}.json').unlink()
            (self.directory / f'{clip_id}.wav').unlink()
