import struct
import tempfile
import time
import unittest
import uuid
import numpy as np
from fastapi.testclient import TestClient
from .clips import wav_bytes
from .server import create_app
from .state import State
from .test_server import FakeDevice, FakeSTT


class FakeSpeech:
    def synthesize(self,text):
        return (1000*np.sin(np.arange(3200)*2*np.pi*700/16000)).astype('<i2').tobytes()


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name,provider_factory=FakeSTT,device=FakeDevice(),testing=True,
                              auth_required=True,speech=FakeSpeech(),lan_ip='192.168.1.50')
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.assertEqual(self.client.post('/api/auth/setup',json={'username':'admin','password':'test-only-password-123'}).status_code,200)
        self.unit = self.client.post('/api/units',json={'name':'Kitchen'}).json()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.temp.cleanup()

    def headers(self,unit=None):
        return {'Authorization':'Bearer '+(unit or self.unit)['token']}

    def poll(self,unit=None):
        unit = unit or self.unit
        return self.client.get(f"/api/units/{unit['id']}/poll",headers=self.headers(unit))

    def stream(self,packets=None,**headers):
        pcm = bytes(640)
        body = b''.join(struct.pack('<I',n)+pcm for n in range(5)) if packets is None else packets
        headers = {**self.headers(),'X-Session-ID':uuid.uuid4().hex,'X-Audio-Frames':'5',**headers}
        # Deliberately split packets across transport chunks.
        chunks = (body[i:i+333] for i in range(0,len(body),333))
        return self.client.post(f"/api/units/{self.unit['id']}/audio",headers=headers,content=chunks)

    def test_login_protection_logout_and_device_scope(self):
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/fleet').status_code,401)
        self.assertEqual(self.client.get('/api/clips').status_code,401)
        self.assertEqual(self.client.get('/api/fleet',headers=self.headers()).status_code,401)
        self.assertEqual(self.poll().status_code,200)
        self.assertEqual(self.client.get(f"/api/units/{self.unit['id']}/poll",headers={'Authorization':'Bearer wrong'}).status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'admin','password':'incorrect'}).status_code,401)
        self.assertEqual(self.client.post('/api/auth/login',json={'username':'admin','password':'test-only-password-123'}).status_code,200)
        self.assertEqual(self.client.post('/api/auth/setup',json={'username':'second','password':'test-only-password-123'}).status_code,400)

    def test_stream_orders_frames_preserves_wake_metadata_and_transcribes(self):
        result = self.stream(**{'X-Trigger':'wake','X-Wake-Model':'hey_adam-test-model'})
        self.assertEqual(result.status_code,202,result.text)
        job_id = result.json()['job_id']
        for _ in range(100):
            job = self.client.get('/api/jobs/'+job_id).json()
            if job['status']=='done':break
            time.sleep(.01)
        self.assertEqual(job['text'],'Fixture transcript')
        self.assertEqual(job['trigger'],'wake')
        self.assertEqual(job['model_id'],'hey_adam-test-model')
        clip = self.client.get('/api/clips/'+result.json()['clip_id']).json()
        self.assertEqual((clip['source'],clip['seconds'],clip['channels']),('stream',.1,1))
        entry = self.client.get('/api/fleet').json()['sessions'][0]
        self.assertEqual((entry['received'],entry['status']),(3200,'done'))

    def test_bad_streams_never_produce_partial_clips(self):
        self.assertEqual(self.stream(**{'X-Trigger':'wake'}).status_code,400)
        wrong = b''.join(struct.pack('<I',n)+bytes(640) for n in (0,1,3,4,5))
        self.assertEqual(self.stream(wrong).status_code,400)
        self.assertEqual(self.stream(bytes(643)).status_code,400)
        self.assertEqual(self.stream(**{'X-Audio-Frames':'751'}).status_code,400)
        self.assertEqual(self.client.get('/api/clips').json(),[])
        self.assertEqual(self.app.state.fleet.active,set())

    def test_preroll_is_preserved_in_recording_and_transcription_input(self):
        from .clips import parse_wav,mono_16k
        from .stt import Transcript
        observed=[]
        class ObservedSTT:
            def transcribe(self,utterance):
                observed.append(utterance.pcm)
                return Transcript(utterance.session_id,'Fixture transcript')
        self.app.state.jobs.factory=ObservedSTT
        packets=b''.join(struct.pack('<I',n)+np.full(320,n,dtype='<i2').tobytes() for n in range(500))
        result=self.stream(packets,**{'X-Audio-Frames':'500','X-Audio-Preroll-Ms':'2000',
                                     'X-Trigger':'wake','X-Wake-Model':'hey_jarvis-dev-v2'})
        self.assertEqual(result.status_code,202,result.text)
        entry=self.client.get('/api/fleet').json()['sessions'][0]
        self.assertEqual((entry['preroll_ms'],entry['expected'],entry['received']),(2000,320000,320000))
        raw=self.client.get('/api/clips/'+result.json()['clip_id']+'/audio').content
        rate,samples=parse_wav(raw)
        self.assertEqual(rate,16000)
        np.testing.assert_array_equal(samples.reshape(-1),np.repeat(np.arange(500,dtype='<i2'),320))
        # The transcriber consumes the original complete WAV, not a cropped
        # post-trigger copy. Verify it with a provider that observes the bytes.
        for _ in range(100):
            job=self.client.get('/api/jobs/'+result.json()['job_id']).json()
            if job['status']=='done':break
            time.sleep(.01)
        self.assertEqual(job['status'],'done')
        self.assertEqual(observed,[mono_16k(rate,samples,1,True)[0]])

    def test_invalid_preroll_and_old_client_compatibility(self):
        for value in ('no','-20','1','2020','100'):
            with self.subTest(preroll=value):
                self.assertEqual(self.stream(**{'X-Audio-Preroll-Ms':value}).status_code,400)
        self.assertEqual(self.client.get('/api/clips').json(),[])
        result=self.stream()
        self.assertEqual(result.status_code,202,result.text)
        entry=self.client.get('/api/fleet').json()['sessions'][0]
        self.assertEqual(entry['preroll_ms'],0)

    def test_variable_audio_end_record_preserves_prefix_and_quiet_tail(self):
        from .clips import parse_wav
        count=260
        body=b''.join(struct.pack('<I',n)+np.full(320,n,dtype='<i2').tobytes() for n in range(count))
        end=struct.pack('<II',0xffffffff,count)+bytes([1])+bytes(635)
        result=self.stream(body+end,**{'X-Audio-Frames':'750','X-Audio-Preroll-Ms':'2000',
            'X-Trigger':'wake','X-Wake-Model':'hey_jarvis-dev-v2','X-Audio-Mode':'silence-v1'})
        self.assertEqual(result.status_code,202,result.text)
        entry=self.app.state.fleet.sessions[result.json()['session_id']]
        self.assertEqual((entry['capture_ms'],entry['endpoint_reason'],entry['received']),(3200,'silence',166400))
        raw=self.client.get('/api/clips/'+result.json()['clip_id']+'/audio').content
        rate,samples=parse_wav(raw)
        np.testing.assert_array_equal(samples.reshape(-1),np.repeat(np.arange(count,dtype='<i2'),320))

    def test_variable_audio_rejects_missing_invalid_or_nonfinal_end(self):
        count=200;body=b''.join(struct.pack('<I',n)+bytes(640) for n in range(count))
        end=struct.pack('<II',0xffffffff,count)+bytes([1])+bytes(635)
        headers={'X-Audio-Frames':'750','X-Audio-Preroll-Ms':'2000',
            'X-Trigger':'wake','X-Wake-Model':'hey_jarvis-dev-v2','X-Audio-Mode':'silence-v1'}
        for raw in (body,body+end[:-1],body+end+end,body+end+bytes(644),
                    body+struct.pack('<II',0xffffffff,count-1)+end[8:],
                    body+end[:8]+bytes(636),body+end[:-1]+bytes([1])):
            with self.subTest(size=len(raw)):
                self.assertEqual(self.stream(raw,**headers).status_code,400)
        self.assertEqual(self.client.get('/api/clips').json(),[])
        self.assertEqual(self.app.state.fleet.active,set())

    def test_pages_are_targeted_and_require_playback_confirmation(self):
        other = self.client.post('/api/units',json={'name':'Bedroom'}).json()
        result = self.client.post('/api/pages/text',json={'units':[self.unit['id']],'text':'Dinner is ready.'})
        self.assertEqual(result.status_code,201,result.text)
        page = self.poll().json()
        self.assertEqual(page['type'],'page')
        self.assertEqual(self.poll(other).json()['type'],'idle')
        path = f"/api/units/{self.unit['id']}/pages/{page['id']}"
        self.assertEqual(self.client.get(path+'/audio',headers=self.headers(other)).status_code,401)
        self.assertEqual(len(self.client.get(path+'/audio',headers=self.headers()).content),6400)
        self.assertEqual(self.client.post(path+'/ack',headers=self.headers(),json={'status':'delivered'}).status_code,400)
        for status in ('playing','delivered','delivered'):
            self.assertEqual(self.client.post(path+'/ack',headers=self.headers(),json={'status':status}).status_code,200)
        self.assertEqual(self.client.get('/api/fleet').json()['pages'][0]['state'],'delivered')
        self.assertEqual(self.client.get(path+'/audio',headers=self.headers()).status_code,404)

    def test_voice_page_resampling_cancel_and_offline_expiry(self):
        raw = wav_bytes(48000,np.full(4800,1000,dtype='<i2'))
        result = self.client.post('/api/pages/voice',content=raw,headers={'X-Units':self.unit['id']})
        self.assertEqual(result.status_code,201,result.text)
        page_id = result.json()[0]['id']
        self.assertEqual(self.client.post('/api/pages/'+page_id+'/cancel').status_code,200)
        self.assertEqual(self.poll().json()['type'],'idle')
        result = self.client.post('/api/pages/text',json={'units':[self.unit['id']],'text':'Test'})
        now = time.time()
        self.app.state.auth.clock = lambda:now+301
        self.assertIsNone(self.app.state.auth.offer(self.unit['id']))
        self.assertEqual(self.app.state.auth.pages()[0]['state'],'expired')
        restored = State(self.app.state.auth.directory,clock=lambda:now+301)
        self.assertEqual(restored.pages()[0]['state'],'expired')
        self.assertTrue(restored.unit_auth(self.unit['id'],self.unit['token']))

    def test_login_survives_restart_and_logout_revokes_it(self):
        token=self.client.cookies.get('voice_session')
        restored=State(self.app.state.auth.directory)
        self.assertTrue(restored.authenticated(token))
        restored.logout(token)
        self.assertFalse(self.app.state.auth.authenticated(token))
        self.assertEqual(self.client.get('/api/fleet').status_code,401)

    def test_busy_and_outstanding_offer_preserve_queued_pages(self):
        for text in ('First message','Second message'):
            self.assertEqual(self.client.post('/api/pages/text',json={'units':[self.unit['id']],'text':text}).status_code,201)
        path=f"/api/units/{self.unit['id']}/poll"
        self.assertEqual(self.client.get(path+'?busy=true',headers=self.headers()).json()['type'],'idle')
        page=self.poll().json()
        self.assertEqual(page['type'],'page')
        self.assertEqual(self.poll().json()['type'],'idle')
        self.assertEqual(sorted(p['state'] for p in self.app.state.auth.pages()),['offered','queued'])
        self.app.state.auth.ack(self.unit['id'],page['id'],'failed')
        self.assertEqual(self.poll().json()['type'],'page')


if __name__=='__main__':
    unittest.main()
