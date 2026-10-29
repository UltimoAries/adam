"""Install the pinned basic English Piper voice. No runtime downloads are needed."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import urllib.request
from headend.voice_package import VoiceManifest,digest,validate_files

ROOT=Path(__file__).resolve().parent.parent
REPOSITORY='rhasspy/piper-voices'
REVISION='5041a7a49ae5b3a0f30e6837769eb03fdbb231bd'
PREFIX='en/en_US/kristin/medium/'
MODEL_SHA='5849957f929cbf720c258f8458692d6103fff2f0e3d3b19c8259474bb06a18d4'
def license_notice(card):
    return (ROOT/'headend/voice-licenses/basic-english.txt').read_text(encoding='utf-8')+'\n\n'+card

destination=ROOT/'headend/models/voices/basic-english/1.0.0'
destination.parent.mkdir(parents=True,exist_ok=True)
if destination.exists():
    manifest=validate_files(destination)
    if manifest.model.sha256!=MODEL_SHA:raise ValueError('Installed default differs from the pinned model')
    (destination/'LICENSE.txt').write_text(license_notice((destination/'MODEL_CARD.md').read_text(encoding='utf-8')),encoding='utf-8')
    print('Pinned basic English voice already installed.');raise SystemExit(0)
with tempfile.TemporaryDirectory(dir=destination.parent,prefix='download-') as temp:
    stage=Path(temp)
    for source,name in [('en_US-kristin-medium.onnx','model.onnx'),('en_US-kristin-medium.onnx.json','model.onnx.json'),('MODEL_CARD','MODEL_CARD.md')]:
        url=f'https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{PREFIX}{source}'
        with urllib.request.urlopen(url,timeout=90) as response,(stage/name).open('wb') as output:
            shutil.copyfileobj(response,output,1024*1024)
    if digest(stage/'model.onnx')!=MODEL_SHA:raise ValueError('Default model checksum mismatch')
    for name,expected in [('model.onnx.json','e94be84ab0dc3532b4b2b48cbb1f5d4a8746bedd'),('MODEL_CARD.md','40000a8e64cf5deb284a999cda92af78cdae783a')]:
        data=(stage/name).read_bytes()
        if hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()!=expected:raise ValueError('Pinned source checksum mismatch: '+name)
    license_text=license_notice((stage/'MODEL_CARD.md').read_text(encoding='utf-8'))
    (stage/'LICENSE.txt').write_text(license_text,encoding='utf-8')
    manifest=VoiceManifest.model_validate(dict(schema_version=1,id='basic-english',version='1.0.0',name='Basic English · Kristin',
        description='Included local English starter voice. A neutral voice for paging and initial tests.',author='Bryce Beattie / Piper voice contributors',
        engine='piper-onnx',language='en-US',model=dict(file='model.onnx',sha256=MODEL_SHA),
        config=dict(file='model.onnx.json',sha256=digest(stage/'model.onnx.json')),
        license=dict(name='MIT repository / public-domain source recordings',file='LICENSE.txt',
            attribution='Kristin voice by Bryce Beattie, distributed through rhasspy/piper-voices; LibriVox source recordings.',
            source_url=f'https://huggingface.co/{REPOSITORY}/tree/{REVISION}/{PREFIX}')))
    (stage/'voice.json').write_text(manifest.model_dump_json(indent=2)+'\n',encoding='utf-8')
    validate_files(stage);os.replace(stage,destination)
record=dict(repository=REPOSITORY,revision=REVISION,path=PREFIX,sha256=MODEL_SHA,runtime='piper-tts==1.8.0',profile='basic-english@1.0.0')
(ROOT/'headend/voice-models.json').write_text(json.dumps(record,indent=2)+'\n')
print('Installed pinned basic English model:',destination)
