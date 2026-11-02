import json
import base64
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from .cameras import CameraOptions, CameraService, GestureGate, Observation, valid_stream
from .server import create_app
from .state import State


class CameraTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 100.0
        self.busy = False
        self.ha = Mock()
        self.ha.inventory_view.return_value = {'devices':[dict(entity_id='light.test',imported=True,enabled=True,present=True)]}
        self.ha.device_action.return_value = {'status':'confirmed'}
        self.service = CameraService(State(self.temp.name), self.ha, lambda:self.busy, lambda:self.now)
        self.options = CameraOptions(name='Test',rtsp_url='rtsp://tester:private-password@192.168.1.50/stream2',enabled=True,
            rules=[dict(gesture='Open_Palm',action_enabled=True,entity_id='light.test',action='on')])
        self.id = self.service.save(self.options)['id']
        self.revision = self.service.get(self.id)['revision']
        self.sequence = 0

    def observe(self, gesture='None', advance=.5, **kwargs):
        self.now += advance
        self.sequence += 1
        return self.service.observe(self.id, Observation(revision=self.revision,sequence=self.sequence,
            status='streaming',gesture=gesture,confidence=.95,**kwargs))

    def arm(self):
        for _ in range(3):
            self.observe()

    def hold(self):
        for _ in range(4):
            self.observe('Open_Palm')

    def test_credentials_private_and_persisted(self):
        public = json.dumps(self.service.public())
        self.assertNotIn('private-password',public)
        self.assertNotIn('tester',public)
        self.assertNotIn('rtsp_url',public)
        self.service.save(self.options.model_copy(update={'rtsp_url':CameraOptions(name='x').rtsp_url}), self.id)
        restored = CameraService(self.service.state,self.ha)
        self.assertIn('private-password',restored.get(self.id)['rtsp_url'])
        self.assertEqual(self.service.worker_token,restored.worker_token)

    def test_lan_protocol_validation(self):
        for url in ['file:///etc/passwd','http://192.168.1.50','rtsp://127.0.0.1/a','rtsp://example.com/a',
                    'rtsp://169.254.169.254/a','rtsp://8.8.8.8/a','rtsp://192.168.1.50:99999/a']:
            with self.assertRaises(ValueError): valid_stream(url)

    def test_only_one_active_camera(self):
        with self.assertRaises(ValueError): self.service.save(self.options)

    def test_hold_rearm_and_no_repeat(self):
        self.hold()  # Existing hand on startup must not fire.
        self.ha.device_action.assert_not_called()
        self.arm();self.hold()
        self.assertEqual(self.ha.device_action.call_count,1)
        for _ in range(20): self.observe('Open_Palm')
        self.assertEqual(self.ha.device_action.call_count,1)
        self.arm();self.hold()
        self.assertEqual(self.ha.device_action.call_count,2)

    def test_stale_and_voice_pause_do_not_fire(self):
        self.arm();self.observe('Open_Palm')
        self.observe('Open_Palm',age_ms=2000)
        self.hold();self.ha.device_action.assert_not_called()
        self.arm();self.busy=True;self.hold();self.busy=False;self.hold()
        self.ha.device_action.assert_not_called()

    def test_duplicates_and_old_revision_rejected(self):
        self.arm();self.hold()
        result=self.service.observe(self.id,Observation(revision=self.revision,sequence=self.sequence,status='streaming'))
        self.assertFalse(result['accepted'])
        self.service.save(self.options,self.id)
        with self.assertRaises(HTTPException): self.observe()
        self.assertEqual(self.ha.device_action.call_count,1)

    def test_timeout_not_retried(self):
        self.ha.device_action.side_effect=TimeoutError()
        self.arm();self.hold();self.hold()
        self.assertEqual(self.ha.device_action.call_count,1)
        self.assertEqual(self.service.public()['events'][0]['outcome'],'failed')

    def test_detection_only_and_multiple_gestures(self):
        options=self.options.model_copy(update={'rules':CameraOptions(name='x',rules=[{'gesture':'Open_Palm'},{'gesture':'Thumb_Up'}]).rules})
        self.service.save(options,self.id);self.revision=self.service.get(self.id)['revision']
        self.arm();self.hold()
        for _ in range(4):self.observe('Thumb_Up')
        self.ha.device_action.assert_not_called()
        self.assertEqual({e['gesture'] for e in self.service.public()['events']},{'Open_Palm','Thumb_Up'})

    def test_invalid_action_rejected_before_save(self):
        self.ha._prevalidate_device_step.side_effect=ValueError('Unsupported action')
        with self.assertRaises(ValueError): self.service.save(self.options,self.id)
        self.assertEqual(self.service.get(self.id)['revision'],self.revision)

    def test_disconnected_or_low_confidence_resets_hold(self):
        self.arm();self.observe('Open_Palm');self.observe('Open_Palm',advance=3)
        self.hold();self.ha.device_action.assert_not_called()

    def enable_snapshots(self):
        self.service.save(self.options.model_copy(update={'snapshot_events':True}),self.id)
        self.revision=self.service.get(self.id)['revision']

    def image_event(self, encoded=None):
        self.arm()
        self.observe('Open_Palm')
        self.observe('Open_Palm')
        self.observe('Open_Palm',snapshot_jpeg=encoded,captured_unix=1234567890,width=1280,height=720,inference_ms=40)
        return self.service.public()['events'][0]

    def test_exact_event_frame_persists_with_metadata(self):
        self.enable_snapshots()
        stale=b'\xff\xd8old-preview\xff\xd9'
        trigger=b'\xff\xd8trigger-frame\xff\xd9'
        self.service.previews[self.id]=(self.now,stale)
        event=self.image_event(base64.b64encode(trigger).decode())
        self.assertTrue(event['has_snapshot'])
        self.assertEqual(self.service.snapshot(event['id']),trigger)
        self.assertEqual(event['metadata']['confidence'],.95)
        self.assertEqual(event['metadata']['rule']['entity_id'],'light.test')
        self.assertEqual(event['metadata']['captured_unix'],1234567890)
        self.assertNotIn('snapshot_jpeg',self.service.public()['cameras'][0]['runtime'])
        restored=CameraService(self.service.state,self.ha)
        self.assertEqual(restored.snapshot(event['id']),trigger)
        self.service.delete(self.id)
        with self.assertRaises(HTTPException):self.service.snapshot(event['id'])

    def test_opt_out_does_not_store_supplied_image(self):
        event=self.image_event(base64.b64encode(b'\xff\xd8x\xff\xd9').decode())
        self.assertFalse(event['has_snapshot'])
        self.assertEqual(event['snapshot_status'],'disabled')
        self.ha.device_action.assert_called_once()

    def test_invalid_or_missing_snapshot_does_not_block_action(self):
        self.enable_snapshots()
        event=self.image_event('not-base64!')
        self.assertEqual(event['snapshot_status'],'invalid')
        self.ha.device_action.assert_called_once()

    def test_low_disk_keeps_event_and_action(self):
        self.enable_snapshots()
        with patch('headend.cameras.shutil.disk_usage',return_value=Mock(free=0)):
            event=self.image_event(base64.b64encode(b'\xff\xd8x\xff\xd9').decode())
        self.assertEqual(event['snapshot_status'],'low_disk')
        self.ha.device_action.assert_called_once()

    def test_snapshot_write_failure_does_not_block_action(self):
        self.enable_snapshots()
        with patch('headend.cameras.shutil.disk_usage',side_effect=OSError('disk unavailable')):
            event=self.image_event(base64.b64encode(b'\xff\xd8x\xff\xd9').decode())
        self.assertEqual(event['snapshot_status'],'storage_error')
        self.ha.device_action.assert_called_once()

    def test_snapshot_expiry_preserves_event_metadata(self):
        self.enable_snapshots()
        event=self.image_event(base64.b64encode(b'\xff\xd8x\xff\xd9').decode())
        with self.service.state.db() as db:
            db.execute('UPDATE camera_events SET created=0 WHERE id=?',(event['id'],))
        with self.assertRaises(HTTPException):self.service.snapshot(event['id'])
        result=self.service.public()['events'][0]
        self.assertEqual(result['snapshot_status'],'expired')
        self.assertIsNotNone(result['metadata'])

    def test_count_retention_removes_old_images(self):
        self.enable_snapshots()
        event=self.image_event(base64.b64encode(b'\xff\xd8old\xff\xd9').decode())
        with self.service.state.db() as db:
            db.execute('UPDATE camera_events SET created=1 WHERE id=?',(event['id'],))
            for i in range(200):
                db.execute('INSERT INTO camera_events VALUES(?,?,?,?,?)',(f'fixture{i}',self.id,2+i,'Open_Palm','detected'))
        self.now+=5
        self.arm();self.hold()
        with self.service.state.db() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM camera_events').fetchone()[0],200)
            self.assertIsNone(db.execute('SELECT 1 FROM camera_event_debug WHERE event_id=?',(event['id'],)).fetchone())


class CameraAPITests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=create_app(data_directory=self.temp.name,testing=True,auth_required=True)
        self.client=TestClient(self.app)
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.state=self.app.state.auth
        self.state.setup('admin','camera-test-password')

    def login(self):
        response=self.client.post('/api/auth/login',json={'username':'admin','password':'camera-test-password'})
        self.assertEqual(response.status_code,200)

    def test_auth_worker_token_and_origin(self):
        self.assertEqual(self.client.get('/api/cameras').status_code,401)
        self.assertEqual(self.client.get('/api/camera-worker/config').status_code,401)
        headers={'Authorization':'Bearer '+self.app.state.cameras.worker_token}
        self.assertEqual(self.client.get('/api/camera-worker/config',headers=headers).status_code,200)
        self.login()
        self.assertEqual(self.client.post('/api/cameras',json={},headers={'Origin':'http://evil.example'}).status_code,403)

    def test_malformed_secrets_never_echo(self):
        self.login()
        for options in [dict(name='Test',rtsp_url='rtsp://user:never-echo@localhost/a'),
                        dict(name='Test',rtsp_url={'secret':'never-echo'}),
                        dict(name='Test',rtsp_url='rtsp://user:never-echo@192.168.1.50/a',fps=999)]:
            result=self.client.post('/api/cameras',json=options)
            self.assertEqual(result.status_code,400)
            self.assertNotIn('never-echo',result.text)

    def test_crud_preview_and_request_bounds(self):
        self.login()
        result=self.client.post('/api/cameras',json={'name':'Test','rtsp_url':'rtsp://u:never-echo@192.168.1.50/a'})
        self.assertEqual(result.status_code,201)
        camera_id=result.json()['id'];service=self.app.state.cameras
        self.assertNotIn('never-echo',self.client.get('/api/cameras').text)
        self.assertEqual(self.client.put('/api/cameras/'+camera_id,json={'name':'Renamed'}).status_code,200)
        self.assertEqual(self.client.post('/api/cameras',content=b'x'*9000).status_code,413)
        headers={'Authorization':'Bearer '+service.worker_token,'X-Camera-Revision':service.get(camera_id)['revision']}
        path='/api/camera-worker/'+camera_id+'/preview'
        self.assertEqual(self.client.put(path,content=b'not jpeg',headers=headers).status_code,400)
        self.assertEqual(self.client.put(path,content=b'\xff\xd8test\xff\xd9',headers=headers).status_code,200)
        self.assertEqual(self.client.get('/api/cameras/'+camera_id+'/preview').status_code,200)
        self.assertEqual(self.client.delete('/api/cameras/'+camera_id).status_code,200)
        self.assertEqual(self.client.get('/api/cameras/'+camera_id+'/preview').status_code,404)

    def test_snapshot_routes_require_login_and_delete_preserves_event(self):
        service=self.app.state.cameras
        camera_id=service.save(CameraOptions(name='Test',rtsp_url='rtsp://192.168.1.50/a'))['id']
        event_id='a'*32
        with self.state.db() as db:
            import time
            db.execute('INSERT INTO camera_events VALUES(?,?,?,?,?)',(event_id,camera_id,time.time(),'Open_Palm','detected'))
            db.execute('INSERT INTO camera_event_debug VALUES(?,?,?,?)',(event_id,'{}','saved',b'\xff\xd8image\xff\xd9'))
        path='/api/cameras/events/'+event_id
        self.assertEqual(self.client.get(path+'/snapshot').status_code,401)
        self.assertEqual(self.client.get(path+'/metadata').status_code,401)
        self.assertEqual(self.client.delete(path+'/snapshot').status_code,401)
        self.login()
        response=self.client.get(path+'/snapshot')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertEqual(self.client.get(path+'/metadata').json()['outcome'],'detected')
        self.assertEqual(self.client.delete(path+'/snapshot').status_code,200)
        self.assertEqual(self.client.get(path+'/snapshot').status_code,404)
        self.assertEqual(self.client.get(path+'/metadata').json()['snapshot_status'],'deleted')


if __name__=='__main__': unittest.main()
