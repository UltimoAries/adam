"""Provision one revision of a public English STT model. No recordings are read."""
import errno
import json
import os
from pathlib import Path

os.environ['HF_HUB_DISABLE_XET'] = '1'
from huggingface_hub import HfApi, snapshot_download

ROOT = Path(__file__).resolve().parent.parent
repo = 'Systran/faster-whisper-base.en'
manifest = ROOT / 'headend/stt-models.json'
if manifest.is_file():
    pinned = json.loads(manifest.read_text(encoding='utf-8'))
    if pinned['repository'] != repo:
        raise ValueError('The existing model manifest points to a different repository')
    revision = pinned['revision']
else:
    revision = HfApi().model_info(repo).sha

destination = ROOT / 'headend/models/stt/base.en'
cache_dir = ROOT / 'headend/models/cache'
destination.mkdir(parents=True, exist_ok=True)
cache_dir.mkdir(parents=True, exist_ok=True)
snapshot_download(
    repo,
    revision=revision,
    local_dir=destination,
    cache_dir=cache_dir,
    allow_patterns=[
        'model.bin',
        'config.json',
        'tokenizer.json',
        'vocabulary.*',
        'preprocessor_config.json',
    ],
)
metadata = dict(
    repository=repo,
    revision=revision,
    directory='headend/models/stt/base.en',
    purpose='Local English transcription; CPU INT8',
)
serialized = json.dumps(metadata, indent=2) + '\n'
if not manifest.is_file() or manifest.read_text(encoding='utf-8') != serialized:
    try:
        manifest.write_text(serialized, encoding='utf-8')
    except OSError as exc:
        if exc.errno != errno.EROFS or not manifest.is_file():
            raise
print(json.dumps(metadata, indent=2))
