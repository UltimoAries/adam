"""Provision one revision of a public English STT model. No recordings are read."""
import json
import os
from pathlib import Path
os.environ['HF_HUB_DISABLE_XET'] = '1'
from huggingface_hub import HfApi, snapshot_download

repo = 'Systran/faster-whisper-base.en'
manifest = Path('headend/stt-models.json')
if manifest.is_file():
    pinned = json.loads(manifest.read_text())
    if pinned['repository'] != repo:
        raise ValueError('The existing model manifest points to a different repository')
    revision = pinned['revision']
else:
    revision = HfApi().model_info(repo).sha
destination = Path('headend/models/stt/base.en')
snapshot_download(repo, revision=revision, local_dir=destination,
                  cache_dir='headend/models/cache',
                  allow_patterns=['model.bin', 'config.json', 'tokenizer.json',
                                  'vocabulary.*', 'preprocessor_config.json'])
metadata = dict(repository=repo, revision=revision, directory=str(destination),
                purpose='Local English transcription; CPU INT8')
manifest.write_text(json.dumps(metadata, indent=2)+'\n')
print(json.dumps(metadata, indent=2))
