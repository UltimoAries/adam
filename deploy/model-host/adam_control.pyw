"""Small Windows control window for ADAM's GPU services; standard-library only."""
import argparse
import ctypes
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk
import urllib.error
import urllib.request


DEFAULT_ROOT = Path(r'C:\ADAM_VOICE')
MODEL_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,149}$')
SYSTEM = ('You are ADAM, a home voice assistant. Answer in one short sentence, '
          'at most 30 words and 180 characters. Use plain speech without markup. '
          'You cannot control devices or access live weather, news or personal data. '
          'Never claim to have performed an action. Admit uncertainty.')


def ps_quote(value):
    return "'"+str(value).replace("'", "''")+"'"


class Backend:
    def __init__(self, root=DEFAULT_ROOT, request=None, run=None):
        self.root = Path(root)
        self.config_path = self.root/'config/ollama.json'
        self.cache_path = self.root/'config/model-list.json'
        self._request = request or self.request
        self._run = run or self.run_powershell

    def config(self):
        return json.loads(self.config_path.read_text(encoding='utf-8-sig'))

    def save_config(self, config):
        temporary = self.config_path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(config, indent=2)+'\n', encoding='utf-8')
        os.replace(temporary, self.config_path)

    def _base_url(self):
        listen = str(self.config()['listen'])
        return 'http://'+('127.0.0.1:'+listen[8:] if listen.startswith('0.0.0.0:') else listen)

    def request(self, path, data=None, timeout=5):
        request = urllib.request.Request(self._base_url()+path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(2*1024**2)
            return json.loads(raw) if raw.strip() else {}

    @staticmethod
    def run_powershell(script):
        import base64
        payload = base64.b64encode(("$ErrorActionPreference='Stop'\n"+script).encode('utf-16-le')).decode()
        powershell = Path(os.environ.get('WINDIR', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', payload],
            capture_output=True, text=True, timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            logging.error('Service operation failed: %s', result.stderr[-2000:])
            raise RuntimeError('Windows could not change the service. See logs\\gpu-control.log.')

    def cached_models(self):
        try:
            return json.loads(self.cache_path.read_text(encoding='utf-8-sig'))
        except (OSError, ValueError):
            return []

    def installed_models(self):
        try:
            models = self._request('/api/tags', timeout=2).get('models', [])
            self.cache_path.write_text(json.dumps(models, indent=2), encoding='utf-8')
        except (OSError, ValueError):
            models = self.cached_models()
        alias = self.config().get('model_alias', 'adam:latest')
        return sorted((m for m in models if m['name'] != alias), key=lambda m: m['name'])

    def status(self):
        config = self.config()
        result = dict(chat=False, ready=False, speech=False, speech_ready=False, models=self.cached_models(),
                      selected=config['preferred_model'])
        try:
            self._request('/api/version', timeout=1)
            result['chat'] = True
            result['models'] = self.installed_models()
            active = self._request('/api/ps', timeout=2).get('models', [])
            selected = next((m for m in result['models'] if m['name'] == config['preferred_model']), {})
            result['ready'] = bool(selected and any(m.get('digest') == selected.get('digest') for m in active))
        except (OSError, ValueError):
            result['models'] = self.installed_models()
        try:
            speech = json.loads((self.root/'config/speech.json').read_text(encoding='utf-8-sig'))
            key = next(line[8:].strip() for line in (self.root/'config/speaches.env').read_text().splitlines() if line.startswith('API_KEY='))
            headers = {'Authorization': 'Bearer '+key}
            request = urllib.request.Request(speech['url']+'/health', headers=headers)
            with urllib.request.urlopen(request, timeout=1) as response:
                result['speech'] = response.status == 200
            request = urllib.request.Request(speech['url']+'/api/ps', headers=headers)
            with urllib.request.urlopen(request, timeout=2) as response:
                result['speech_ready'] = speech['model'] in json.load(response).get('models', [])
        except (OSError, ValueError, StopIteration):
            pass
        result['load'] = self.system_load()
        return result

    @staticmethod
    def system_load():
        def capture(command):
            return subprocess.run(command, capture_output=True, text=True, timeout=4,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        readings = []
        try:
            result = capture(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                "(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter \"Name='_Total'\").PercentProcessorTime"])
            cpu = int(result.stdout.strip())
            readings.append(f'CPU {cpu}%')
        except (OSError, ValueError, subprocess.TimeoutExpired):
            readings.append('CPU unavailable')
        try:
            result = capture(['nvidia-smi.exe', '--query-gpu=utilization.gpu,memory.used,memory.total',
                              '--format=csv,noheader,nounits'])
            load, used, total = [int(part.strip()) for part in result.stdout.splitlines()[0].split(',')]
            readings.extend([f'GPU {load}%', f'GPU memory {used/1024:.1f} / {total/1024:.1f} GiB'])
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            readings.append('GPU unavailable')
        return '   |   '.join(readings)

    def start(self, kind):
        if kind not in ('chat', 'speech'):
            raise ValueError('Unknown service.')
        task = 'ADAM-Ollama' if kind == 'chat' else 'ADAM-Speech'
        self._run('Enable-ScheduledTask -TaskName '+ps_quote(task)+' | Out-Null\nStart-ScheduledTask -TaskName '+ps_quote(task))
        return 'Starting conversation model.' if kind == 'chat' else 'Starting speech recognizer.'

    def stop(self, kind):
        if kind not in ('chat', 'speech'):
            raise ValueError('Unknown service.')
        if kind == 'chat':
            binary_root = str(self.root/'bin/ollama').rstrip('\\/')+'\\'
            self._run("Disable-ScheduledTask -TaskName 'ADAM-Ollama' | Out-Null\n"
                "Stop-ScheduledTask -TaskName 'ADAM-Ollama'\n"
                "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
                "$_.ExecutablePath.StartsWith("+ps_quote(binary_root)+",[StringComparison]::OrdinalIgnoreCase) } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
            return 'Conversation stopped. ADAM will use its Pi fallback.'
        docker = r'C:\Program Files\Docker\Docker\resources\bin\docker.exe'
        self._run("Disable-ScheduledTask -TaskName 'ADAM-Speech' | Out-Null\n"
            "Stop-ScheduledTask -TaskName 'ADAM-Speech'\n& "+ps_quote(docker)+
            ' --config '+ps_quote(self.root/'config/docker-public')+
            " --host 'npipe:////./pipe/dockerDesktopLinuxEngine' stop adam-speech\n"
            "if ($LASTEXITCODE -ne 0) { throw 'Could not stop speech container' }")
        return 'Speech recognition stopped here. ADAM will recognize speech on the Pi.'

    def select_model(self, name):
        models = self.installed_models()
        if not MODEL_PATTERN.fullmatch(name) or name not in {m['name'] for m in models}:
            raise ValueError('Choose an installed conversation model.')
        config = self.config()
        try:
            self._request('/api/version', timeout=1)
        except OSError:
            config['preferred_model'] = name
            self.save_config(config)
            return 'Saved. This model will load when you start the conversation service.'
        alias = config.get('model_alias', 'adam:latest')
        self._request('/api/copy', {'source': name, 'destination': alias}, timeout=15)
        previous = config['preferred_model']
        config.update(preferred_model=name, model_alias=alias)
        try:
            self.save_config(config)
        except OSError:
            self._request('/api/copy', {'source': previous, 'destination': alias}, timeout=15)
            raise
        self._request('/api/chat', {'model': alias, 'stream': False, 'think': False, 'keep_alive': -1,
            'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': 'Say ready.'}],
            'options': {'num_ctx': 4096, 'num_predict': 8, 'temperature': 0.3}}, timeout=180)
        return 'ADAM is using '+name+'.'

    def download(self, name, progress):
        if not MODEL_PATTERN.fullmatch(name) or name == self.config().get('model_alias', 'adam:latest'):
            raise ValueError('Enter an Ollama model name, such as qwen3.5:4b.')
        request = urllib.request.Request(self._base_url()+'/api/pull',
            data=json.dumps({'model': name, 'stream': True}).encode(), headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=1800) as response:
            for line in response:
                data = json.loads(line)
                if data.get('error'):
                    raise RuntimeError(str(data['error'])[:200])
                total, completed = data.get('total', 0), data.get('completed', 0)
                percent = round(100*completed/total) if total else None
                progress(str(data.get('status', 'Downloading'))+(f' · {percent}%' if percent is not None else ''))
        self.installed_models()
        return 'Downloaded '+name+'. Choose it above and click Use selected model.'


class ControlWindow:
    def __init__(self, root, backend, poll=True):
        self.window, self.backend = root, backend
        self.events = queue.Queue()
        self.polling = False
        self.busy = False
        self.action_buttons = []
        self.closed = False
        root.title('ADAM GPU Control')
        root.geometry('800x570')
        root.minsize(740, 540)
        style = ttk.Style(root)
        if 'vista' in style.theme_names():
            style.theme_use('vista')
        style.configure('Title.TLabel', font=('Segoe UI', 17, 'bold'))
        main = ttk.Frame(root, padding=22)
        main.pack(fill='both', expand=True)
        ttk.Label(main, text='ADAM GPU Control', style='Title.TLabel').pack(anchor='w')
        ttk.Label(main, text='ADAM uses its local Pi fallbacks when these services are off.').pack(anchor='w', pady=(5, 16))
        self.load = tk.StringVar(value='CPU / GPU: checking…')
        ttk.Label(main, textvariable=self.load).pack(anchor='w', pady=(0, 14))
        self.chat_status = tk.StringVar(value='Checking…')
        self.speech_status = tk.StringVar(value='Checking…')
        for title, kind, status in [('Conversation model', 'chat', self.chat_status), ('Speech recognition', 'speech', self.speech_status)]:
            group = ttk.LabelFrame(main, text=title, padding=12)
            group.pack(fill='x', pady=(0, 10))
            ttk.Label(group, textvariable=status).pack(side='left', fill='x', expand=True)
            start = ttk.Button(group, text='Start', command=lambda k=kind: self.perform(lambda: backend.start(k)))
            start.pack(side='right', padx=(7, 0))
            stop = ttk.Button(group, text='Stop', command=lambda k=kind: self.perform(lambda: backend.stop(k)))
            stop.pack(side='right')
            self.action_buttons.extend([start, stop])
        group = ttk.LabelFrame(main, text='Conversation model selection', padding=12)
        group.pack(fill='x', pady=(0, 10))
        self.model = tk.StringVar()
        self.choices = ttk.Combobox(group, textvariable=self.model, state='readonly', width=48)
        self.choices.grid(row=0, column=0, sticky='ew', padx=(0, 10))
        select = ttk.Button(group, text='Use selected model', command=self.select_model)
        select.grid(row=0, column=1)
        self.action_buttons.append(select)
        group.columnconfigure(0, weight=1)
        self.model_detail = tk.StringVar(value='Choose an installed model; ADAM will follow the selection automatically.')
        ttk.Label(group, textvariable=self.model_detail).grid(row=1, column=0, columnspan=2, sticky='w', pady=(8, 0))
        self.choices.bind('<<ComboboxSelected>>', lambda event: self.show_detail())
        downloads = ttk.Frame(group)
        downloads.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(12, 0))
        self.download_name = tk.StringVar(value='qwen3.5:4b')
        ttk.Entry(downloads, textvariable=self.download_name).pack(side='left', fill='x', expand=True, padx=(0, 10))
        download = ttk.Button(downloads, text='Download another model', command=self.download_model)
        download.pack(side='right')
        self.action_buttons.append(download)
        self.message = tk.StringVar(value='Stop disables automatic startup until you click Start again. After starting, allow ADAM up to 30 seconds to retry.')
        ttk.Label(main, textvariable=self.message, wraplength=720).pack(anchor='w', fill='x', pady=(5, 10))
        ttk.Label(main, text='Closing this window leaves services running. Model downloads need internet access.').pack(anchor='w')
        self.models = []
        root.protocol('WM_DELETE_WINDOW', self.close)
        if poll:
            root.after(50, self.refresh)
            root.after(100, self.drain)

    def select_model(self):
        name = self.model.get()
        self.perform(lambda: self.backend.select_model(name))

    def download_model(self):
        name = self.download_name.get().strip()
        self.perform(lambda: self.backend.download(name, lambda text: self.events.put(('message', text))))

    def perform(self, operation):
        if self.busy:
            return
        self.busy = True
        for button in self.action_buttons:
            button.state(['disabled'])
        self.message.set('Working…')
        def run():
            try:
                self.events.put(('message', operation()))
            except Exception as exc:
                logging.exception('Control operation failed')
                self.events.put(('message', 'Unable to complete this action: '+str(exc)[:220]))
            finally:
                self.events.put(('finished', None))
        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        if self.closed:
            return
        if not self.polling:
            self.polling = True
            def run():
                try:
                    self.events.put(('status', self.backend.status()))
                except Exception as exc:
                    self.events.put(('message', str(exc)[:200]))
                finally:
                    self.polling = False
            threading.Thread(target=run, daemon=True).start()
        self.window.after(3000, self.refresh)

    def show_detail(self):
        model = next((item for item in self.models if item['name'] == self.model.get()), {})
        details = model.get('details', {})
        info = [details.get('parameter_size', ''), details.get('quantization_level', '')]
        if model.get('size'):
            info.append(f"{model['size']/1024**3:.1f} GiB on disk")
        self.model_detail.set(' · '.join(part for part in info if part) or 'Choose an installed conversation model.')

    def drain(self):
        if self.closed:
            return
        while True:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'message':
                self.message.set(data)
            elif kind == 'finished':
                self.busy = False
                for button in self.action_buttons:
                    button.state(['!disabled'])
            else:
                self.load.set(data['load'])
                self.chat_status.set(('Ready · ' if data['ready'] else 'Running / warming · ')+data['selected'] if data['chat'] else 'Stopped / unavailable')
                self.speech_status.set(('Ready · ' if data['speech_ready'] else 'Running / warming · ')+'Whisper large-v3-turbo' if data['speech'] else 'Stopped / unavailable')
                self.models = data['models']
                self.choices['values'] = [m['name'] for m in self.models]
                if not self.model.get() or self.model.get() not in self.choices['values']:
                    self.model.set(data['selected'])
                self.show_detail()
        self.window.after(100, self.drain)

    def close(self):
        self.closed = True
        self.window.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--check-ui', action='store_true')
    args = parser.parse_args()
    if not args.check_ui and os.name == 'nt' and not ctypes.windll.shell32.IsUserAnAdmin():
        result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', sys.executable,
            subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]]), str(args.root), 1)
        if result <= 32:
            ctypes.windll.user32.MessageBoxW(None, 'Windows administrator access is needed to control the background services.', 'ADAM GPU Control', 0x10)
        return
    if not args.check_ui:
        (args.root/'logs').mkdir(parents=True, exist_ok=True)
        logging.basicConfig(handlers=[RotatingFileHandler(args.root/'logs/gpu-control.log', maxBytes=2*1024**2, backupCount=2)], level=logging.INFO,
                            format='%(asctime)s %(levelname)s %(message)s')
    root = tk.Tk()
    if args.check_ui:
        root.withdraw()
    app = ControlWindow(root, Backend(args.root), poll=not args.check_ui)
    if args.check_ui:
        root.update_idletasks()
        print(json.dumps({'window_created': True, 'title': root.title(), 'model_selector': bool(app.choices.winfo_exists())}))
        app.close()
    else:
        root.mainloop()


if __name__ == '__main__':
    main()
