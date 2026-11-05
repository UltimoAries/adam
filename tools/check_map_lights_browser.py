"""Isolated room-placement/browser checks. Uses synthetic HA; never opens real devices."""
import asyncio
import base64
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def serve(port):
    import uvicorn
    from fastapi import Request
    from headend.test_map_lights import MapLightTests
    fixture = MapLightTests()
    fixture.setUp()
    if os.environ.get('ADAM_MAP_PREVIEW_DIRECTORY'):
        from headend.map_layout import load_private_layout
        source = Path(os.environ['ADAM_MAP_PREVIEW_DIRECTORY'])
        layout = load_private_layout(source)
        if layout is None:
            raise ValueError('The private preview directory has no map layout.')
        destination = fixture.app.state.auth.directory / 'maps'
        destination.mkdir()
        shutil.copy2(source / 'maps/layout.json', destination / 'layout.json')
        shutil.copy2(layout['image'], destination / layout['image'].name)
        fixture.map.__init__(fixture.app.state.auth, fixture.service)
    fixture.map.clock = time.monotonic

    @fixture.app.put('/api/fixture/map-state')
    async def change(request: Request):
        options = await request.json()
        with fixture.map.lock:
            for row in fixture.rows:
                if row['entity_id'] in options:
                    row['state'] = options[row['entity_id']]
                if row['entity_id'] in options.get('brightness', {}):
                    row['attributes']['brightness'] = options['brightness'][row['entity_id']]
            fixture.map.cached = None
        return {'actions': fixture.actions}

    try:
        uvicorn.run(fixture.app, host='127.0.0.1', port=port, lifespan='off', access_log=False)
    finally:
        fixture.tearDown()


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def check():
    port, debug = free_port(), free_port()
    base = f'http://127.0.0.1:{port}'
    chrome_path = os.environ.get('CHROME_BIN') or shutil.which('chromium') or shutil.which('google-chrome') or r'C:\Program Files\Google\Chrome\Application\chrome.exe'
    artifacts = ROOT / 'artifacts' / 'map-lights'
    artifacts.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    with tempfile.TemporaryDirectory(prefix='adam-map-browser-') as profile, (artifacts / 'browser.log').open('w') as log:
        server = subprocess.Popen([sys.executable, __file__, '--serve', str(port)], cwd=ROOT, stdout=log, stderr=log, creationflags=flags)
        chrome = None
        try:
            for _ in range(100):
                try:
                    if httpx.get(base+'/api/health', timeout=.5).is_success:
                        break
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('Fixture failed to start; see '+str(artifacts / 'browser.log'))
            chrome = subprocess.Popen([chrome_path, '--headless=new', '--no-sandbox', '--enable-unsafe-swiftshader',
                '--use-angle=swiftshader', '--remote-debugging-port='+str(debug), '--user-data-dir='+profile,
                '--no-first-run', base], stdout=log, stderr=log, creationflags=flags)
            for _ in range(100):
                try:
                    target = next(t for t in httpx.get(f'http://127.0.0.1:{debug}/json', timeout=.5).json()
                                  if t['type'] == 'page' and str(port) in t['url'])
                    break
                except (httpx.HTTPError, StopIteration):
                    pass
                await asyncio.sleep(.2)
            else:
                raise RuntimeError('Chrome failed to start')
            async with websockets.connect(target['webSocketDebuggerUrl'], max_size=8*1024*1024) as ws:
                seq, errors = 0, []

                async def cdp(method, params=None):
                    nonlocal seq
                    seq += 1
                    current = seq
                    await ws.send(json.dumps(dict(id=current, method=method, params=params or {})))
                    while True:
                        reply = json.loads(await ws.recv())
                        if reply.get('method') == 'Runtime.exceptionThrown':
                            errors.append(reply)
                        if reply.get('id') == current:
                            if 'error' in reply:
                                raise RuntimeError(reply)
                            return reply.get('result', {})

                async def js(expression):
                    result = await cdp('Runtime.evaluate', dict(expression=expression, awaitPromise=True, returnByValue=True))
                    if 'exceptionDetails' in result:
                        raise RuntimeError(result)
                    return result.get('result', {}).get('value')

                async def until(expression):
                    for _ in range(180):
                        if await js(expression):
                            return
                        await asyncio.sleep(.1)
                    detail = await js("({active:document.activeElement?.outerHTML, notice:document.querySelector('#mapNotice')?.textContent, result:document.querySelector('#controlResult')?.textContent, mode:document.querySelector('#moveMode')?.outerHTML})")
                    raise AssertionError((expression, detail, errors))

                async def screenshot(name):
                    result = await cdp('Page.captureScreenshot', {'format': 'png'})
                    (artifacts / name).write_bytes(base64.b64decode(result['data']))

                await cdp('Runtime.enable')
                await cdp('Emulation.setDeviceMetricsOverride', dict(width=1280, height=1100, deviceScaleFactor=1, mobile=False))
                await until("location.origin==="+json.dumps(base)+" && document.readyState==='complete'")
                await js("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'test-only-password-123'})}).then(r=>r.json())")
                await cdp('Page.navigate', {'url': base+'/map'})
                await until("document.querySelector('#lightChoice')?.options.length===3")
                async def actions():
                    return await js("fetch('/api/fixture/map-state',{method:'PUT',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(r=>r.actions)")
                if os.environ.get('ADAM_MAP_PREVIEW_DIRECTORY'):
                    await until("document.querySelector('#floorArtwork').complete && document.querySelector('#floorArtwork').naturalWidth>0")
                    assert await js("document.querySelector('#lightInspector').hidden")
                    view_width = await js("document.querySelector('#floorScroll').getBoundingClientRect().width")
                    await js("document.querySelector('#moveMode').click()")
                    assert not await js("document.querySelector('#lightInspector').hidden")
                    assert view_width > await js("document.querySelector('#floorScroll').getBoundingClientRect().width")
                    await js("document.querySelector('#lightChoice').value='light.lounge';document.querySelector('#lightChoice').dispatchEvent(new Event('change'));document.querySelector('#roomChoice').value='kitchen';document.querySelector('#roomChoice').dispatchEvent(new Event('change'));document.querySelector('#placeLight').click()")
                    await until("document.querySelector('.light-marker') && !document.querySelector('#moveMode').disabled")
                    await js("document.querySelector('.light-marker').click()")
                    assert await actions() == []
                    await js("document.querySelector('#moveMode').click();document.querySelector('.light-marker').click()")
                    await until("document.querySelector('.light-marker').dataset.state==='on' && !document.querySelector('#moveMode').disabled")
                    await until("Number(getComputedStyle(document.querySelector('[data-room=kitchen] .room-illumination')).opacity)>.4")
                    full_glow = await js("Number(document.querySelector('[data-room=kitchen]').style.getPropertyValue('--room-glow'))")
                    assert len(await actions()) == 1
                    assert await js("document.querySelector('[data-room=kitchen] .room-floor').getAttribute('points')===document.querySelector('[data-room=kitchen] .room-illumination').getAttribute('points')")
                    await js("document.querySelector('#floorArtwork').decode()")
                    await screenshot('private-floor-desktop.png')
                    await js("document.querySelector('.light-marker').click()")
                    await until("document.querySelector('.light-marker').dataset.state==='off' && !document.querySelector('#moveMode').disabled")
                    await until("Number(getComputedStyle(document.querySelector('[data-room=kitchen] .room-illumination')).opacity)===0")
                    assert await js("getComputedStyle(document.querySelector('[data-room=kitchen] .room-floor')).fill") == 'rgba(0, 0, 0, 0.62)'
                    await screenshot('private-floor-off.png')
                    await js("document.querySelector('.light-marker').click()")
                    await until("document.querySelector('.light-marker').dataset.state==='on' && !document.querySelector('#moveMode').disabled")
                    await js("fetch('/api/fixture/map-state',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({brightness:{'light.lounge':32}})})")
                    await until("Number(document.querySelector('[data-room=kitchen]').style.getPropertyValue('--room-glow'))<"+str(full_glow*.6))
                    assert len(await actions()) == 3
                    ratio = await js("(()=>{const i=document.querySelector('#floorArtwork');const r=i.getBoundingClientRect();return Math.abs(r.width/r.height-i.naturalWidth/i.naturalHeight)})()")
                    assert ratio < .01, ratio
                    # Drag a marker onto each full-width outdoor band and reload.
                    await js("document.querySelector('#moveMode').click();document.querySelector('#floorSurface').scrollIntoView({block:'center'})")
                    for room, x, y in [('outdoor_back',850,-38), ('outdoor_front',150,738)]:
                        source = await js("(()=>{const r=document.querySelector('.light-marker').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                        dest = await js("(()=>{const r=document.querySelector('#floorSurface').getBoundingClientRect();return {x:r.x+r.width*("+str(x)+"+60)/1120,y:r.y+r.height*("+str(y)+"+75)/850}})()")
                        await cdp('Input.dispatchMouseEvent', dict(type='mousePressed', button='left', buttons=1, clickCount=1, **source))
                        await cdp('Input.dispatchMouseEvent', dict(type='mouseMoved', button='left', buttons=1, **dest))
                        await cdp('Input.dispatchMouseEvent', dict(type='mouseReleased', button='left', buttons=0, clickCount=1, **dest))
                        await until("document.querySelector('#roomChoice').value==="+json.dumps(room)+" && !document.querySelector('#moveMode').disabled")
                        saved = await js("fetch('/api/map/lights').then(r=>r.json()).then(r=>r.devices.find(d=>d.entity_id==='light.lounge'))")
                        assert abs(saved['x']-x)<3 and abs(saved['y']-y)<3, saved
                    assert len(await actions()) == 3
                    await cdp('Page.reload')
                    await until("document.querySelector('.light-marker') && !document.querySelector('#lightInspector').offsetWidth")
                    saved = await js("fetch('/api/map/lights').then(r=>r.json()).then(r=>r.devices.find(d=>d.entity_id==='light.lounge'))")
                    assert saved['room_id'] == 'outdoor_front'
                    await js("document.querySelector('[data-room=kitchen]').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
                    assert len(await actions()) == 3
                    await screenshot('private-floor-outdoor.png')
                    assert httpx.get(base+'/api/map/background').status_code == 401
                    await cdp('Emulation.setDeviceMetricsOverride', dict(width=390,height=1000,deviceScaleFactor=1,mobile=True))
                    assert await js('document.documentElement.scrollWidth<=window.innerWidth')
                    await screenshot('private-floor-mobile.png')
                    await js("fetch('/api/auth/logout',{method:'POST'})")
                    await until("document.querySelector('#mapDashboard').hidden")
                    assert await js("document.querySelector('#floorArtwork').getAttribute('src')") is None
                    assert not errors, errors
                    print(json.dumps(dict(private_image=True, aspect_ratio=True, marker_placement=True, marker_toggle=True, room_illumination=True, brightness=True, outdoor_placement=True, view_mode=True,
                        image_authentication=True, logout_clears_image=True, mobile=True, javascript_errors=0)))
                    return
                assert await js("document.querySelectorAll('.floor-room').length") == 8
                assert await js("document.querySelector('#moveMode').getAttribute('aria-pressed')") == 'false'
                await js("document.querySelector('#moveMode').click()")
                await js("document.querySelector('#lightChoice').value='light.lounge';document.querySelector('#lightChoice').dispatchEvent(new Event('change'));document.querySelector('#placeLight').click()")
                await until("document.querySelector('.light-marker')?.dataset.state==='off' && !document.querySelector('#moveMode').disabled")
                await screenshot('desktop-off.png')
                # Layout changes and selection must never trigger device actions.
                assert await actions() == []
                await js("document.querySelector('.light-marker').click()")
                assert await actions() == []
                assert await js("document.querySelector('#lightControls button').disabled")
                await js("document.querySelector('#moveMode').click()")
                # A drag in control mode must neither move nor toggle the marker.
                drag_start = await js("(()=>{const r=document.querySelector('.light-marker').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                drag_end = dict(x=drag_start['x']+40, y=drag_start['y']+20)
                await cdp('Input.dispatchMouseEvent', dict(type='mousePressed', button='left', buttons=1, clickCount=1, **drag_start))
                await cdp('Input.dispatchMouseEvent', dict(type='mouseMoved', button='left', buttons=1, **drag_end))
                await cdp('Input.dispatchMouseEvent', dict(type='mouseReleased', button='left', buttons=0, clickCount=1, **drag_end))
                assert await actions() == []
                # A pending command must not claim on or illuminate the room.
                await js("window.normalFetch=window.fetch;window.fetch=(p,o)=>String(p).endsWith('/actions')?new Promise(resolve=>{window.releaseAction=()=>resolve(window.normalFetch(p,o))}):window.normalFetch(p,o);document.querySelector('.light-marker').click()")
                assert await js("document.querySelector('.light-marker').dataset.state") == 'off'
                assert await js("document.querySelector('#moveMode').disabled")
                assert await js("getComputedStyle(document.querySelector('[data-room=living] .room-illumination')).opacity") == '0'
                await js("window.releaseAction();window.fetch=window.normalFetch")
                await until("document.querySelector('.light-marker')?.dataset.state==='on' && !document.querySelector('#moveMode').disabled")
                assert len(await actions()) == 1
                await screenshot('desktop-on.png')
                await js("document.querySelector('.light-marker').click()")
                await until("document.querySelector('.light-marker').dataset.state==='off' && !document.querySelector('#moveMode').disabled")
                assert len(await actions()) == 2
                await js("document.querySelector('.light-marker').focus()")
                await cdp('Input.dispatchKeyEvent', dict(type='keyDown', key='Enter', code='Enter', windowsVirtualKeyCode=13, text='\r'))
                await cdp('Input.dispatchKeyEvent', dict(type='keyUp', key='Enter', code='Enter', windowsVirtualKeyCode=13))
                await until("document.querySelector('.light-marker').dataset.state==='on' && !document.querySelector('#moveMode').disabled")
                assert len(await actions()) == 3
                # In view mode the inspector is hidden and room clicks do nothing.
                assert await js("document.querySelector('#lightInspector').hidden")
                await js("document.querySelector('[data-room=kitchen]').dispatchEvent(new MouseEvent('click',{bubbles:true}))")
                assert len(await actions()) == 3
                assert await js("document.querySelector('#roomChoice').value") == 'living'
                await js("window.savedFetch=window.fetch;window.fetch=(p,o)=>String(p)==='/api/map/lights'?Promise.reject(Error('test outage')):window.savedFetch(p,o)")
                await until("document.querySelector('.light-marker')?.dataset.state==='unavailable'")
                assert await js("document.querySelectorAll('#lightControls button').length") == 0
                await js("document.querySelector('.light-marker').click()")
                assert len(await actions()) == 3
                await until("getComputedStyle(document.querySelector('[data-room=living] .room-illumination')).opacity==='0'")
                await js("window.fetch=window.savedFetch")
                await until("document.querySelector('.light-marker')?.dataset.state==='on'")
                # Native pointer drag into the kitchen persists normalized coordinates.
                await js("document.querySelector('#moveMode').click();document.querySelector('#floorSurface').scrollIntoView({block:'center'})")
                source = await js("(()=>{const r=document.querySelector('.light-marker').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                dest = await js("(()=>{const r=document.querySelector('#floorSurface').getBoundingClientRect();return {x:r.x+r.width*.56,y:r.y+r.height*.30}})()")
                await cdp('Input.dispatchMouseEvent', dict(type='mouseMoved', **source))
                await cdp('Input.dispatchMouseEvent', dict(type='mousePressed', button='left', buttons=1, clickCount=1, **source))
                for step in range(1, 9):
                    pos = {k: source[k]+(dest[k]-source[k])*step/8 for k in ('x','y')}
                    await cdp('Input.dispatchMouseEvent', dict(type='mouseMoved', button='left', buttons=1, **pos))
                await cdp('Input.dispatchMouseEvent', dict(type='mouseReleased', button='left', buttons=0, clickCount=1, **dest))
                await until("document.querySelector('#roomChoice').value==='kitchen' && !document.querySelector('#moveMode').disabled")
                saved = await js("fetch('/api/map/lights').then(r=>r.json()).then(r=>r.devices.find(d=>d.entity_id==='light.lounge'))")
                assert saved['room_id'] == 'kitchen', saved
                assert abs(saved['x']-560) < 3 and abs(saved['y']-210) < 3, saved
                assert len(await actions()) == 3
                # Focused markers support keyboard positioning without switching power.
                await js("document.querySelector('.light-marker').focus()")
                await cdp('Input.dispatchKeyEvent', dict(type='keyDown', key='ArrowRight', code='ArrowRight', windowsVirtualKeyCode=39))
                await cdp('Input.dispatchKeyEvent', dict(type='keyUp', key='ArrowRight', code='ArrowRight', windowsVirtualKeyCode=39))
                await until("!document.querySelector('#moveMode').disabled")
                moved = await js("fetch('/api/map/lights').then(r=>r.json()).then(r=>r.devices.find(d=>d.entity_id==='light.lounge'))")
                assert moved['x'] == saved['x']+10, moved
                await js('window.browserCheckReload=true')
                await cdp('Page.reload')
                await until("!window.browserCheckReload && document.querySelector('.light-marker')?.dataset.state==='on'")
                persisted = await js("document.querySelector('.light-marker').style.left")
                assert abs(float(persisted.rstrip('%'))-moved['x']/10) < .01
                # Touch dragging and responsive sidebar on a narrow screen.
                await cdp('Emulation.setDeviceMetricsOverride', dict(width=390, height=1000, deviceScaleFactor=1, mobile=True))
                await cdp('Emulation.setTouchEmulationEnabled', {'enabled': True})
                await js("document.querySelector('#moveMode').click();document.querySelector('#floorSurface').scrollIntoView({block:'center'})")
                source = await js("(()=>{const r=document.querySelector('.light-marker').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()")
                dest = dict(x=source['x']-30, y=source['y']+20)
                await cdp('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[source]})
                await cdp('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[dest]})
                await cdp('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
                await until("!document.querySelector('#moveMode').disabled")
                touched = await js("fetch('/api/map/lights').then(r=>r.json()).then(r=>r.devices.find(d=>d.entity_id==='light.lounge'))")
                assert touched['x'] < moved['x']-10, touched
                assert await js('document.documentElement.scrollWidth<=window.innerWidth'), 'Mobile horizontal overflow'
                await js("window.scrollTo(0,0)")
                await screenshot('mobile-map.png')
                await js("document.querySelector('.light-inspector').scrollIntoView({block:'start'})")
                await screenshot('mobile-controls.png')
                await js("fetch('/api/fixture/map-state',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({'light.lounge':'unavailable'})})")
                await until("document.querySelector('.light-marker')?.dataset.state==='unavailable'")
                await js("document.querySelector('#removeLight').click()")
                await until("document.querySelectorAll('.light-marker').length===0 && !document.querySelector('#moveMode').disabled")
                assert len(await actions()) == 3
                # Session loss clears all map data and controls.
                await js("fetch('/api/auth/logout',{method:'POST'})")
                await until("document.querySelector('#mapDashboard').hidden && !document.querySelector('#signIn').hidden")
                assert await js("document.querySelector('#lightMarkers').childElementCount") == 0
                assert await js("fetch('/api/map/lights').then(r=>r.status)") == 401
                await js("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'test-only-password-123'})})")
                await cdp('Emulation.setTouchEmulationEnabled', {'enabled': False})
                # Every page renders the same menu and identifies the active page.
                for path in ('/', '/map', '/home-assistant', '/queries', '/voices', '/cameras', '/models', '/training', '/bench'):
                    await cdp('Page.navigate', {'url':base+path})
                    await until("location.pathname==="+json.dumps(path)+" && document.readyState==='complete' && document.querySelectorAll('.adam-nav-links a').length===9")
                    assert await js("document.querySelector('.adam-nav-links [aria-current=page]').getAttribute('href')") == path
                    await js("document.querySelector('.adam-nav-menu summary').click()")
                    assert await js("document.querySelector('.adam-nav-menu').open")
                    await cdp('Input.dispatchKeyEvent', dict(type='keyDown', key='Escape', code='Escape', windowsVirtualKeyCode=27))
                    assert not await js("document.querySelector('.adam-nav-menu').open")
                    assert await js('document.documentElement.scrollWidth<=window.innerWidth'), path+' overflows'
                assert not errors, errors
                print(json.dumps(dict(placement=True, controls=True, mouse_drag=True,
                    keyboard_move=True, touch_drag=True, persistence=True, unavailable=True, mobile=True,
                    removal=True, failure_recovery=True, logout_clears=True, navigation_pages=9,
                    edit_mode=True, marker_toggle=True, pending_state=True, device_actions=3, javascript_errors=0)))
        finally:
            if chrome:
                chrome.terminate(); chrome.wait(timeout=10)
            server.terminate(); server.wait(timeout=10)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve(int(sys.argv[-1]))
    else:
        asyncio.run(check())
