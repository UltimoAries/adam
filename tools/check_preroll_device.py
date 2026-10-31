"""Record one real 8-second session plus pre-roll and check its local WAV.

Uses the paired device/head-end. It plays the acknowledgement and records room
audio. No authentication is bypassed: local clip files are inspected directly.
"""
import argparse
import json
import os
from pathlib import Path
import time
import wave
import serial
from headend.clips import ClipStore
from headend.device import DeviceBridge

ROOT=Path(__file__).resolve().parent.parent
DEFAULT_PORT=os.environ.get('VOICE_USB_PORT','COM6' if os.name=='nt' else '/dev/ttyACM0')


def check():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',default=DEFAULT_PORT)
    args=parser.parse_args()
    store=ClipStore(ROOT/'headend/data/clips')
    before={clip['id'] for clip in store.list()}
    port=serial.Serial(port=None,baudrate=115200,timeout=.1,write_timeout=2)
    port.dtr=port.rts=False;port.port=args.port;port.open()
    history=[]
    def command(text):return DeviceBridge.command(port,text)
    def status():
        item=DeviceBridge.fields(command('headend status'));history.append(item);return item
    try:
        time.sleep(.15);port.reset_input_buffer()
        baseline=status()
        assert baseline['state']=='READY',baseline
        assert baseline['history_ms']=='2000',baseline
        assert command('headend selftest').startswith('OK ')
        command('headend listen');started=time.monotonic()
        immediate=status()
        assert immediate['listening']=='1' and int(immediate['captured_ms'])<1000,immediate
        deadline=started+20
        while time.monotonic()<deadline:
            current=status()
            if int(current['completed'])>int(baseline['completed']):break
            assert int(current['errors'])==int(baseline['errors']),current
            time.sleep(.2)
        else:raise RuntimeError('Device did not complete the pre-roll upload')
        assert current['preroll_ms']=='2000' and current['captured_ms']=='8000',current
        assert current['sent_bytes']=='320000' and current['queue_failed']=='0',current
        assert current['input_gaps']==baseline['input_gaps'],current
        new=[clip for clip in store.list() if clip['id'] not in before and clip['source']=='stream']
        assert len(new)==1,new
        clip=new[0]
        assert clip['seconds']==10 and clip['channels']==1,clip
        with wave.open(str(store.directory/(clip['id']+'.wav')),'rb') as audio:
            assert (audio.getframerate(),audio.getnchannels(),audio.getsampwidth(),audio.getnframes())==(16000,1,2,160000)
        result=dict(on_device_selftest=True,immediate_capture=True,seconds_before_trigger=2,seconds_after_trigger=8,
                    clip_id=clip['id'],clip_seconds=clip['seconds'],network_complete=True,input_gaps=0,
                    elapsed_seconds=round(time.monotonic()-started,2),status_history=history)
        (ROOT/'artifacts/preroll-device-check.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in result.items() if k!='status_history'},indent=2))
    finally:port.close()


if __name__=='__main__':check()
