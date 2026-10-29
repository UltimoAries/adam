"""Exercise login, targeting, text/voice paging and mobile layout in isolated Chrome."""
import asyncio
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import time
import zipfile
import requests
import websockets

ROOT=Path(__file__).resolve().parent.parent
try:requests.get('http://127.0.0.1:8766/api/health',timeout=.5)
except requests.RequestException:pass
else:raise RuntimeError('Browser fixture port 8766 is occupied; stop the earlier fixture before running this check')
qa=ROOT/'headend'/'.browser-test';qa.mkdir(exist_ok=True)
temp=tempfile.TemporaryDirectory(dir=qa,prefix='fleet-qa-')
assert Path(temp.name).resolve().is_relative_to(qa.resolve())
server_log=open(qa/'fleet-fixture.log','w')
server=subprocess.Popen([str(ROOT/'headend/.venv/Scripts/python.exe'),'-m','tools.fleet_ui_fixture',temp.name],
    cwd=ROOT,stdout=server_log,stderr=server_log,creationflags=subprocess.CREATE_NO_WINDOW)
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
    chrome=subprocess.Popen([r'C:\Program Files\Google\Chrome\Application\chrome.exe','--headless=new',
        '--remote-debugging-port=9223','--user-data-dir='+str(qa/'fleet-profile'),'--no-first-run',
        '--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--autoplay-policy=no-user-gesture-required',
        'http://127.0.0.1:8766'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
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
        await js("document.querySelector('#searchName').value='Olympia, Washington';document.querySelector('#searchLocations').click()")
        await until("document.querySelectorAll('.location-result').length===1")
        await js("document.querySelector('.location-result').click();document.querySelectorAll('.saved-location input')[1].value='the cabin';document.querySelectorAll('.saved-location input')[1].dispatchEvent(new Event('input'));document.querySelector('#units').value='celsius';document.querySelector('#saveSettings').click()")
        await until("document.querySelector('#savedStatus').textContent.startsWith('Saved.')")
        await js("document.querySelector('#question').value='What is the weather at the cabin?';document.querySelector('#testQuery').click()")
        await until("document.querySelector('#answer').textContent.includes('In Olympia')")
        assert await js("document.querySelector('#answer').textContent.includes('Celsius')")
        await js("document.querySelector('#testUnit').selectedIndex=1;document.querySelector('#testQuery').click()")
        await until("document.querySelector('#answer').textContent.includes('Queued for playback')")
        await cdp('Page.reload')
        await until("document.querySelectorAll('.saved-location').length===2")
        assert await js("document.querySelector('#units').value==='celsius' && document.querySelectorAll('.saved-location input')[1].value==='the cabin'")
        for kind,width,mobile in [('desktop',1360,False),('mobile',390,True)]:
            await cdp('Emulation.setDeviceMetricsOverride',dict(width=width,height=1100,deviceScaleFactor=1,mobile=mobile))
            await asyncio.sleep(.2)
            assert await js('document.documentElement.scrollWidth<=window.innerWidth'),'Queries '+kind+' overflow'
            capture=await cdp('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))
            (ROOT/f'artifacts/queries-{kind}.png').write_bytes(base64.b64decode(capture['data']))
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
            query_persistence=True,mobile_overflow=False,console_errors=errors)
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
            subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:pass
    server_log.close();temp.cleanup()
