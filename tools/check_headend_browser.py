"""Check the local UI in an already-started isolated Chrome DevTools session."""
import asyncio
import base64
import json
from pathlib import Path
import requests
import websockets


async def main():
    pages = requests.get('http://127.0.0.1:9223/json', timeout=3).json()
    target = next(page for page in pages if page['type'] == 'page' and '8765' in page['url'])
    messages = []
    sequence = 0
    async with websockets.connect(target['webSocketDebuggerUrl'], max_size=8*1024*1024) as socket:
        async def cdp(method, params=None):
            nonlocal sequence
            sequence += 1
            request_id = sequence
            await socket.send(json.dumps(dict(id=request_id, method=method, params=params or {})))
            while True:
                message = json.loads(await socket.recv())
                if message.get('id') == request_id:
                    if 'error' in message:
                        raise RuntimeError(message['error'])
                    return message.get('result', {})
                if message.get('method') == 'Runtime.exceptionThrown':
                    messages.append(message)

        async def evaluate(expression):
            result = await cdp('Runtime.evaluate',dict(expression=expression,awaitPromise=True,
                                                     returnByValue=True,userGesture=True))
            if 'exceptionDetails' in result:
                raise RuntimeError(result['exceptionDetails'])
            return result.get('result', {}).get('value')

        await cdp('Runtime.enable')
        await cdp('Page.enable')
        await cdp('Page.navigate', {'url':'http://127.0.0.1:8765'})
        await asyncio.sleep(1.5)
        for _ in range(30):
            if await evaluate("document.querySelectorAll('.clip-item').length") >= 4:
                break
            await asyncio.sleep(0.2)
        else:
            screenshot = await cdp('Page.captureScreenshot', {'format':'png'})
            Path('artifacts/headend-browser-failure.png').write_bytes(base64.b64decode(screenshot['data']))
            raise RuntimeError({'body':await evaluate('document.body.innerText'),'errors':messages})
        assert await evaluate("document.querySelector('#deviceState').textContent") == 'Connected · microphone ready'
        await evaluate("Array.from(document.querySelectorAll('.clip-item')).find(x=>x.textContent.includes('Clean speech reference')).click()")
        await evaluate("document.querySelector('#transcribe').click()")
        for _ in range(60):
            await asyncio.sleep(0.2)
            text = await evaluate("document.querySelector('#transcript').textContent")
            if 'living room lights' in text.lower():
                break
        else:
            raise RuntimeError('Reference transcript did not appear: '+str(text))
        playback = await evaluate("(async()=>{const a=document.querySelector('#player');await a.play();await new Promise(r=>setTimeout(r,400));const result={paused:a.paused,duration:a.duration,error:a.error?.message||null};a.pause();return result;})()")
        assert playback['paused'] is False and playback['error'] is None, playback
        assert 5 < playback['duration'] < 6, playback
        desktop = await cdp('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True})
        Path('artifacts/headend-desktop.png').write_bytes(base64.b64decode(desktop['data']))
        await cdp('Emulation.setDeviceMetricsOverride',dict(width=390,height=844,deviceScaleFactor=1,mobile=True))
        await asyncio.sleep(0.3)
        assert await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Mobile horizontal overflow'
        mobile = await cdp('Page.captureScreenshot', {'format':'png','captureBeyondViewport':True})
        Path('artifacts/headend-mobile.png').write_bytes(base64.b64decode(mobile['data']))
        assert not messages, messages
        result = dict(reference_transcript=text,browser_playback=playback,console_exceptions=messages,
                      desktop='headend-desktop.png',mobile='headend-mobile.png')
        Path('artifacts/headend-browser-check.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result, indent=2))


asyncio.run(main())
