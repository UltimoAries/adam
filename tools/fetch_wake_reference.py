"""Fetch pinned, Git-blob-verified wake runner, features and stock test model."""
import hashlib
import json
from pathlib import Path
import requests

sources=[
 ('0xD34D/micro_wake_word_standalone','dc7b5ebd020b98b26b9a0d7f25cb36619182dd7a','micro_wake_word'),
 ('0xD34D/ESPMicroSpeechFeatures','c2a077454c24d71fc84be377bb6e28725f05c333','micro_speech_features'),
 ('esphome/micro-wake-word-models','40ff33f57f8fc6ad71a75ef085abbf742495b225','micro_wake_models')]
manifest=[]
for repo,revision,name in sources:
    response=requests.get(f'https://api.github.com/repos/{repo}/git/trees/{revision}?recursive=1',timeout=30)
    response.raise_for_status()
    tree=response.json()['tree']
    files=[]
    for item in tree:
        path=item['path']
        if item['type']!='blob':continue
        if name=='micro_wake_models' and path not in ('LICENSE','models/v2/hey_jarvis.json','models/v2/hey_jarvis.tflite'):continue
        if name!='micro_wake_models' and path.startswith(('examples/','.git')):continue
        target=Path('vendor/wake')/name/path
        data=target.read_bytes() if target.is_file() else b''
        def blob_hash(content):return hashlib.sha1(b'blob '+str(len(content)).encode()+b'\0'+content).hexdigest()
        if blob_hash(data)!=item['sha']:
            result=requests.get(f'https://raw.githubusercontent.com/{repo}/{revision}/{path}',timeout=30)
            result.raise_for_status();data=result.content
            if blob_hash(data)!=item['sha']:raise RuntimeError('Source hash mismatch: '+path)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        files.append(dict(path=path,sha256=hashlib.sha256(data).hexdigest()))
    manifest.append(dict(repository=repo,revision=revision,directory='vendor/wake/'+name,files=files))
Path('vendor/wake/sources.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Verified pinned wake runner, feature extractor and Hey Jarvis test model.')
