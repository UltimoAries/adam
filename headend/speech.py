"""Offline Windows speech synthesis for short paging announcements."""
import json
from pathlib import Path
import subprocess
import tempfile
import threading
from .clips import parse_wav, mono_16k


class LocalSpeech:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def synthesize(self, text):
        if not self.lock.acquire(blocking=False):
            raise ValueError('Speech synthesis is busy; try again in a moment')
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory,suffix='.wav',delete=False) as temp:
                path = Path(temp.name)
            script = Path(__file__).resolve().parent / 'synthesize.ps1'
            result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-File',str(script)],
                input=json.dumps(dict(text=text,path=str(path))).encode('utf-8'),
                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                raise ValueError('Windows local speech synthesis failed; check the installed English voice')
            rate, samples = parse_wav(path.read_bytes())
            return mono_16k(rate,samples,1)[0]
        except subprocess.TimeoutExpired as exc:
            raise ValueError('Local speech synthesis timed out') from exc
        finally:
            if path:
                path.unlink(missing_ok=True)
            self.lock.release()
