"""Preview the real panel at four times of day, then restore live local time.
Only the sky's time of day is previewed; clock/weather data stays live.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_PORT=os.environ.get('VOICE_USB_PORT','COM6' if os.name=='nt' else '/dev/ttyACM0')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--port',default=DEFAULT_PORT)
cli_args=parser.parse_args()
OUT=ROOT/'artifacts'/'daylight-panel'
OUT.mkdir(parents=True,exist_ok=True)

def command(*args):
    return subprocess.run([sys.executable,str(ROOT/'tools/control.py'),*args,'--port',cli_args.port,'--wait','.5'],
                          check=True,capture_output=True,text=True).stdout

def pixels(frame,box):
    patch=frame.crop(box)
    return [patch.getpixel((x,y)) for y in range(patch.height) for x in range(patch.width)]

def wire_color(rgb):
    r,g,b=rgb
    return ((r>>3)*255//31,(g>>2)*255//63,(b>>3)*255//31)


report={};date_mask=None;frames={}
for _ in range(20):
    if 'panel clock=1' in command('headend','status'):break
    time.sleep(.5)
else:raise RuntimeError('Panel clock did not sync')
try:
    command('sky','weather','0')
    for label,hour,minute in [('night',0,0),('sunrise',6,30),('day',12,0),('sunset',18,0)]:
        command('sky',str(hour),str(minute))
        path=OUT/(label+'.png')
        subprocess.run([sys.executable,str(ROOT/'tools/snapshot.py'),str(path),'--port',cli_args.port],check=True)
        frame=Image.open(path).convert('RGB');frames[label]=frame
        background=pixels(frame,(28,110,55,135))
        rgb=[sum(p[c] for p in background)/len(background) for c in range(3)]
        ink=wire_color((255,255,255))
        mask=[p==ink for p in pixels(frame,(90,125,270,149))]
        assert sum(mask)>400,'Date is not legible at the uniform label size'
        if date_mask is not None:assert mask==date_mask,'Date moved during palette changes'
        date_mask=mask
        horizon=pixels(frame,(150,340,210,350))
        report[label]=dict(sky=rgb,horizon=[sum(p[c] for p in horizon)/len(horizon) for c in range(3)])
    assert max(report['night']['sky'])<30,report
    r,g,b=report['day']['sky']
    assert b>r*2 and g>r*1.5 and b>g,report
    for label in ('sunrise','sunset'):
        r,g,b=report[label]['horizon']
        assert r>g and g>b,report
    sun=pixels(frames['day'],(264,60,301,97))
    assert sum(r>240 and g>190 and b<90 for r,g,b in sun)>650,'Yellow upper-right sun is missing'
    assert sum(frames['night'].getpixel((230,30)))>sum(frames['night'].getpixel((236,30)))+90,'Night stars are missing'
finally:
    command('sky','live');command('sky','weather','live');command('menu','home')
time.sleep(3)
report['status']=command('status')
(OUT/'check.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS: upper-right yellow sun, warm dawn/dusk, night stars and white text; live time/weather restored.')
