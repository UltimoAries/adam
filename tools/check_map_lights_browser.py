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
    fixture.map.clock = time.monotonic

    @fixture.app.put('/api/fixture/map-state')
    async def change(request: Request):
        options = await request.json()
        with fixture.map.lock:
            for row in fixture.rows:
                if row['entity_id'] in options:
                    row['state'] = options[row['entity_id']]
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
                    raise AssertionError(expression)

                async def screenshot(name):
                    result = await cdp('Page.captureScreenshot', {'format': 'png'})
                    (artifacts / name).write_bytes(base64.b64decode(result['data']))

                await cdp('Runtime.enable')
                await cdp('Emulation.setDeviceMetricsOverride', dict(width=1280, height=1100, deviceScaleFactor=1, mobile=False))
                await js("fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:'admin',password:'test-only-password-123'})}).then(r=>r.json())")
                await cdp('Page.reload')
                await until("document.querySelector('#mapViewport')?.dataset.ready==='true' && document.querySelector('#mapLightSelect')?.options.length===3")
                await js("document.querySelector('#mapLightSelect').value='light.lounge';document.querySelector('#mapRoomSelect').value='living';document.querySelector('#mapAssign').click()")
                await until("document.querySelector('[data-room=living]')?.textContent.includes('All lights off')")
                await js("document.querySelector('#mapViewport').scrollIntoView({block:'start'})")
                await screenshot('desktop-off.png')
                await js("fetch('/api/fixture/map-state',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({'light.lounge':'on'})})")
                await until("JSON.parse(document.querySelector('#mapViewport canvas').dataset.lightStates).living==='on'")
                await screenshot('desktop-on.png')
                # Real pointer selection uses projected floor coordinates and raycasting.
                point = await js("(()=>{const c=document.querySelector('#mapViewport canvas'),p=JSON.parse(c.dataset.rooms).find(r=>r.id==='study'),b=c.getBoundingClientRect();return {x:b.left+p.x,y:b.top+p.y};})()")
                await cdp('Input.dispatchMouseEvent', dict(type='mousePressed', button='left', clickCount=1, **point))
                await cdp('Input.dispatchMouseEvent', dict(type='mouseReleased', button='left', clickCount=1, **point))
                await until("document.querySelector('#mapRoomSelect').value==='study'")
                # The canvas drop path uses the same room hit testing as a physical drag.
                await js("(()=>{const c=document.querySelector('#mapViewport canvas'),p=JSON.parse(c.dataset.rooms).find(r=>r.id==='kitchen'),b=c.getBoundingClientRect(),d=new DataTransfer();d.setData('application/x-adam-light','light.lounge');c.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,clientX:b.left+p.x,clientY:b.top+p.y,dataTransfer:d}));})()")
                await until("document.querySelector('[data-room=kitchen]')?.textContent.includes('1 of 1 on') && document.querySelector('[data-room=living]')?.textContent.includes('No lights assigned')")
                await cdp('Page.reload')
                await until("document.querySelector('[data-room=kitchen]')?.textContent.includes('1 of 1 on')")
                await js("fetch('/api/fixture/map-state',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({'light.lounge':'unavailable'})})")
                await until("document.querySelector('[data-room=kitchen]')?.textContent.includes('Status unavailable')")
                await cdp('Emulation.setDeviceMetricsOverride', dict(width=390, height=1000, deviceScaleFactor=1, mobile=True))
                await js("document.querySelector('#mapRooms').scrollIntoView({block:'start'})")
                await asyncio.sleep(.3)
                assert await js('document.documentElement.scrollWidth<=window.innerWidth'), 'Mobile horizontal overflow'
                await screenshot('mobile-assignments.png')
                await js("document.querySelector('[data-remove=\"light.lounge\"]').click()")
                await until("document.querySelector('[data-room=kitchen]')?.textContent.includes('No lights assigned')")
                assert await js("fetch('/api/fixture/map-state',{method:'PUT',headers:{'Content-Type':'application/json'},body:'{}'}).then(r=>r.json()).then(r=>r.actions.length)") == 0
                assert not errors, errors
                print(json.dumps(dict(assignment=True, illumination=True, room_picking=True, canvas_drop=True,
                    persistence=True, unavailable=True, mobile=True, removal=True, device_actions=0, javascript_errors=0)))
        finally:
            if chrome:
                chrome.terminate(); chrome.wait(timeout=10)
            server.terminate(); server.wait(timeout=10)


if __name__ == '__main__':
    if '--serve' in sys.argv:
        serve(int(sys.argv[-1]))
    else:
        asyncio.run(check())
