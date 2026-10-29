"""Prepare the locally synthesized sleepy Huh cue for 16 kHz device playback."""
import hashlib
import json
from pathlib import Path
import wave
import numpy as np
import av
from fractions import Fraction

root=Path(__file__).resolve().parent.parent
with wave.open(str(root/'artifacts/recordings/listen-cue-source.wav'),'rb') as source:
    assert (source.getframerate(),source.getnchannels(),source.getsampwidth())==(16000,1,2)
    values=np.frombuffer(source.readframes(source.getnframes()),dtype='<i2').astype(np.float64)
audible=np.flatnonzero(abs(values)>max(50,abs(values).max()*.015))
assert len(audible)
values=values[max(0,audible[0]-960):min(len(values),audible[-1]+1600)]
# Stretch separately from pitch so a drawn-out vowel stays intelligible.
frame=av.AudioFrame.from_ndarray(np.rint(values).astype('<i2').reshape(1,-1),format='s16',layout='mono')
frame.sample_rate=16000;frame.time_base=Fraction(1,16000);frame.pts=0
graph=av.filter.Graph();source_filter=graph.add_abuffer(sample_rate=16000,format='s16',layout='mono',channels=1,time_base=Fraction(1,16000))
tempo=graph.add('atempo','0.62');sink=graph.add('abuffersink')
source_filter.link_to(tempo);tempo.link_to(sink);graph.configure()
source_filter.push(frame);source_filter.push(None)
resampler=av.AudioResampler(format='s16',layout='mono',rate=16000);parts=[]
while True:
    try:output_frame=sink.pull()
    except (av.error.EOFError,av.error.BlockingIOError):break
    parts.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(output_frame))
parts.extend(f.to_ndarray().reshape(-1) for f in resampler.resample(None))
values=np.concatenate(parts).astype(np.float64)
values=np.interp(np.arange(0,len(values)-1,1/1.14),np.arange(len(values)),values)
values-=values.mean();values*=10000/max(abs(values).max(),1)
fade=min(128,len(values)//4);values[:fade]*=np.linspace(0,1,fade);values[-fade:]*=np.linspace(1,0,fade)
pcm=np.rint(values).astype('<i2').tobytes()
assert 0<len(pcm)<=64000
(root/'firmware/main/listen_cue.pcm').write_bytes(pcm)
with wave.open(str(root/'artifacts/recordings/listen-cue.wav'),'wb') as target:
    target.setparams((1,2,16000,0,'NONE','not compressed'));target.writeframes(pcm)
metadata=dict(text='Huh?',voice='Microsoft David Desktop',source='Local Windows SAPI',rate=16000,
    channels=1,bits=16,seconds=round(len(pcm)/32000,3),peak=10000,tempo=.62,pitch_slowdown=1.14,
    sha256=hashlib.sha256(pcm).hexdigest())
(root/'firmware/main/listen_cue.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(json.dumps(metadata))
