"""Versioned, data-only Piper voice package contract."""
import hashlib
import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

MAX_MODEL=150*1024*1024
MAX_PACKAGE=160*1024*1024
ALLOWED_FILES={'voice.json','model.onnx','model.onnx.json','LICENSE.txt','MODEL_CARD.md','README.md'}
REQUIRED_FILES={'voice.json','model.onnx','model.onnx.json','LICENSE.txt'}
PROBE_TEXT='Hello. This voice is ready.'


class StrictModel(BaseModel):
    model_config=ConfigDict(extra='forbid',allow_inf_nan=False)


class Asset(StrictModel):
    file: str
    sha256: str=Field(pattern=r'^[a-f0-9]{64}$')


class License(StrictModel):
    name: str=Field(min_length=1,max_length=100)
    file: Literal['LICENSE.txt']='LICENSE.txt'
    attribution: str=Field(min_length=1,max_length=500)
    source_url: str=Field(default='',max_length=500)


class Settings(StrictModel):
    speaker_id: int=Field(default=0,ge=0,le=63)
    length_scale: float=Field(default=1,ge=.5,le=2)
    noise_scale: float=Field(default=.667,ge=0,le=1)
    noise_w_scale: float=Field(default=.8,ge=0,le=1)


class VoiceManifest(StrictModel):
    schema_version: Literal[1]=1
    id: str=Field(pattern=r'^[a-z][a-z0-9_-]{2,47}$')
    version: str=Field(pattern=r'^\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9.-]{1,20})?$')
    name: str=Field(min_length=1,max_length=80)
    description: str=Field(default='',max_length=500)
    author: str=Field(min_length=1,max_length=120)
    engine: Literal['piper-onnx']='piper-onnx'
    language: Literal['en-US','en-GB','en-AU','en-CA']='en-US'
    model: Asset
    config: Asset
    license: License
    settings: Settings=Field(default_factory=Settings)

    @property
    def key(self):
        return self.id+'@'+self.version


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def load_manifest(path):
    if Path(path).stat().st_size>32768:raise ValueError('Voice manifest exceeds 32 KiB')
    try:manifest=VoiceManifest.model_validate_json(Path(path).read_bytes())
    except ValueError as exc:raise ValueError('Invalid voice.json: '+str(exc)[:500]) from exc
    if manifest.model.file!='model.onnx' or manifest.config.file!='model.onnx.json':
        raise ValueError('Use the canonical model.onnx and model.onnx.json filenames')
    if manifest.license.source_url and not manifest.license.source_url.startswith(('https://','http://')):
        raise ValueError('License source_url must be an HTTP(S) reference')
    return manifest


def validate_config(path,manifest):
    if Path(path).stat().st_size>65536:raise ValueError('Piper configuration exceeds 64 KiB')
    try:config=json.loads(Path(path).read_bytes())
    except (ValueError,UnicodeError) as exc:raise ValueError('Piper configuration is not valid JSON') from exc
    try:
        rate=config['audio']['sample_rate']
        symbols=config['num_symbols'];speakers=config['num_speakers']
        phonemes=config['phoneme_id_map'];espeak=config['espeak']['voice']
        language=config['language']['code'].replace('_','-')
    except (KeyError,TypeError,AttributeError) as exc:raise ValueError('Missing required Piper configuration fields') from exc
    if type(rate) is not int or rate not in (16000,22050,24000,44100,48000):
        raise ValueError('Unsupported Piper sample rate')
    if config.get('phoneme_type')!='espeak' or not isinstance(espeak,str) or not 1<=len(espeak)<=32 or not all(c.isalnum() or c in '-_' for c in espeak):
        raise ValueError('This package version supports Piper espeak phonemes only')
    if language.lower()!=manifest.language.lower():raise ValueError('Manifest language does not match the Piper configuration')
    if type(symbols) is not int or not 1<=symbols<=1024 or type(speakers) is not int or not 1<=speakers<=64:
        raise ValueError('Invalid Piper symbol/speaker count')
    if manifest.settings.speaker_id>=speakers:raise ValueError('Selected speaker_id is not in this model')
    if not isinstance(phonemes,dict) or not 1<=len(phonemes)<=1024:raise ValueError('Invalid phoneme map')
    for name,ids in phonemes.items():
        if not isinstance(name,str) or not 1<=len(name)<=16 or not isinstance(ids,list) or not 1<=len(ids)<=16 or any(type(i) is not int or not 0<=i<symbols for i in ids):
            raise ValueError('Invalid phoneme map entry')
    return config


def validate_files(directory):
    directory=Path(directory)
    if any(p.is_symlink() or not p.is_file() for p in directory.iterdir()):raise ValueError('Only regular files are permitted in a voice package')
    names={p.name for p in directory.iterdir()}
    if not REQUIRED_FILES<=names or not names<=ALLOWED_FILES:raise ValueError('Package must contain voice.json, model.onnx, model.onnx.json and LICENSE.txt at its root')
    manifest=load_manifest(directory/'voice.json')
    if not 1<=(directory/'model.onnx').stat().st_size<=MAX_MODEL:raise ValueError('Model must be at most 150 MiB')
    for asset in (manifest.model,manifest.config):
        if digest(directory/asset.file)!=asset.sha256:raise ValueError('SHA-256 mismatch: '+asset.file)
    for name in names-{'model.onnx','voice.json','model.onnx.json'}:
        if not 1<=(directory/name).stat().st_size<=65536:raise ValueError('License/document files must contain 1–64 KiB')
    validate_config(directory/'model.onnx.json',manifest)
    return manifest


def template():
    return dict(schema_version=1,id='my-character',version='1.0.0',name='My character',
        description='Replace with a short description of the voice.',author='Your name',engine='piper-onnx',language='en-US',
        model=dict(file='model.onnx',sha256='0'*64),config=dict(file='model.onnx.json',sha256='0'*64),
        license=dict(name='REPLACE WITH MODEL LICENSE',file='LICENSE.txt',attribution='REPLACE WITH REQUIRED ATTRIBUTION',source_url=''),
        settings=Settings().model_dump())
