"""Check the idle LLM ring from the actual USB framebuffer; no audio is uploaded."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_PORT=os.environ.get('VOICE_USB_PORT','COM6' if os.name=='nt' else '/dev/ttyACM0')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expect',choices=['pi','gpu'],required=True)
    parser.add_argument('--port',default=DEFAULT_PORT)
    parser.add_argument('--name')
    args=parser.parse_args()
    def command(*words):
        return subprocess.check_output([sys.executable,str(ROOT/'tools/control.py'),*words,
            '--port',args.port,'--wait','.25'],text=True)
    status=''
    for _ in range(50):
        status=command('headend','status')
        if 'llm='+args.expect in status:
            break
        time.sleep(1)
    assert 'llm='+args.expect in status, status
    ui=command('ui','status')
    assert 'scene=idle' in ui and 'controls=0' in ui, ui
    output=ROOT/'artifacts/llm-ring'
    output.mkdir(parents=True,exist_ok=True)
    name=args.name or args.expect
    image_path=output/(name+'.png')
    subprocess.run([sys.executable,str(ROOT/'tools/snapshot.py'),str(image_path),'--port',args.port],check=True)
    frame=Image.open(image_path).convert('RGB')
    def matches(pixel):
        red,green,blue=pixel
        return green>180 and (red>200 and blue<130 if args.expect=='pi' else red<130 and blue>100)
    # All four edges must be colored, and most of the full circumference must
    # be present. This checks the idle display rather than relying on telemetry.
    points=[(180,5),(180,355),(5,180),(355,180)]
    assert all(matches(frame.getpixel(point)) for point in points), 'Edge color differs from provider'
    count=sum(matches(frame.getpixel((x,y))) for y in range(360) for x in range(360)
        if 169**2 <= (x-180)**2+(y-180)**2 <= 176**2)
    assert count>7000, f'Incomplete thick ring: {count} pixels'
    report=dict(provider=args.expect,idle=True,ring_pixels=count,edge_rgb=[frame.getpixel(p) for p in points],
                device=status.strip(),ui=ui.strip())
    (output/(name+'.json')).write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
