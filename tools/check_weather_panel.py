"""Capture real clear/drizzle/rain/snow/fog skies and verify stable readable rows.

Background previews expire in 60 seconds and never change actual clock/weather
readings. Restores live rendering without changing brightness, sound or settings.
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
OUT=ROOT/'artifacts'/'weather-panel'
OUT.mkdir(parents=True,exist_ok=True)


def command(*args):
    return subprocess.run([sys.executable,str(ROOT/'tools/control.py'),*args,'--port',cli_args.port,'--wait','.3'],
                          check=True,capture_output=True,text=True).stdout


def capture(name):
    path=OUT/(name+'.png')
    for attempt in range(2):
        try:
            subprocess.run([sys.executable,str(ROOT/'tools/snapshot.py'),str(path),'--port',cli_args.port],check=True)
            break
        except subprocess.CalledProcessError:
            if attempt:
                raise
            print('Retrying incomplete USB framebuffer capture',flush=True)
    return Image.open(path).convert('RGB')


def ink_mask(frame,box,color=(255,255,255)):
    return [p==color for p in pixels(frame.crop(box))]


def pixels(frame):
    return [frame.getpixel((x,y)) for y in range(frame.height) for x in range(frame.width)]


report={};reference=None
rows={'device':(62,34,298,66),'weekday':(65,87,295,111),
      'date':(90,125,270,149),'time':(62,165,298,202),
      'weather':(45,227,315,250),'temperature':(95,271,265,301)}
try:
    command('menu','home');command('sky','12','0')
    for label,code in [('clear',0),('overcast',3),('drizzle',51),('rain',63),('heavy-rain',65),('snow',73),('fog',45),('storm',95)]:
        command('sky','weather',str(code))
        frame=capture(label)
        masks={key:ink_mask(frame,box) for key,box in rows.items()}
        assert all(sum(mask)>80 for mask in masks.values()),(label,{k:sum(v) for k,v in masks.items()})
        # Clock minutes can advance during USB transfers; other live rows stay fixed.
        if reference is None:reference=masks
        else:
            for key in ('device','weekday','date'):
                assert masks[key]==reference[key],(label,key,'text moved or was obscured')
        sky=pixels(frame.crop((120,308,240,318)))
        report[label]=dict(ink_pixels={k:sum(v) for k,v in masks.items()},sky_rgb=[sum(p[c] for p in sky)/len(sky) for c in range(3)])
        if label in ('rain','heavy-rain','storm'):
            blue=sum(b>180 and b-g>60 and b-r>90
                for y in range(30,330) for x in range(30,330)
                if (x-180)**2+(y-180)**2<150**2
                for r,g,b in [frame.getpixel((x,y))])
            assert blue>25, (label,'Rain does not stand out blue against the gray sky',blue)
            report[label]['blue_rain_pixels']=blue
        if label in ('drizzle','rain'):
            time.sleep(.5)
            second=capture(label+'-motion')
            changed=sum(a!=b for a,b in zip(pixels(frame),pixels(second)))
            assert changed>150,'Precipitation scene is static'
            for key in ('device','weekday','date'):
                assert ink_mask(second,rows[key])==masks[key],(label,key,'animation covered text')
            report[label]['animated_pixels']=changed
    command('sky','0','0');command('sky','weather','63')
    night=capture('night-rain')
    assert sum(ink_mask(night,rows['date']))>400,'Night text is not white'
finally:
    command('sky','live');command('sky','weather','live');command('menu','home')
report['status']=command('status')
(OUT/'check.json').write_text(json.dumps(report,indent=2)+'\n')
print('PASS: ordered readable rows, weather backgrounds, moving drizzle/rain, and night contrast; live weather restored.')
