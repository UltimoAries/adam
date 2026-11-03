import tempfile
import unittest
from unittest.mock import Mock

from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from .intercom import Intercom, intent
from .state import State
from .server import create_app
from .test_server import FakeDevice, FakeSTT
from .test_fleet import FakeSpeech


class IntercomTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = State(self.temp.name)
        self.now = 100.
        self.service = Intercom(self.state, Mock(return_value={'status':'queued'}), lambda:self.now)
        self.a = self.state.add_unit('Kitchen')
        self.b = self.state.add_unit('Office ADAM')
        for u in (self.a,self.b):
            self.state.heartbeat(u['id'],'v31-185','test')
            self.service.heartbeat(u['id'],True,False)

    def tearDown(self):
        self.temp.cleanup()

    def start(self):
        self.service.configure(self.b['id'],True)
        result = self.service.start(self.a['id'],self.b['id'])
        return self.service.calls[result['id']]

    def test_exact_voice_names_and_no_incidental_calls(self):
        self.assertEqual(intent('Hey Adam, drop in on Office ADAM.'),'office adam')
        self.assertEqual(intent('Call Kitchen please'),'kitchen')
        for text in ('What is a phone call?', 'Turn on the office light', 'Call', 'Hang up'):
            self.assertIsNone(intent(text))
        entry = {'unit_id':self.a['id'],'id':'voice-1'}
        self.service.configure(self.b['id'],True)
        self.assertTrue(self.service.submit(entry,{'text':'Hey Adam, call Office ADAM'}))
        self.assertEqual(entry['reply']['status'],'done')
        self.service.end(entry['reply']['call_id'])
        self.service.submit(entry,{'text':'Call Office ADAM'})
        self.assertEqual(len(self.service.calls),1)
        # Replayed transcription is also ignored after a head-end restart.
        restarted = Intercom(self.state,Mock(),lambda:self.now)
        self.assertTrue(restarted.submit(entry,{'text':'Call Office ADAM'}))
        self.assertEqual(len(restarted.calls),0)

    def test_permissions_busy_unsupported_and_timeout(self):
        with self.assertRaises(HTTPException):
            self.service.start(self.a['id'],self.b['id'])
        self.service.configure(self.b['id'],True)
        self.service.heartbeat(self.b['id'],False,False)
        with self.assertRaises(HTTPException):
            self.service.start(self.a['id'],self.b['id'])
        self.service.heartbeat(self.b['id'],True,True)
        with self.assertRaises(HTTPException):
            self.service.start(self.a['id'],self.b['id'])
        self.service.heartbeat(self.b['id'],True,False)
        call = self.start()
        with self.assertRaises(HTTPException):
            self.service.start(self.a['id'],self.b['id'])
        self.now += 16
        self.assertIsNone(self.service.current(self.a['id']))
        self.assertEqual(call.reason,'Connection timed out')

    def test_full_duplex_bounded_audio_and_permission_revoke(self):
        call = self.start()
        call.active_at = self.now
        left,right = b'\x10\x20'*320,b'\x20\x30'*320
        for _ in range(30):
            self.now += .02
            self.service.receive(call,self.a['id'],left)
            self.service.receive(call,self.b['id'],right)
        self.assertEqual(len(call.queues[self.a['id']]),10)
        self.assertEqual(call.queues[self.a['id']][-1][1],right)
        self.assertEqual(call.queues[self.b['id']][-1][1],left)
        self.service.configure(self.b['id'],False)
        self.assertTrue(call.reason)
        self.assertEqual(call.queues,{})
        self.assertNotIn('pcm',str(self.service.public()))

    def test_invalid_frame_rate_limit_disconnect_and_max_duration(self):
        call = self.start()
        self.service.receive(call,self.a['id'],b'bad')
        self.assertEqual(call.reason,'Invalid audio frame')
        call = self.start()
        for _ in range(11):
            self.service.receive(call,self.a['id'],bytes(640))
        self.assertEqual(call.reason,'Audio arrived too quickly')
        call = self.start()
        call.active_at = self.now
        call.last_rx[self.a['id']] = self.now
        self.now += 4
        self.service.public()
        self.assertEqual(call.reason,'Speaker disconnected')
        self.now = 100
        call = self.start()
        call.active_at = self.now
        self.now += 601
        self.service.public()
        self.assertEqual(call.reason,'Ten-minute call limit reached')


class IntercomAPITests(unittest.TestCase):
    def test_auth_pair_scoping_streaming_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(tmp,provider_factory=FakeSTT,device=FakeDevice(),testing=True,auth_required=True,speech=FakeSpeech())
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/intercom').status_code,401)
                client.post('/api/auth/setup',json={'username':'admin','password':'test-only-password-123'})
                units = [client.post('/api/units',json={'name':n}).json() for n in ('Kitchen','Office','Hall')]
                a,b,c = units
                for u in units:
                    client.get(f"/api/units/{u['id']}/poll?intercom=1",headers={'Authorization':'Bearer '+u['token']})
                client.put('/api/intercom/units/'+b['id'],json={'allow_drop_in':True})
                result = client.post('/api/intercom/calls',json={'caller':a['id'],'callee':b['id']})
                self.assertEqual(result.status_code,201,result.text)
                cid = result.json()['id']
                path = lambda u:f"/api/units/{u['id']}/calls/{cid}/stream"
                for u,headers in ((a,{}),(c,{'Authorization':'Bearer '+c['token']}),(a,{'Authorization':'Bearer '+a['token'],'Origin':'https://untrusted.example'})):
                    with self.assertRaises(WebSocketDisconnect):
                        with client.websocket_connect(path(u),headers=headers):pass
                with client.websocket_connect(path(a),headers={'Authorization':'Bearer '+a['token']}) as wa:
                    self.assertEqual(wa.receive_json()['state'],'connecting')
                    with client.websocket_connect(path(b),headers={'Authorization':'Bearer '+b['token']}) as wb:
                        self.assertEqual(wb.receive_json()['state'],'active')
                        self.assertEqual(wa.receive_json()['state'],'active')
                        wa.send_bytes(b'\x10\x20'*320)
                        wb.send_bytes(b'\x20\x30'*320)
                        self.assertEqual(wb.receive_bytes(),b'\x10\x20'*320)
                        self.assertEqual(wa.receive_bytes(),b'\x20\x30'*320)
                        self.assertEqual(client.post(f'/api/intercom/calls/{cid}/end').status_code,200)
                        self.assertEqual(wa.receive_json()['state'],'ended')
                self.assertIsNone(app.state.fleet.intercom.current(a['id']))
                self.assertEqual(client.get('/api/clips').json(),[])
                for message,reason in (({'type':'end'},'Call ended'),
                                       ({'type':'end','reason':'audio_overload'},'Audio overload detected; call stopped')):
                    cid = client.post('/api/intercom/calls',json={'caller':a['id'],'callee':b['id']}).json()['id']
                    with client.websocket_connect(path(a),headers={'Authorization':'Bearer '+a['token']}) as wa:
                        self.assertEqual(wa.receive_json()['state'],'connecting')
                        wa.send_json(message)
                        try:
                            # The sender may flush a final status before close.
                            self.assertEqual(wa.receive_json()['state'],'ended')
                        except WebSocketDisconnect:
                            pass
                        else:
                            with self.assertRaises(WebSocketDisconnect):
                                wa.receive_json()
                    self.assertEqual(app.state.fleet.intercom.calls[cid].reason,reason)
                self.assertEqual(client.get('/api/clips').json(),[])


if __name__ == '__main__':
    unittest.main()
