"""Read-only, bounded model discovery. Never loads or downloads model weights."""
import asyncio
import json
import threading

import httpx
from fastapi import HTTPException

_slots = threading.BoundedSemaphore(4)


async def catalog_json(url, transport=None, api_key=''):
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, 'Model discovery is busy. Try again shortly.')
    try:
        async with asyncio.timeout(4):
            async with httpx.AsyncClient(transport=transport, trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(3, connect=1)) as client:
                headers = {'Authorization': 'Bearer '+api_key} if api_key else {}
                async with client.stream('GET', url, headers=headers) as response:
                    if response.status_code in (401, 403):
                        raise HTTPException(502, 'The model service rejected the API key. Check the key for this address.')
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 512*1024:
                            raise ValueError('Catalog too large')
                    data = json.loads(raw)
                    if not isinstance(data, dict):
                        raise ValueError('Invalid catalog')
                    return data
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise HTTPException(502, 'Cannot read models from this service. Check its address and that it is running. Your selection has been kept.') from None
    finally:
        _slots.release()


def catalog_models(data, kind):
    rows = data.get('models' if kind == 'conversation' else 'data')
    if not isinstance(rows, list) or len(rows) > 1000:
        raise HTTPException(502, 'The service did not return a supported model list. Check that speech uses Speaches and conversation uses Ollama.')
    result = {}
    wanted = 'automatic-speech-recognition' if kind == 'stt' else 'text-to-speech'
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = row.get('name' if kind == 'conversation' else 'id')
        if not isinstance(model, str) or not model.strip() or len(model) > 200 or any(ord(c) < 32 for c in model):
            continue
        if kind != 'conversation' and row.get('task') not in (None, wanted):
            continue
        if kind == 'conversation' and isinstance(row.get('capabilities'), list) and 'completion' not in row['capabilities']:
            continue
        label = 'Follow GPU Control selection (adam:latest)' if kind == 'conversation' and model == 'adam:latest' else model
        result[model] = dict(id=model, label=label)
    return dict(models=sorted(result.values(), key=lambda m: (m['id'] != 'adam:latest', m['id'].casefold())))


async def bounded_body(request):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 8192:
            raise HTTPException(400, 'Settings exceed their size limit.')
    return bytes(raw)
