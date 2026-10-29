"""Actual offline Piper package/import/preview/paging check, using isolated data."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import time
from fastapi.testclient import TestClient
from headend.clips import parse_wav,mono_16k
from headend.server import create_app
from headend.session import Utterance,Trigger
from headend.stt import FasterWhisperSTT
from headend.test_server import FakeDevice,FakeSTT
from headend.voices import VoiceService,VoiceRegistry,DEFAULT_DIRECTORY,DEFAULT_KEY
from tools.package_voice import build,parser

ROOT=Path(__file__).resolve().parent.parent


def check():
    qa=ROOT/'headend/.browser-test';qa.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=qa,prefix='voice-runtime-') as temporary:
        directory=Path(temporary)
        assert directory.resolve().is_relative_to(qa.resolve())
        service=VoiceService(directory/'library')
        output=directory/'runtime-check.zip'
        args=parser().parse_args(['--model',str(DEFAULT_DIRECTORY/'model.onnx'),
            '--id','runtime-check','--name','Runtime check of included Kristin weights',
            '--author','Test fixture; original voice by Bryce Beattie',
            '--license-file',str(DEFAULT_DIRECTORY/'LICENSE.txt'),
            '--license-name','MIT repository / public-domain source recordings',
            '--attribution','Kristin / Bryce Beattie; LibriVox; temporary runtime validation only',
            '--output',str(output)])
        package=build(args)
        app=create_app(directory/'clips',state_directory=directory/'state',provider_factory=FakeSTT,
                       device=FakeDevice(),testing=True,auth_required=True,voices=service)
        with TestClient(app) as client:
            client.post('/api/auth/setup',json=dict(username='admin',password='runtime-fixture-only'))
            started=time.monotonic()
            result=client.post('/api/voices/import',content=output.read_bytes())
            assert result.status_code==201,result.text
            imported=result.json();import_seconds=time.monotonic()-started
            assert service.registry.default()==DEFAULT_KEY
            key=imported['voice_id']
            assert key in VoiceRegistry(directory/'library').records
            phrase='Hello. Dinner is ready.'
            latencies=[]
            with ThreadPoolExecutor(max_workers=1) as pool:
                started=time.monotonic()
                request=pool.submit(client.post,'/api/voices/preview',json=dict(voice_id=key,text=phrase))
                while not request.done():
                    ping=time.monotonic()
                    assert client.get('/api/fleet').status_code==200
                    latencies.append(time.monotonic()-ping)
                    time.sleep(.2)
                preview=request.result()
                preview_seconds=time.monotonic()-started
            assert preview.status_code==200,preview.text[:200]
            rate,samples=parse_wav(preview.content);pcm=mono_16k(rate,samples,1)[0]
            assert rate==16000 and len(pcm)>3200 and len(pcm)<=480000
            (ROOT/'artifacts/recordings/basic-voice.wav').write_bytes(preview.content)
            unit=client.post('/api/units',json=dict(name='Runtime fixture')).json()
            page=client.post('/api/pages/text',json=dict(units=[unit['id']],text='This is a local voice test.',voice_id=key))
            assert page.status_code==201,page.text
            assert client.delete('/api/voices/'+key).status_code==200
            assert service.registry.default()==DEFAULT_KEY
        # Real local recognition is an additional intelligibility check. It is
        # not a substitute for the user's judgment of the character voice.
        recognizer=FasterWhisperSTT(ROOT/'headend/models/stt/base.en')
        transcript=recognizer.transcribe(Utterance('tts-validation',Trigger.TOUCH,None,pcm)).text
        assert 'dinner' in transcript.lower() and 'ready' in transcript.lower(),transcript
        report=dict(actual_piper=True,default_unchanged=True,model_bytes=(DEFAULT_DIRECTORY/'model.onnx').stat().st_size,
            package_bytes=package['bytes'],import_seconds=round(import_seconds,2),import_probe=imported['probe'],
            preview_seconds=round(preview_seconds,2),preview_audio_seconds=len(pcm)/32000,
            concurrent_fleet_reads=len(latencies),max_fleet_read_seconds=round(max(latencies),3),
            explicit_voice_paging=True,imported_voice_removed=True,local_stt_transcript=transcript)
        (ROOT/'artifacts/voice-runtime-check.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2))


if __name__=='__main__':check()
