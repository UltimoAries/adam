"""Exercise login, targeting, text/voice paging and mobile layout in isolated Chrome."""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
import requests
import websockets

ROOT=Path(__file__).resolve().parent.parent
CREATION_FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)


def find_venv_python():
    candidates=(
        [ROOT/'headend/.venv/Scripts/python.exe',ROOT/'.venv/Scripts/python.exe']
        if os.name=='nt' else
        [ROOT/'headend/.venv/bin/python',ROOT/'.venv/bin/python']
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def find_chrome():
    override=os.environ.get('CHROME_BIN')
    if override:
        resolved=override if Path(override).is_file() else shutil.which(override)
        if resolved:
            return resolved
        raise RuntimeError(f'CHROME_BIN not found or not executable: {override}')
    candidates=[
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
        str(Path.home()/r'AppData\Local\Google\Chrome\Application\chrome.exe'),
        '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
        'google-chrome',
        'google-chrome-stable',
        'chromium',
        'chromium-browser',
    ]
    for candidate in candidates:
        resolved=candidate if Path(candidate).is_file() else shutil.which(candidate)
        if resolved:
            return resolved
    raise RuntimeError('Chrome/Chromium binary not found; set CHROME_BIN to the browser executable')


try:requests.get('http://127.0.0.1:8766/api/health',timeout=.5)
except requests.RequestException:pass
else:raise RuntimeError('Browser fixture port 8766 is occupied; stop the earlier fixture before running this check')
qa=ROOT/'headend'/'.browser-test';qa.mkdir(exist_ok=True)
temp=tempfile.TemporaryDirectory(dir=qa,prefix='fleet-qa-')
assert Path(temp.name).resolve().is_relative_to(qa.resolve())
server_log=open(qa/'fleet-fixture.log','w')
server=subprocess.Popen([find_venv_python(),'-m','tools.fleet_ui_fixture',temp.name],
    cwd=ROOT,stdout=server_log,stderr=server_log,creationflags=CREATION_FLAGS)
chrome=None

async def check():
    global chrome
    for _ in range(40):
        if server.poll() is not None:raise RuntimeError('Fixture exited; read '+str(qa/'fleet-fixture.log'))
        try:
            if requests.get('http://127.0.0.1:8766/api/health',timeout=1).ok:break
        except requests.RequestException:pass
        await asyncio.sleep(.2)
    else:raise RuntimeError('Fixture did not start')
    chrome=subprocess.Popen([find_chrome(),'--headless=new',
        '--remote-debugging-port=9223','--user-data-dir='+str(qa/'fleet-profile'),'--no-first-run',
        '--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--autoplay-policy=no-user-gesture-required',
        'http://127.0.0.1:8766'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=CREATION_FLAGS)
    for _ in range(40):
        try:
            targets=requests.get('http://127.0.0.1:9223/json',timeout=1).json()
            target=next(t for t in targets if t['type']=='page' and '8766' in t['url']);break
        except (requests.RequestException,StopIteration):pass
        await asyncio.sleep(.2)
    else:raise RuntimeError('Chrome did not start')
    sequence=0;errors=[]
    async with websockets.connect(target['webSocketDebuggerUrl'],max_size=10*1024*1024) as ws:
        async def cdp(method,params=None):
            nonlocal sequence
            sequence+=1;current=sequence
            await ws.send(json.dumps(dict(id=current,method=method,params=params or {})))
            while True:
                message=json.loads(await ws.recv())
                if message.get('method')=='Runtime.exceptionThrown':errors.append(message)
                if message.get('id')==current:
                    if 'error' in message:raise RuntimeError(message)
                    return message.get('result',{})
        async def js(code):
            result=await cdp('Runtime.evaluate',dict(expression=code,awaitPromise=True,returnByValue=True,userGesture=True))
            if 'exceptionDetails' in result:raise RuntimeError(result['exceptionDetails'])
            return result.get('result',{}).get('value')
        async def until(expression):
            for _ in range(80):
                if await js(expression):return
                await asyncio.sleep(.1)
            raise RuntimeError('UI condition failed: '+expression+'\n'+str(await js('document.body.innerText')))
        await cdp('Runtime.enable');await cdp('Page.enable')
        await cdp('Emulation.setDeviceMetricsOverride',dict(width=1360,height=1100,deviceScaleFactor=1,mobile=False))
        await until("!document.querySelector('#auth').hidden")
        await js("document.querySelector('#username').value='admin';document.querySelector('#password').value='fixture-password-only';document.querySelector('#login').click()")
        await until("document.querySelectorAll('.unit-card').length===2")
        await until("document.querySelector('[data-id=storage-session] .recording-note')?.textContent.includes('Voice processing continues')")
        assert await js("!document.querySelector('[data-id=storage-session] audio')")
        await until("document.querySelector('#mapViewport').dataset.ready==='true'")
        assert await js("document.title==='ADAM · Automation Display and Mapping'")
        await until("!!document.querySelector('#mapViewport canvas').dataset.view")
        async def camera_view():
            await asyncio.sleep(.15)
            return await js("JSON.parse(document.querySelector('#mapViewport canvas').dataset.view)")
        initial_view=await camera_view()
        await js("document.querySelector('#mapZoomIn').click()")
        zoom_view=await camera_view()
        assert sum(x*x for x in zoom_view['position'])<sum(x*x for x in initial_view['position'])
        rect=await js("(()=>{const r=document.querySelector('#mapViewport canvas').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
        async def drag_map():
            await cdp('Input.dispatchMouseEvent',dict(type='mousePressed',button='left',buttons=1,clickCount=1,**rect))
            await cdp('Input.dispatchMouseEvent',dict(type='mouseMoved',button='left',buttons=1,x=rect['x']+65,y=rect['y']+35))
            await cdp('Input.dispatchMouseEvent',dict(type='mouseReleased',button='left',buttons=0,clickCount=1,x=rect['x']+65,y=rect['y']+35))
        await drag_map()
        orbit_view=await camera_view()
        assert orbit_view['position']!=zoom_view['position'] and orbit_view['target']==zoom_view['target']
        await js("document.querySelector('#mapPan').click()")
        await drag_map()
        pan_view=await camera_view()
        assert pan_view['target']!=orbit_view['target']
        await js("document.querySelector('#mapTop').click()")
        top_view=await camera_view()
        assert top_view['position'][1]>18 and abs(top_view['position'][0])<.01
        await js("document.querySelector('#mapReset').click()")
        assert await camera_view()==initial_view
        await js("document.querySelector('#mapViewport canvas').focus()")
        await cdp('Input.dispatchKeyEvent',dict(type='keyDown',key='ArrowLeft',code='ArrowLeft',windowsVirtualKeyCode=37))
        await cdp('Input.dispatchKeyEvent',dict(type='keyUp',key='ArrowLeft',code='ArrowLeft',windowsVirtualKeyCode=37))
        assert (await camera_view())['target']!=initial_view['target']
        await js("document.querySelector('#mapReset').click()")
        await js("Array.from(document.querySelectorAll('.unit-card')).find(n=>n.textContent.includes('Kitchen')).querySelector('input').click();document.querySelector('#message').value='Dinner is ready.';document.querySelector('#message').dispatchEvent(new Event('input'));document.querySelector('#sendPage').click()")
        await until("document.querySelector('#pages').textContent.includes('Dinner is ready.')")
        assert await js("document.querySelector('#sessions').textContent.includes('The kitchen lights are on.')")
        assert await js("document.querySelector('#sessions').textContent.includes('2.0 sec pre-roll')")
        await js("document.querySelector('#voiceTab').click();document.querySelector('#recordVoice').click()")
        await until("document.querySelector('#recordVoice').textContent.includes('Stop')")
        await asyncio.sleep(1.2);await js("document.querySelector('#recordVoice').click()")
        await until("!document.querySelector('#voicePreview').hidden && !document.querySelector('#sendPage').disabled")
        playback=await js("(async()=>{const a=document.querySelector('#voicePreview');await a.play();await new Promise(r=>setTimeout(r,150));const r={duration:a.duration,paused:a.paused,error:a.error?.message||null};a.pause();return r;})()")
        assert .8<playback['duration']<3 and not playback['paused'] and not playback['error'],playback
        await js("document.querySelector('#sendPage').click()")
        await until("document.querySelector('#pages').textContent.includes('Recorded voice message')")
        for kind,width,mobile in [('desktop',1360,False),('mobile',390,True)]:
            await cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1000,deviceScaleFactor=1,mobile=mobile))
            await asyncio.sleep(.2)
            if mobile:
                await cdp('Emulation.setTouchEmulationEnabled',dict(enabled=True,maxTouchPoints=2))
                touch_rect=await js("(()=>{const r=document.querySelector('#mapViewport canvas').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()")
                before_touch=await camera_view()
                await cdp('Input.dispatchTouchEvent',dict(type='touchStart',touchPoints=[dict(id=1,**touch_rect)]))
                await cdp('Input.dispatchTouchEvent',dict(type='touchMove',touchPoints=[dict(id=1,x=touch_rect['x']+30,y=touch_rect['y']+20)]))
                await cdp('Input.dispatchTouchEvent',dict(type='touchEnd',touchPoints=[]))
                assert (await camera_view())['position']!=before_touch['position']
                await cdp('Emulation.setTouchEmulationEnabled',dict(enabled=False))
                await js("document.querySelector('#mapReset').click()")
            await js("document.activeElement.blur()")
            assert await js('document.documentElement.scrollWidth<=window.innerWidth'),kind+' overflow'
            capture=await cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))
            (ROOT/f'artifacts/fleet-{kind}.png').write_bytes(base64.b64decode(capture['data']))
        # Test the voice library with a tiny explicitly synthetic model/runtime.
        # Actual Piper inference is validated separately by check_voice_runtime.
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/voices'))
        await until("document.querySelectorAll('.voice-card').length===1")
        source=Path(temp.name)/'import-source';package=Path(temp.name)/'browser-voice.zip'
        with zipfile.ZipFile(package,'w') as archive:
            for path in source.iterdir():archive.write(path,path.name)
        document=await cdp('DOM.getDocument')
        field=await cdp('DOM.querySelector',dict(nodeId=document['root']['nodeId'],selector='#packageInput'))
        await cdp('DOM.setFileInputFiles',dict(nodeId=field['nodeId'],files=[str(package)]))
        await until("!document.querySelector('#importPackage').disabled")
        await js("document.querySelector('#importPackage').click()")
        await until("document.querySelectorAll('.voice-card').length===2 && !document.querySelector('#generatePreview').disabled")
        assert await js("(async()=>{const v=await(await fetch('/api/voices')).json();return v.default_voice==='basic-english@1.0.0';})()")
        await js("document.querySelector('#generatePreview').click()")
        await until("!document.querySelector('#previewPlayer').hidden && document.querySelector('#previewDetails').textContent.includes('Preview ready')")
        voice_playback=await js("(async()=>{const a=document.querySelector('#previewPlayer');await a.play();const r={duration:a.duration,paused:a.paused,error:a.error?.message||null};a.pause();return r;})()")
        assert .1<voice_playback['duration']<1 and not voice_playback['paused'] and not voice_playback['error'],voice_playback
        await js("document.querySelector('.voice-card:not(.active) .primary').click()")
        await until("document.querySelector('#notice').textContent.includes('now the default')")
        assert await js("(async()=>{const v=await(await fetch('/api/voices')).json();return v.default_voice==='browser-character@1.0.0';})()")
        for kind,width,mobile in [('desktop',1360,False),('mobile',390,True)]:
            await cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1100,deviceScaleFactor=1,mobile=mobile))
            await asyncio.sleep(.2)
            assert await js('document.documentElement.scrollWidth<=window.innerWidth'),'Voices '+kind+' overflow'
            capture=await cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))
            (ROOT/f'artifacts/voices-{kind}.png').write_bytes(base64.b64decode(capture['data']))
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/home-assistant'))
        await until("!!document.querySelector('#haDashboard') && !document.querySelector('#haDashboard').hidden && document.querySelector('#serverAddress').value.length>0")
        await js("document.querySelector('#serverAddress').value='http://ha.local:8123';document.querySelector('#accessToken').value='browser-fixture-token';document.querySelector('#enabled').checked=true;document.querySelector('#autoImport').checked=true;document.querySelector('#saveSettings').click()")
        await until("!document.querySelector('#saveSettings').disabled && document.querySelectorAll('.device-card').length===3")
        assert await js("document.querySelector('#accessToken').value===''")
        await js("document.querySelector('#checkConnection').click()")
        await until("document.querySelector('#connectionStatus').textContent.includes('Connected to Home Assistant')")
        assert await js("document.querySelector('[data-entity=\"light.bedroom\"]').textContent.includes('Dimming')")
        assert await js("!document.querySelector('[data-entity=\"switch.living_room_led\"]')")
        await js("document.querySelector('#deviceFilter').value='all';document.querySelector('#deviceFilter').dispatchEvent(new Event('change'))")
        await until("document.querySelectorAll('.device-card').length===6")
        await js("document.querySelector('#deviceFilter').value='imported';document.querySelector('#deviceFilter').dispatchEvent(new Event('change'))")
        await js("document.querySelector('#command').value='Turn on Living Room';document.querySelector('#sendCommand').click()")
        await until("document.querySelector('#answer').textContent.includes('Living Room is now on')")
        await js("document.querySelector('[data-entity=\"switch.living_room\"] > button').click()")
        await until("document.querySelector('#deviceDialog').open")
        await js("document.querySelector('#deviceName').value='Lounge';document.querySelector('#deviceAliases').value='Front room';document.querySelector('#saveDevice').click()")
        await until("!document.querySelector('#deviceDialog').open && document.querySelector('[data-entity=\"switch.living_room\"] h3').textContent==='Lounge'")
        await js("document.querySelector('#command').value='Turn off Front room';document.querySelector('#sendCommand').click()")
        await until("document.querySelector('#answer').textContent.includes('Lounge is now off')")
        await cdp('Page.reload')
        await until("!!document.querySelector('[data-entity=\"switch.living_room\"] h3') && document.querySelector('[data-entity=\"switch.living_room\"] h3').textContent==='Lounge'")
        assert await js("document.querySelector('#accessToken').value===''")
        await js("document.querySelector('#deviceSearch').value='Front room';document.querySelector('#deviceSearch').dispatchEvent(new Event('input'))")
        await until("document.querySelectorAll('.device-card').length===1")
        await js("document.querySelector('#deviceSearch').value='';document.querySelector('#deviceSearch').dispatchEvent(new Event('input'))")
        await js("document.querySelector('[data-entity=\"light.bedroom\"] details').open=true")
        for kind,width,mobile in [('desktop',1360,False),('mobile',390,True)]:
            await cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1100,deviceScaleFactor=1,mobile=mobile))
            await asyncio.sleep(.2)
            assert await js('document.documentElement.scrollWidth<=window.innerWidth'),'Home Assistant '+kind+' overflow'
            capture=await cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))
            (ROOT/f'artifacts/home-assistant-{kind}.png').write_bytes(base64.b64decode(capture['data']))
        await js("document.querySelector('#forgetToken').click()")
        await until("document.querySelector('#tokenStatus').textContent==='No token saved.' && !document.querySelector('#enabled').checked")
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/'))
        await until("document.querySelectorAll('.unit-card').length===2 && !!document.querySelector('#pageVoice option[value=\"browser-character@1.0.0\"]')")
        await js("document.querySelector('.unit-card input').click();document.querySelector('#pageVoice').value='browser-character@1.0.0';document.querySelector('#message').value='A custom voice page.';document.querySelector('#message').dispatchEvent(new Event('input'));document.querySelector('#sendPage').click()")
        await until("document.querySelector('#pages').textContent.includes('A custom voice page.')")
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/voices'))
        await until("document.querySelectorAll('.voice-card').length===2")
        await js("document.querySelector('.voice-card:not(.active) .primary').click()")
        await until("document.querySelector('#notice').textContent.includes('now the default')")
        await js("document.querySelector('.voice-card:not(.active) .danger').click()")
        await until("document.querySelectorAll('.voice-card').length===1")
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/'))
        await until("document.querySelectorAll('.unit-card').length===2")
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/queries'))
        await until("!!document.querySelector('#queryDashboard') && !document.querySelector('#queryDashboard').hidden && document.querySelectorAll('.saved-location').length===1")
        await js("document.querySelector('#searchName').value='Portland, Oregon';document.querySelector('#searchName').dispatchEvent(new Event('input'))")
        await until("document.querySelectorAll('.location-result').length===1")
        await js("document.querySelector('.location-result').click();document.querySelectorAll('.saved-location input')[1].value='the cabin';document.querySelectorAll('.saved-location input')[1].dispatchEvent(new Event('input'));document.querySelector('#units').value='celsius';document.querySelector('#saveSettings').click()")
        await until("document.querySelector('#savedStatus').textContent.startsWith('Saved.')")
        await js("document.querySelector('#question').value='What is the weather at the cabin?';document.querySelector('#testQuery').click()")
        await until("document.querySelector('#answer').textContent.includes('In Portland')")
        assert await js("document.querySelector('#answer').textContent.includes('Celsius')")
        await js("document.querySelector('#testUnit').selectedIndex=1;document.querySelector('#testQuery').click()")
        await until("document.querySelector('#answer').textContent.includes('Queued for playback')")
        await cdp('Page.reload')
        await until("document.querySelectorAll('.saved-location').length===2")
        assert await js("document.querySelector('#units').value==='celsius' && document.querySelectorAll('.saved-location input')[1].value==='the cabin'")
        await js("const locationSelect=document.querySelector('#deviceLocations select');locationSelect.selectedIndex=2;locationSelect.dispatchEvent(new Event('change'));document.querySelector('#panelName').checked=false;document.querySelector('#saveSettings').click()")
        await until("document.querySelector('#savedStatus').textContent.startsWith('Saved.')")
        await cdp('Page.reload')
        await until("document.querySelectorAll('#deviceLocations select').length===2")
        assert await js("!document.querySelector('#panelName').checked && document.querySelector('#deviceLocations select').value.startsWith('Portland')")
        for kind,width,mobile in [('desktop',1360,False),('mobile',390,True)]:
            await cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1100,deviceScaleFactor=1,mobile=mobile))
            await asyncio.sleep(.2)
            assert await js('document.documentElement.scrollWidth<=window.innerWidth'),'Queries '+kind+' overflow'
            capture=await cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))
            (ROOT/f'artifacts/queries-{kind}.png').write_bytes(base64.b64decode(capture['data']))
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/'))
        await until("!!document.querySelector('#logout') && !document.querySelector('#logout').hidden")
        # Original archive playback, explicit approval, persistent tags and storage controls.
        await js("document.querySelector('#trainingLink').click()")
        await until("document.querySelectorAll('.recording').length===1")
        await js("document.querySelector('.recording').click()")
        await until("document.querySelector('#recordTitle')?.textContent==='Kitchen' && document.querySelector('#player').readyState>=1")
        assert await js("document.querySelector('#reviewLabel').value==='unreviewed' && document.querySelector('#reviewSession').value==='browser-household-01'")
        archive_playback=await js("(async()=>{const a=document.querySelector('#player');await a.play();await new Promise(r=>setTimeout(r,150));const r={duration:a.duration,paused:a.paused,error:a.error?.message||null};a.pause();return r;})()")
        assert archive_playback['duration']==3 and not archive_playback['paused'] and not archive_playback['error'],archive_playback
        original_hash=await js("(async()=>{const r=await(await fetch('/api/training/recordings')).json();return r.records[0].sha256;})()")
        await js("document.querySelector('#phraseStart').value='0.100';document.querySelector('#phraseEnd').value='1.600';document.querySelector('#reviewLabel').value='positive';document.querySelector('#reviewForm').requestSubmit()")
        await until("document.querySelector('#notice').textContent.includes('check the listening cue')")
        await js("document.querySelector('#gain').value='24';document.querySelector('#gain').dispatchEvent(new Event('change'));document.querySelector('#playSelection').click()")
        await until("document.querySelector('#notice').textContent.includes('Playing selected phrase')")
        await js("document.querySelector('#cueStatus').value='excluded';document.querySelector('#reviewSpeaker').value='Household A';document.querySelector('#reviewForm').requestSubmit()")
        await until("document.querySelector('#recordLabel').textContent==='positive' && document.querySelector('#positiveCount').textContent==='1'")
        await cdp('Page.reload')
        await until("document.querySelector('#reviewLabel')?.value==='positive' && document.querySelector('#reviewSpeaker').value==='Household A'")
        assert await js("document.querySelector('#phraseStart').value==='0.1' && document.querySelector('#phraseEnd').value==='1.6'")
        assert original_hash==await js("(async()=>{const r=await(await fetch('/api/training/recordings')).json();const a=await(await fetch('/api/training/recordings/'+r.records[0].id+'/audio')).arrayBuffer();return [...new Uint8Array(await crypto.subtle.digest('SHA-256',a))].map(v=>v.toString(16).padStart(2,'0')).join('');})()")
        await js("document.querySelector('details.panel').open=true;document.querySelector('#collectionUnit').selectedIndex=1;document.querySelector('#collectionUnit').dispatchEvent(new Event('change'))")
        await until("!document.querySelector('#manualListen').disabled")
        await js("document.querySelector('#contextSession').value='difficult-evening-01';document.querySelector('#manualListen').click()")
        await until("document.querySelector('#notice').textContent.includes('Listen requested')")
        assert await js("(async()=>{const c=await(await fetch('/api/training/contexts/'+document.querySelector('#collectionUnit').value)).json();return c.collection_session==='difficult-evening-01';})()")
        await js("document.querySelector('#maxRecords').value='12';document.querySelector('#settingsForm').requestSubmit()")
        await until("document.querySelector('#notice').textContent.includes('Storage settings saved')")
        await cdp('Page.reload')
        await until("document.querySelector('#maxRecords')?.value==='12' && document.querySelector('#reviewLabel').value==='positive'")
        await js("document.querySelector('#retry').click()")
        await until("document.querySelector('#transcript').textContent==='Fixture transcript'")
        assert await js("document.querySelector('#reviewLabel').value==='positive'")
        for kind,width,mobile in [('desktop',1360,False),('mobile',390,True)]:
            await cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1100,deviceScaleFactor=1,mobile=mobile))
            await asyncio.sleep(.2)
            assert await js('document.documentElement.scrollWidth<=window.innerWidth'),'Training '+kind+' overflow'
            capture=await cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))
            (ROOT/f'artifacts/training-{kind}.png').write_bytes(base64.b64decode(capture['data']))
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/models'))
        await until("!!document.querySelector('#modelForm') && !document.querySelector('#modelForm').hidden")
        await js("document.querySelector('#stt-mode').value='prefer_remote';document.querySelector('#stt-url').value='http://fixture-gpu:8000/v1';document.querySelector('#stt-model').value='test-model';document.querySelector('#stt-api_key').value='fixture-model-key';document.querySelector('#modelForm').requestSubmit()")
        await until("document.querySelector('#notice').textContent.includes('Saved.')")
        await cdp('Page.reload')
        await until("document.querySelector('#stt-model')?.value==='test-model'")
        assert await js("document.querySelector('#stt-api_key').value==='' && document.querySelector('#stt-keyStatus').textContent.includes('Key saved')")
        assert await js("(async()=>{const s=await(await fetch('/api/models/settings')).text();return !s.includes('fixture-model-key');})()")
        for kind,width,mobile in [('desktop',1360,False),('mobile',390,True)]:
            await cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1100,deviceScaleFactor=1,mobile=mobile))
            await asyncio.sleep(.2)
            assert await js('document.documentElement.scrollWidth<=window.innerWidth'),'Models '+kind+' overflow'
            capture=await cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))
            (ROOT/f'artifacts/models-{kind}.png').write_bytes(base64.b64decode(capture['data']))
        await js("document.querySelector('#stt-mode').value='local';document.querySelector('#stt-clear_key').checked=true;document.querySelector('#modelForm').requestSubmit()")
        await until("document.querySelector('#stt-keyStatus').textContent==='No key saved.'")
        await cdp('Page.navigate',dict(url='http://127.0.0.1:8766/'))
        await until("!!document.querySelector('#logout') && !document.querySelector('#logout').hidden")
        await until("document.querySelector('#mapViewport').dataset.ready==='true'")
        await js("document.querySelector('#mapViewport canvas').getContext('webgl2').getExtension('WEBGL_lose_context').loseContext()")
        await until("!document.querySelector('#mapFallback').hidden && document.querySelector('#mapZoomIn').disabled")
        await js("document.querySelector('#logout').click()")
        await until("!document.querySelector('#auth').hidden")
        assert await js("(async()=>{return (await fetch('/api/voices')).status===401;})()")
        assert not errors,errors
        result=dict(monitor_map=True,map_zoom=True,map_orbit=True,map_pan=True,map_top_view=True,map_reset=True,map_keyboard=True,map_touch=True,map_context_loss_fallback=True,
            login=True,text_page=True,recorded_page=True,preview=playback,
            voice_import=True,voice_preview=voice_playback,voice_default=True,per_page_voice=True,voice_remove=True,
            voice_auth=True,query_settings=True,query_location_search=True,query_preview=True,query_send=True,
            query_persistence=True,location_suggestions=True,device_locations=True,panel_name_option=True,home_assistant_setup=True,home_assistant_command=True,home_assistant_persistence=True,
            home_assistant_token_clear=True,home_assistant_setting_filter=True,spoken_name_preserves_target=True,device_capabilities=True,
            unsaved_live_recording=True,training_playback=archive_playback,training_review=True,training_boundaries=True,
            training_original_hash=True,training_tags=True,training_manual_listen=True,training_limits=True,
            training_persistence=True,training_retry=True,model_settings=True,model_key_redaction=True,model_settings_persistence=True,mobile_overflow=False,console_errors=errors)
        (ROOT/'artifacts/fleet-browser-check.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
        await cdp('Browser.close')

try:
    asyncio.run(check())
finally:
    # Windows venv Python and Chrome both create child processes. Close the
    # exact process trees launched here before removing the fixture directory.
    for process in (chrome,server):
        if process and process.poll() is None:
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else:
                process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                if os.name!='nt':
                    process.kill()
                    try:process.wait(timeout=2)
                    except subprocess.TimeoutExpired:pass
    server_log.close();temp.cleanup()
