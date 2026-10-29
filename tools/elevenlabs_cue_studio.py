"""Local masked-key window to generate/preview one ElevenLabs cue at a time.

Only a text prompt is sent to ElevenLabs. The key remains in this process;
generated audio is saved locally. Keep selection does not flash the device.
"""
import json
import os
from pathlib import Path
import threading
import tkinter as tk
from tkinter import messagebox,ttk
import uuid
import webbrowser
from headend.cue import PROMPT,generate_elevenlabs
from headend.clips import ClipStore

ROOT=Path(__file__).resolve().parent.parent
directory=ROOT/'headend/data/cues';directory.mkdir(parents=True,exist_ok=True)
window=tk.Tk();window.title('Adam · ElevenLabs listening cue');window.geometry('620x420')
panel=ttk.Frame(window,padding=20);panel.pack(fill='both',expand=True)
ttk.Label(panel,text='A playful “Huh?” for the box',font=('Segoe UI',17)).pack(anchor='w')
ttk.Label(panel,text='One generation uses your ElevenLabs credits. Only this prompt is sent.\nThe API key is masked, kept in memory, and never saved.',wraplength=570).pack(anchor='w',pady=(8,14))
ttk.Label(panel,text='ElevenLabs API key').pack(anchor='w')
key=tk.StringVar(value=os.environ.get('ELEVENLABS_API_KEY',''));entry=ttk.Entry(panel,textvariable=key,show='•');entry.pack(fill='x',pady=(4,12))
prompt=tk.Text(panel,height=6,wrap='word',font=('Segoe UI',10));prompt.insert('1.0',PROMPT);prompt.pack(fill='x')
status=tk.StringVar(value='Enter a key, then generate a candidate. No device recordings are uploaded.')
ttk.Label(panel,textvariable=status,wraplength=570).pack(anchor='w',pady=12)
buttons=ttk.Frame(panel);buttons.pack(fill='x');candidate={};running=False;closed=False
def finish(error,result):
    global running
    running=False;generate.configure(state='normal')
    if error:
        status.set(str(error));return
    pcm,wav,metadata=result;identifier=uuid.uuid4().hex;path=directory/(identifier+'.wav')
    path.write_bytes(wav);path.with_suffix('.json').write_text(json.dumps(metadata,indent=2)+'\n')
    clip=ClipStore(ROOT/'headend/data/clips').add(wav,'ElevenLabs playful Huh.wav','upload')
    candidate.update(path=str(path),metadata=metadata,clip_id=clip['id'])
    status.set(f'Candidate ready ({metadata["seconds"]:.2f} seconds). Preview it in Audio bench, then keep it or generate another.')
    preview.configure(state='normal');keep.configure(state='normal')
def make():
    global running
    if running:return
    credential=key.get();text=prompt.get('1.0','end').strip()
    if not credential.strip():messagebox.showinfo('API key needed','Enter your ElevenLabs API key in this window, not the chat.');return
    running=True;generate.configure(state='disabled');keep.configure(state='disabled');status.set('Generating one short cue…')
    def work():
        try:result=generate_elevenlabs(credential,text);error=None
        except Exception as exc:result=None;error=exc
        if not closed:window.after(0,lambda:finish(error,result))
    threading.Thread(target=work,daemon=True).start()
def choose():
    (directory/'selection.json').write_text(json.dumps(candidate,indent=2)+'\n')
    key.set('');window.destroy()
def close():
    global closed
    closed=True;key.set('');window.destroy()
generate=ttk.Button(buttons,text='Generate one candidate',command=make);generate.pack(side='left')
preview=ttk.Button(buttons,text='Open Audio bench preview',state='disabled',command=lambda:webbrowser.open('http://127.0.0.1:8765/bench'));preview.pack(side='left',padx=9)
keep=ttk.Button(buttons,text='Keep this cue',state='disabled',command=choose);keep.pack(side='left')
window.protocol('WM_DELETE_WINDOW',close);window.mainloop()
