"""Administrator voice-library endpoints. These share the fleet login gate."""
import asyncio
import json
from pathlib import Path
import tempfile
from fastapi import HTTPException,Request
from fastapi.responses import FileResponse,Response
from pydantic import BaseModel,Field
from .voice_package import MAX_PACKAGE,VoiceManifest,template


class VoiceChoice(BaseModel):
    voice_id: str=Field(min_length=5,max_length=90)


class VoicePreview(VoiceChoice):
    text: str=Field(default='Hello. This is the voice for your home.',min_length=1,max_length=200)


def add_voice_routes(router,service):
    @router.get('/api/voices')
    async def list_voices():
        voices=await asyncio.to_thread(service.registry.list)
        return dict(default_voice=next(v['voice_id'] for v in voices if v['active']),voices=voices,
            supported_engines=['piper-onnx'],package_schema=1,max_upload_bytes=MAX_PACKAGE)

    @router.get('/api/voices/template')
    async def download_template():
        return Response(json.dumps(template(),indent=2)+'\n',media_type='application/json',
            headers={'Content-Disposition':'attachment; filename="voice.template.json"'})

    @router.get('/api/voices/schema')
    async def download_schema():
        return Response(json.dumps(VoiceManifest.model_json_schema(),indent=2)+'\n',media_type='application/schema+json',
            headers={'Content-Disposition':'attachment; filename="voice-package.schema.json"'})

    @router.get('/api/voices/guide')
    async def guide():
        return FileResponse(Path(__file__).resolve().parent.parent/'docs'/'VOICE_PACKAGES.md',media_type='text/markdown',
                            filename='VOICE_PACKAGES.md')

    @router.post('/api/voices/import',status_code=201)
    async def import_voice(request: Request):
        if request.headers.get('content-length'):
            try:declared=int(request.headers['content-length'])
            except ValueError:raise HTTPException(400,'Invalid upload length')
            if declared>MAX_PACKAGE:raise HTTPException(413,'Voice ZIP exceeds 160 MiB')
        path=None
        try:
            with tempfile.NamedTemporaryFile(dir=service.registry.uploads,suffix='.zip',delete=False) as output:
                path=Path(output.name);total=0
                async for chunk in request.stream():
                    total+=len(chunk)
                    if total>MAX_PACKAGE:raise HTTPException(413,'Voice ZIP exceeds 160 MiB')
                    await asyncio.to_thread(output.write,chunk)
            return await asyncio.to_thread(service.import_package,path)
        finally:
            if path:path.unlink(missing_ok=True)

    @router.post('/api/voices/default')
    async def activate(options: VoiceChoice):
        await asyncio.to_thread(service.registry.activate,options.voice_id)
        return dict(default_voice=options.voice_id)

    @router.post('/api/voices/preview')
    async def preview(options: VoicePreview):
        audio,details=await asyncio.to_thread(service.generate,options.text,options.voice_id)
        return Response(audio,media_type='audio/wav',headers={'X-Voice-ID':details['voice'],
            'X-Generation-Ms':str(details.get('generation_ms',0))})

    @router.delete('/api/voices/{voice_id}')
    async def remove(voice_id: str):
        await asyncio.to_thread(service.remove,voice_id)
        return {'removed':True}
