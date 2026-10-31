"""Install a short original two-note trigger chime; build/flash afterward."""
from array import array
import hashlib
import json
import math
from pathlib import Path
import sys
import wave

ROOT=Path(__file__).resolve().parent.parent
RATE=16000
SECONDS=.12
# The acknowledgement plays while the microphone captures the command. The old
# 10,000 peak overloaded the adjacent mic; keep it 20 dB below that level.
PEAK=1000
NOTES=[(.005,.045,880.0),(.055,.060,1174.66)]


def main():
    values=[0.0]*round(RATE*SECONDS)
    for start,duration,hz in NOTES:
        for i in range(round(RATE*duration)):
            t=i/RATE
            envelope=min(1,t/.003)*min(1,(duration-t)/.006)*math.exp(-3*t/duration)
            values[round(start*RATE)+i]+=envelope*(math.sin(2*math.pi*hz*t)+.12*math.sin(4*math.pi*hz*t))
    scale=PEAK/max(abs(v) for v in values)
    samples=array('h',(round(v*scale) for v in values))
    if sys.byteorder!='little':samples.byteswap()
    pcm=samples.tobytes()
    assert len(pcm)==3840 and max(abs(v) for v in values)>0
    (ROOT/'firmware/main/listen_cue.pcm').write_bytes(pcm)
    metadata=dict(text='Trigger chime',cue_kind='chime',source='Original locally synthesized two-note chime',
        rate=RATE,channels=1,bits=16,seconds=SECONDS,peak=PEAK,frequencies_hz=[n[2] for n in NOTES],
        sha256=hashlib.sha256(pcm).hexdigest(),generator='tools/make_trigger_chime.py')
    (ROOT/'firmware/main/listen_cue.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    preview=ROOT/'artifacts/recordings/trigger-chime.wav';preview.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(preview),'wb') as target:
        target.setparams((1,2,RATE,0,'NONE','not compressed'));target.writeframes(pcm)
    print(json.dumps(metadata))


if __name__=='__main__':main()
