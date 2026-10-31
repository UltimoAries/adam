"""Package current head-end source (including local edits); runtime data is exported separately."""
import argparse
import fnmatch
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

ROOT=Path(__file__).resolve().parents[1]
SCOPE=['.dockerignore','LICENSE','THIRD_PARTY_NOTICES.md','headend','deploy/pi5','deploy/model-host','docs/VOICE_PACKAGES.md','docs/CONVERSATION.md','docs/SPACE_PANEL.md','docs/HEADEND_NETWORK.md',
       'tools/fetch_stt_model.py','tools/fetch_voice_model.py',
       'tools/migrate_headend.py','tools/package_voice.py',
       'tools/check_pi_host.py','tools/check_pi_runtime.py','tools/check_live_speech.py',
       'tools/check_conversation_runtime.py','tools/check_speech_routing.py',
       'tools/check_llm_ring.py','tools/control.py','tools/snapshot.py',
       'tools/check_weather_panel.py','tools/check_space_panel.py']
EXCLUDED_DIRS={'data','models','recordings','.venv','.browser-test','__pycache__'}
EXCLUDED_NAMES={'.env','ollama.json','speech.json','model-list.json'}
EXCLUDED_GLOBS=('test_*.py','*.env','config.local.*','*_ed25519*','*.pub','*known_hosts*','*.zip','*.tar.gz','*.tgz')
EXCLUDED_SUFFIXES={'.log','.pid','.pyc','.sqlite3','.db','.key','.pem','.wav'}


def is_excluded(relative: Path) -> bool:
    lower_name=relative.name.lower()
    return (
        any(part in EXCLUDED_DIRS for part in relative.parts)
        or lower_name in EXCLUDED_NAMES
        or any(fnmatch.fnmatch(lower_name,pattern) for pattern in EXCLUDED_GLOBS)
        or relative.suffix.lower() in EXCLUDED_SUFFIXES
        or any(s.lower().startswith('.sqlite') for s in relative.suffixes)
    )


def package(output):
    output=Path(output).resolve()
    result=subprocess.run(['git','-c','safe.directory='+ROOT.as_posix(),'ls-files','-z',
                           '--cached','--others','--exclude-standard','--',*SCOPE],
                          cwd=ROOT,check=True,capture_output=True)
    names=sorted(set(n.decode('utf-8') for n in result.stdout.split(b'\0') if n))
    manifest={}
    def metadata(info):
        info.uid=info.gid=0;info.uname=info.gname='';info.mode=0o644
        return info
    with output.open('xb') as target,tarfile.open(fileobj=target,mode='w:gz') as archive:
        for name in names:
            relative=Path(name)
            if is_excluded(relative):
                continue
            source=ROOT/relative
            if source.is_symlink() or not source.resolve().is_relative_to(ROOT):
                raise ValueError('Source package does not follow links outside the project')
            if not source.is_file():
                continue
            raw=source.read_bytes()
            info=metadata(archive.gettarinfo(str(source),arcname=relative.as_posix()))
            info.size=len(raw)
            archive.addfile(info,io.BytesIO(raw))
            manifest[relative.as_posix()]=hashlib.sha256(raw).hexdigest()
        raw=json.dumps({'format':1,'files':manifest},indent=2).encode('utf-8')
        info=metadata(tarfile.TarInfo('source-manifest.json'));info.size=len(raw)
        archive.addfile(info,io.BytesIO(raw))
    return dict(files=len(manifest),bytes=output.stat().st_size,
                sha256=hashlib.sha256(output.read_bytes()).hexdigest(),path=str(output))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    print(json.dumps(package(parser.parse_args().output),indent=2))
