"""Isolated browser fixture; never opens hardware or the real head-end data."""
import sys
from pathlib import Path
import threading
import time
import numpy as np
import httpx
import uvicorn
from headend.server import create_app
from headend.clips import wav_bytes
from headend.test_server import FakeDevice,FakeSTT
from headend.test_fleet import FakeSpeech
from headend.test_voices import FixtureRuntime,fixture
from headend.voices import VoiceService
from headend.queries import OpenMeteo
from headend.test_queries import weather_fixture
from headend.test_home_assistant import FakeHomeAssistant

directory=Path(sys.argv[1])
fixture(directory/'builtin','basic-english')
fixture(directory/'import-source','browser-character')
service=VoiceService(directory/'voice-library',directory/'builtin',FixtureRuntime())
app=create_app(sys.argv[1],provider_factory=FakeSTT,device=FakeDevice(),testing=True,
               auth_required=True,speech=FakeSpeech(),voices=service,lan_ip='192.168.1.10')
state=app.state.auth
def weather_fetch(url,params):
    if 'geocoding' in url:
        query=str((params or {}).get('name','')).lower()
        if 'seattle' in query:
            return {'results':[dict(name='Seattle',admin1='Washington',country_code='US',latitude=47.6062,longitude=-122.3321,timezone='America/Los_Angeles')]}
        return {'results':[dict(name='Portland',admin1='Oregon',country_code='US',latitude=45.5152,longitude=-122.6784,timezone='America/Los_Angeles')]}
    return weather_fixture()
app.state.fleet.queries.provider=OpenMeteo(fetch=weather_fetch)
class BrowserHomeAssistant(FakeHomeAssistant):
    def handle(self, request):
        response=super().handle(request)
        if request.url.path=='/api/states':
            rows=response.json()
            bedroom=next(row for row in rows if row['entity_id']=='switch.bedroom')
            bedroom['entity_id']='light.bedroom'
            bedroom['attributes'].update(supported_color_modes=['brightness'],color_mode=None,brightness=None)
            extra=[dict(entity_id='switch.living_room_'+suffix,state='on',attributes={'friendly_name':'Living Room '+name})
                   for suffix,name in [('led','LED'),('auto_off_enabled','Auto-off enabled'),('auto_update_enabled','Auto-update enabled')]]
            return httpx.Response(200,json=rows+extra)
        return response
app.state.fleet.home_assistant.client=BrowserHomeAssistant().client()
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
app.state.fleet.sessions['storage-session']=dict(id='storage-session',unit_id=unit['id'],trigger='wake',
    model_id='hey_adam-test',status='done',text='Is Living Room on?',started=time.time(),received=12800,
    expected=12800,clip_id=None,inference_ms=500,
    recording_warning='Recording not saved: the clip archive is full. Voice processing continues.')
original=wav_bytes(16000,(180*np.sin(np.arange(48000)*2*np.pi*330/16000)).astype('<i2'))
archived=app.state.fleet.archive.capture(original,dict(id='training-fixture',unit_id=unit['id'],device_name='Kitchen',
    trigger='wake',model_id='hey_adam-test',started=time.time()-60,preroll_ms=2000,
    collection_tags={'collection_session':'browser-household-01'}))
app.state.fleet.archive.complete(archived['id'],dict(status='done',text='Hey Adam, turn off the kitchen lights.'))
uvicorn.run(app,host='127.0.0.1',port=8766,access_log=False)
