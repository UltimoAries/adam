"""Local, versioned voice profiles with atomic package import and bounded inference."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import uuid
import zipfile
from .voice_package import ALLOWED_FILES,MAX_MODEL,MAX_PACKAGE,PROBE_TEXT,validate_files

ROOT=Path(__file__).resolve().parent
DEFAULT_KEY='basic-english@1.0.0'
DEFAULT_DIRECTORY=ROOT/'models/voices/basic-english/1.0.0'


class PiperRuntime:
    """Keep one validated model warm in a bounded, restartable subprocess."""
    def __init__(self,directory):
        from concurrent.futures import ThreadPoolExecutor
        import atexit
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.process=None
        self.io=ThreadPoolExecutor(max_workers=1,thread_name_prefix='piper-io')
        self.lock=threading.RLock()
        self.closed=False
        atexit.register(self.close)

    def _stop(self):
        process=self.process;self.process=None
        if process:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdin.close();process.stdout.close()

    def close(self):
        with self.lock:
            if self.closed:return
            self.closed=True
            self._stop()
            self.io.shutdown(wait=True,cancel_futures=True)

    def _exchange(self,options):
        process=self.process
        process.stdin.write((json.dumps(options)+'\n').encode());process.stdin.flush()
        return process.stdout.readline(65536)

    def synthesize(self,directory,text):
        from concurrent.futures import TimeoutError
        with self.lock, tempfile.TemporaryDirectory(dir=self.directory,prefix='speech-') as work:
            if self.closed:raise ValueError('Voice worker is stopped')
            work=Path(work)
            assert work.resolve().is_relative_to(self.directory.resolve())
            output=work/'speech.wav'
            options=dict(directory=str(Path(directory).resolve()),text=text,output=str(output))
            if self.process is None or self.process.poll() is not None:
                self._stop()
                flags={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
                self.process=subprocess.Popen([sys.executable,'-m','headend.voice_worker'],cwd=ROOT.parent,
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,**flags)
            try:
                raw=self.io.submit(self._exchange,options).result(timeout=35)
                result=json.loads(raw.decode())
                if not result.get('ok'):
                    raise ValueError('Voice validation/generation failed: '+str(result.get('error','unknown model error'))[:400])
                if not output.is_file() or output.stat().st_size>480044:
                    raise ValueError('Voice output exceeds the 15-second limit')
                return output.read_bytes(),result
            except TimeoutError as exc:
                self._stop()
                raise ValueError('Voice generation exceeded its 35-second limit') from exc
            except (OSError,ValueError,UnicodeError) as exc:
                self._stop()
                raise ValueError('Voice worker failed: '+str(exc)[:400]) from exc


class VoiceRegistry:
    def __init__(self,directory,builtin=DEFAULT_DIRECTORY):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.packages=self.directory/'packages';self.packages.mkdir(exist_ok=True)
        self.uploads=self.directory/'uploads';self.uploads.mkdir(exist_ok=True)
        self.builtin=Path(builtin) if builtin else None
        self.lock=threading.RLock();self.records={}
        if self.builtin and self.builtin.is_dir():
            manifest=validate_files(self.builtin)
            self.records[manifest.key]=(manifest,self.builtin,True)
        for path in self.packages.iterdir():
            if path.is_dir() and not path.is_symlink():
                try:
                    manifest=validate_files(path)
                    if manifest.key not in self.records:self.records[manifest.key]=(manifest,path,False)
                except (ValueError,OSError):
                    continue

    def default(self):
        with self.lock:
            try:key=json.loads((self.directory/'active.json').read_bytes())['voice_id']
            except (ValueError,OSError,KeyError,TypeError):key=DEFAULT_KEY
            return key if key in self.records else DEFAULT_KEY

    def list(self):
        with self.lock:
            active=self.default()
            result=[]
            for key,(manifest,path,builtin) in sorted(self.records.items()):
                result.append(dict(voice_id=key,**manifest.model_dump(),builtin=builtin,ready=True,
                                   active=key==active,model_bytes=(path/'model.onnx').stat().st_size))
            if DEFAULT_KEY not in self.records:
                result.insert(0,dict(voice_id=DEFAULT_KEY,name='Basic English',description='Default Piper model has not been installed.',
                    engine='piper-onnx',language='en-US',version='1.0.0',builtin=True,ready=False,active=active==DEFAULT_KEY,model_bytes=0))
            return result

    def resolve(self,key=None):
        with self.lock:
            key=key or self.default()
            if key not in self.records:
                raise ValueError('Voice is unavailable. Install the default model or import a valid voice package.')
            return self.records[key]

    def activate(self,key):
        with self.lock:
            self.resolve(key)
            temporary=self.directory/(uuid.uuid4().hex+'.json')
            temporary.write_text(json.dumps(dict(voice_id=key))+'\n')
            os.replace(temporary,self.directory/'active.json')

    def import_package(self,archive,runtime):
        # VoiceService serializes imports/generation. Keep the registry lock
        # short so device polling and dashboard reads continue during the probe.
        with self.lock:
            if len(self.records)>=16:raise ValueError('Voice library is full (16 profiles)')
        used=sum(p.stat().st_size for p in self.packages.rglob('*') if p.is_file())
        with tempfile.TemporaryDirectory(dir=self.directory,prefix='import-') as stage:
            stage=Path(stage)
            assert stage.resolve().is_relative_to(self.directory.resolve())
            try:
                with zipfile.ZipFile(archive) as source:
                    entries=source.infolist();names=[e.filename for e in entries]
                    if len(entries)>len(ALLOWED_FILES) or len({n.casefold() for n in names})!=len(names):
                        raise ValueError('Voice ZIP has duplicate or excess entries')
                    total=sum(e.file_size for e in entries)
                    if total>MAX_PACKAGE or used+total>1024**3:raise ValueError('Voice package/library exceeds its storage bound')
                    for entry in entries:
                        mode=stat.S_IFMT(entry.external_attr>>16)
                        if entry.filename not in ALLOWED_FILES or entry.is_dir() or mode not in (0,stat.S_IFREG) or entry.flag_bits&1:
                            raise ValueError('Voice ZIP must contain regular, unencrypted files at its root; directories and scripts are not supported')
                        limit=MAX_MODEL if entry.filename=='model.onnx' else 65536
                        if not 0<entry.file_size<=limit:raise ValueError('A voice package file exceeds its size bound')
                        with source.open(entry) as inp,(stage/entry.filename).open('wb') as out:
                            copied=0
                            while chunk:=inp.read(1024*1024):
                                copied+=len(chunk)
                                if copied>limit:raise ValueError('Voice ZIP expands beyond its declared size')
                                out.write(chunk)
            except (zipfile.BadZipFile,RuntimeError,NotImplementedError) as exc:
                raise ValueError('Invalid or unsupported voice ZIP') from exc
            manifest=validate_files(stage)
            with self.lock:
                if manifest.key in self.records:raise ValueError('That voice ID/version is already installed; use a new version')
            wav,probe=runtime.synthesize(stage,PROBE_TEXT)
            with self.lock:
                target=self.packages/uuid.uuid4().hex
                os.replace(stage,target)
                self.records[manifest.key]=(manifest,target,False)
                return dict(voice_id=manifest.key,name=manifest.name,version=manifest.version,probe=probe)

    def remove(self,key):
        with self.lock:
            manifest,path,builtin=self.resolve(key)
            if builtin:raise ValueError('The included basic voice cannot be removed')
            if key==self.default():raise ValueError('Choose a different default voice before removing this one')
            resolved=path.resolve()
            if resolved==self.packages.resolve() or not resolved.is_relative_to(self.packages.resolve()):raise ValueError('Invalid installed voice location')
            shutil.rmtree(resolved)
            del self.records[key]


class VoiceService:
    def __init__(self,directory,builtin=DEFAULT_DIRECTORY,runtime=None):
        self.registry=VoiceRegistry(directory,builtin)
        self.runtime=runtime or PiperRuntime(Path(directory)/'work')
        self.lock=threading.Lock()

    def close(self):
        if hasattr(self.runtime,'close'):
            self.runtime.close()

    def generate(self,text,key=None):
        if not isinstance(text,str) or not 1<=len(text.strip())<=200:raise ValueError('Use 1–200 characters of speech')
        if not self.lock.acquire(blocking=False):raise ValueError('Voice generation/import is busy; try again shortly')
        try:
            manifest,path,builtin=self.registry.resolve(key)
            return self.runtime.synthesize(path,text.strip())
        finally:self.lock.release()

    def synthesize(self,text,voice_id=None):
        from .clips import parse_wav,mono_16k
        wav,details=self.generate(text,voice_id)
        rate,samples=parse_wav(wav)
        return mono_16k(rate,samples,1)[0]

    def import_package(self,path):
        if not self.lock.acquire(blocking=False):raise ValueError('Voice generation/import is busy; try again shortly')
        try:return self.registry.import_package(path,self.runtime)
        finally:self.lock.release()

    def remove(self,key):
        if not self.lock.acquire(blocking=False):raise ValueError('Wait for voice generation to finish before removing a voice')
        try:self.registry.remove(key)
        finally:self.lock.release()
