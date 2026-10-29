"""Isolated browser fixture; never opens hardware or the real head-end data."""
import sys
from pathlib import Path
import threading
import time
import numpy as np
import uvicorn
from headend.server import create_app
from headend.clips import wav_bytes
from headend.test_server import FakeDevice,FakeSTT
from headend.test_fleet import FakeSpeech
from headend.test_voices import FixtureRuntime,fixture
from headend.voices import VoiceService
from headend.queries import OpenMeteo
from headend.test_queries import weather_fixture

directory=Path(sys.argv[1])
fixture(directory/'builtin','basic-english')
fixture(directory/'import-source','browser-character')
service=VoiceService(directory/'voice-library',directory/'builtin',FixtureRuntime())
app=create_app(sys.argv[1],provider_factory=FakeSTT,device=FakeDevice(),testing=True,
               auth_required=True,speech=FakeSpeech(),voices=service,lan_ip='192.168.5.10')
state=app.state.auth
def weather_fetch(url,params):
    if 'geocoding' in url:
        return {'results':[dict(name='Olympia',admin1='Washington',country_code='US',latitude=47.04,longitude=-122.9)]}
    return weather_fixture()
app.state.fleet.queries.provider=OpenMeteo(fetch=weather_fetch)
state.setup('admin','fixture-password-only')
unit=state.add_unit('Kitchen');state.add_unit('Bedroom')
def heartbeat():
    while True:
        state.heartbeat(unit['id'],'fixture','hey_jarvis-dev-v2');time.sleep(3)
threading.Thread(target=heartbeat,daemon=True).start()
clip=app.state.store.add(wav_bytes(16000,np.zeros(6400,dtype='<i2')),'Fixture clip.wav','stream')
app.state.fleet.sessions['fixture-session']=dict(id='fixture-session',unit_id=unit['id'],trigger='touch',
    model_id=None,status='done',text='The kitchen lights are on.',started=time.time(),received=12800,
    expected=12800,clip_id=clip['id'],inference_ms=500,preroll_ms=2000)
uvicorn.run(app,host='127.0.0.1',port=8766,access_log=False)
