import io
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from fastapi.testclient import TestClient
from .clips import wav_bytes
from .server import create_app
from .test_server import FakeDevice,FakeSTT
from .voice_package import MAX_PACKAGE,PROBE_TEXT,digest,template
from .voices import VoiceRegistry,VoiceService,DEFAULT_KEY


class FixtureRuntime:
    """Synthetic audio proves transport and registry behavior, not speech quality."""
    def __init__(self):self.fail=False;self.calls=[]
    def synthesize(self,directory,text):
        self.calls.append((Path(directory),text))
        if self.fail:raise ValueError('Fixture model could not synthesize')
        manifest=json.loads((Path(directory)/'voice.json').read_bytes())
        wave=wav_bytes(16000,(1000*np.sin(np.arange(3200)*2*np.pi*440/16000)).astype('<i2'))
        return wave,dict(voice=manifest['id']+'@'+manifest['version'],seconds=.2,generation_ms=1)


def fixture(directory,identifier='my-character',version='1.0.0'):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    # Deliberately not a real model. Only FixtureRuntime is used with these bytes.
    (directory/'model.onnx').write_bytes(b'fixture-model-not-for-inference')
    config=dict(audio=dict(sample_rate=22050),language=dict(code='en_US'),espeak=dict(voice='en-us'),
        num_symbols=256,num_speakers=1,phoneme_type='espeak',phoneme_id_map={'_':[0],'^':[1],'$':[2],'a':[3]})
    (directory/'model.onnx.json').write_text(json.dumps(config),encoding='utf-8')
    (directory/'LICENSE.txt').write_text('Test fixture only. No real voice or model.',encoding='utf-8')
    manifest=template();manifest.update(id=identifier,version=version,name='Fixture voice · English',author='Test fixture')
    manifest['model']['sha256']=digest(directory/'model.onnx');manifest['config']['sha256']=digest(directory/'model.onnx.json')
    manifest['license'].update(name='Fixture',attribution='Synthetic test data')
    (directory/'voice.json').write_text(json.dumps(manifest),encoding='utf-8')
    return directory


def archive(directory,extra=None):
    result=io.BytesIO()
    with zipfile.ZipFile(result,'w') as target:
        for path in Path(directory).iterdir():target.write(path,path.name)
        if extra:
            for name,contents in extra:target.writestr(name,contents)
    return result.getvalue()


class VoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.builtin=fixture(self.root/'builtin','basic-english')
        self.runtime=FixtureRuntime();self.service=VoiceService(self.root/'library',self.builtin,self.runtime)
        self.package=fixture(self.root/'source')
    def tearDown(self):self.temp.cleanup()
    def import_bytes(self,raw=None):
        path=self.root/'upload.zip';path.write_bytes(raw or archive(self.package))
        return self.service.import_package(path)

    def test_valid_import_does_not_activate_and_default_persists(self):
        result=self.import_bytes();self.assertEqual(result['voice_id'],'my-character@1.0.0')
        self.assertEqual(self.service.registry.default(),DEFAULT_KEY)
        self.service.registry.activate(result['voice_id'])
        restored=VoiceRegistry(self.root/'library',self.builtin)
        self.assertEqual(restored.default(),result['voice_id'])
        self.assertEqual(len(restored.list()),2)
        self.assertEqual(self.runtime.calls[0][1],PROBE_TEXT)

    def test_registry_stays_responsive_during_probe_and_generation_is_bounded(self):
        entered=threading.Event();release=threading.Event()
        synthesize=self.runtime.synthesize
        def slow_probe(directory,text):
            entered.set()
            if not release.wait(3):raise RuntimeError('Test did not release the probe')
            return synthesize(directory,text)
        self.runtime.synthesize=slow_probe
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending=pool.submit(self.import_bytes)
            try:
                self.assertTrue(entered.wait(1))
                records=pool.submit(self.service.registry.list).result(timeout=.5)
                self.assertEqual(len(records),1)
                with self.assertRaisesRegex(ValueError,'busy'):self.service.generate('Hello.')
            finally:release.set()
            self.assertEqual(pending.result(timeout=2)['voice_id'],'my-character@1.0.0')

    def test_packaging_tool_generates_hashes_and_refuses_overwrite(self):
        from tools.package_voice import build,parser
        output=self.root/'created.zip'
        args=parser().parse_args(['--model',str(self.package/'model.onnx'),'--id','created-voice',
            '--name','Created voice','--author','Fixture','--license-file',str(self.package/'LICENSE.txt'),
            '--license-name','Fixture','--attribution','Fixture only','--output',str(output)])
        result=build(args)
        self.assertEqual(result['voice_id'],'created-voice@1.0.0')
        self.assertEqual(self.import_bytes(output.read_bytes())['voice_id'],'created-voice@1.0.0')
        with self.assertRaisesRegex(ValueError,'already exists'):build(args)

    def test_onnx_rejects_external_data_and_custom_code_domains(self):
        import onnx
        from onnx import helper,TensorProto
        from .voice_worker import inspect_model
        tensor=helper.make_tensor('weight',TensorProto.FLOAT,[1],[1.0])
        tensor.data_location=TensorProto.EXTERNAL
        item=tensor.external_data.add();item.key='location';item.value='../outside'
        graph=helper.make_graph([], 'fixture', [], [], [tensor])
        model=helper.make_model(graph)
        path=self.root/'unsafe.onnx';path.write_bytes(model.SerializeToString())
        with self.assertRaisesRegex(ValueError,'External'):inspect_model(path)
        model=helper.make_model(helper.make_graph([helper.make_node('Run',[],[],domain='untrusted')],'fixture',[],[]))
        path.write_bytes(model.SerializeToString())
        with self.assertRaisesRegex(ValueError,'Custom'):inspect_model(path)

    def test_failed_probe_leaves_existing_voice_and_no_partial_install(self):
        self.runtime.fail=True
        with self.assertRaises(ValueError):self.import_bytes()
        self.assertEqual(self.service.registry.default(),DEFAULT_KEY)
        self.assertEqual(list(self.service.registry.packages.iterdir()),[])
        self.assertFalse(list((self.root/'library').glob('import-*')))

    def test_duplicate_version_and_active_removal_are_rejected(self):
        result=self.import_bytes()
        with self.assertRaises(ValueError):self.import_bytes()
        self.service.registry.activate(result['voice_id'])
        with self.assertRaises(ValueError):self.service.remove(result['voice_id'])
        with self.assertRaises(ValueError):self.service.remove(DEFAULT_KEY)
        self.service.registry.activate(DEFAULT_KEY);self.service.remove(result['voice_id'])
        self.assertEqual(len(self.service.registry.list()),1)

    def test_path_traversal_scripts_and_case_duplicates_are_rejected(self):
        for extra in [[('../outside.txt','bad')],[('installer.py','bad')],[('MODEL.ONNX','bad')]]:
            with self.subTest(extra=extra),self.assertRaises(ValueError):self.import_bytes(archive(self.package,extra))
        self.assertFalse((self.root/'outside.txt').exists())

    def test_symlinks_and_checksum_changes_are_rejected(self):
        data=io.BytesIO()
        with zipfile.ZipFile(data,'w') as target:
            link=zipfile.ZipInfo('model.onnx');link.create_system=3;link.external_attr=(stat.S_IFLNK|0o777)<<16
            target.writestr(link,'../elsewhere')
        with self.assertRaises(ValueError):self.import_bytes(data.getvalue())
        (self.package/'model.onnx').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError,'SHA-256'):self.import_bytes()

    def test_bad_family_and_configuration_never_reach_inference(self):
        manifest=json.loads((self.package/'voice.json').read_bytes());manifest['engine']='qwen'
        (self.package/'voice.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):self.import_bytes()
        self.assertEqual(self.runtime.calls,[])
        fixture(self.package)
        config=json.loads((self.package/'model.onnx.json').read_bytes());config['phoneme_id_map']['a']=[9999]
        (self.package/'model.onnx.json').write_text(json.dumps(config))
        manifest=json.loads((self.package/'voice.json').read_bytes());manifest['config']['sha256']=digest(self.package/'model.onnx.json')
        (self.package/'voice.json').write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError,'phoneme'):self.import_bytes()
        self.assertEqual(self.runtime.calls,[])

    def test_api_auth_import_preview_selection_and_paging(self):
        app=create_app(self.root/'clips',provider_factory=FakeSTT,device=FakeDevice(),testing=True,
                       auth_required=True,state_directory=self.root/'state',voices=self.service)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/voices').status_code,401)
            client.post('/api/auth/setup',json=dict(username='admin',password='fixture-password-only'))
            self.assertEqual(client.get('/voices').status_code,200)
            self.assertEqual(client.get('/api/voices/template').json()['engine'],'piper-onnx')
            self.assertEqual(client.get('/api/voices/guide').status_code,200)
            self.assertEqual(client.get('/api/voices/schema').json()['title'],'VoiceManifest')
            response=client.post('/api/voices/import',content=archive(self.package))
            self.assertEqual(response.status_code,201,response.text);key=response.json()['voice_id']
            preview=client.post('/api/voices/preview',json=dict(voice_id=key,text='Test this voice.'))
            self.assertEqual(preview.status_code,200,preview.text[:100]);self.assertEqual(preview.headers['x-voice-id'],key)
            self.assertTrue(preview.content.startswith(b'RIFF'))
            self.assertEqual(client.post('/api/voices/default',json=dict(voice_id=key)).status_code,200)
            unit=client.post('/api/units',json=dict(name='Kitchen')).json()
            response=client.post('/api/pages/text',json=dict(units=[unit['id']],text='Dinner is ready.',voice_id=key))
            self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(client.delete('/api/voices/'+key).status_code,400)
            self.assertEqual(client.post('/api/voices/import',content=b'bad').status_code,400)
            self.assertEqual(list(self.service.registry.uploads.iterdir()),[])
            self.assertEqual(client.post('/api/voices/import',content=b'bad',headers={'content-length':str(MAX_PACKAGE+1)}).status_code,413)


if __name__=='__main__':unittest.main()
