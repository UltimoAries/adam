"""Compare saved microphone channels locally, without a supplied phrase prompt."""
import json
import os
import time
import wave
from pathlib import Path
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
import numpy as np
from faster_whisper import WhisperModel

model = WhisperModel('headend/models/stt/base.en', device='cpu', compute_type='int8', cpu_threads=4,
                     local_files_only=True)
results = []
for name in ('mic-speech-v5-mic1.wav', 'mic-speech-36db-v5-mic1.wav', 'mic-speech-36db-v5-mic2.wav'):
    with wave.open('artifacts/recordings/'+name, 'rb') as source:
        audio = np.frombuffer(source.readframes(source.getnframes()), dtype='<i2').astype(np.float32)/32768
    # A fixed amplification reveals low-level capture without changing timing.
    audio = np.clip(audio*16, -1, 1)
    started = time.monotonic()
    segments, _ = model.transcribe(audio, language='en', beam_size=1, vad_filter=False,
                                   condition_on_previous_text=False, temperature=0)
    segments = list(segments)
    result = dict(file=name, seconds=time.monotonic()-started,
                  text=' '.join(s.text.strip() for s in segments),
                  segments=[dict(start=s.start,end=s.end,avg_logprob=s.avg_logprob,
                                 no_speech_prob=s.no_speech_prob) for s in segments])
    results.append(result)
    print(json.dumps(result), flush=True)
Path('artifacts/recordings/mic-stt-diagnostic.json').write_text(json.dumps(results, indent=2)+'\n')
