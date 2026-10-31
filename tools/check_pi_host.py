"""Read-only Pi deployment preflight; run with the Pi's system Python before installing ADAM."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess


def read(path):
    try:
        return Path(path).read_text().replace('\0','').strip()
    except OSError:
        return None


def command(*args):
    try:
        result = subprocess.run(args,capture_output=True,text=True,timeout=12)
        return dict(ok=result.returncode==0,output=result.stdout.strip()[:12000],
                    error=result.stderr.strip()[:400])
    except (OSError,subprocess.TimeoutExpired) as exc:
        return dict(ok=False,error=type(exc).__name__)


def main():
    memory={}
    for line in (read('/proc/meminfo') or '').splitlines():
        key,_,value=line.partition(':')
        if key in ('MemTotal','MemAvailable','SwapTotal','SwapFree'):
            memory[key]=int(value.split()[0])*1024
    release={}
    for line in (read('/etc/os-release') or '').splitlines():
        key,separator,value=line.partition('=')
        if separator and key in ('ID','VERSION_ID','VERSION_CODENAME','PRETTY_NAME'):
            release[key]=value.strip('"')
    disk=shutil.disk_usage(Path.cwd())
    temperature=read('/sys/class/thermal/thermal_zone0/temp')
    result=dict(hostname=platform.node(),model=read('/proc/device-tree/model'),
                architecture=platform.machine(),os=release,kernel=platform.release(),
                uid=os.getuid(),gid=os.getgid(),cpu_count=os.cpu_count(),memory_bytes=memory,
                current_disk_bytes=dict(total=disk.total,free=disk.free),
                temperature_c=int(temperature)/1000 if temperature and temperature.isdigit() else None,
                addresses=command('hostname','-I'),
                disks=command('lsblk','--json','--bytes','--output','NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,MODEL,TRAN'),
                sudo_without_password=command('sudo','-n','true')['ok'],
                docker=command('docker','version','--format','{{json .Server}}'),
                compose=command('docker','compose','version'),
                power_status=command('vcgencmd','get_throttled'))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
