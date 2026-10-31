"""Normalize a short cue for local playback; optional one-shot ElevenLabs generation."""
import hashlib
import io
import numpy as np
import av
import httpx
from .clips import wav_bytes

PROMPT=('One short human vocalization: a playful, cartoonish, slightly exasperated '
        '"Huh?" from a sleepy little character who has just been interrupted. '
        'Natural expressive human delivery, a tiny breathy onset and a curious rising finish. '
        'One Huh only, under one second. Dry clean close voice, no music, no background sounds, '
        'no metallic or robotic processing, no extra words.')


def decode_cue(raw):
    if not 0<len(raw)<=10*1024*1024:
        raise ValueError('Cue file must be smaller than 10 MiB')
    chunks=[];total=0
    resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
    try:
        with av.open(io.BytesIO(raw)) as container:
            for frame in container.decode(audio=0):
                for output in resampler.resample(frame):
                    values=output.to_ndarray().reshape(-1)
                    total+=len(values)
                    if total>160000:raise ValueError('Use a short cue, not a longer recording')
                    chunks.append(values)
            chunks.extend(frame.to_ndarray().reshape(-1) for frame in resampler.resample(None))
    except (av.error.FFmpegError,IndexError) as exc:
        raise ValueError('Could not decode this audio file') from exc
    if not chunks:raise ValueError('No audio in this file')
    values=np.concatenate(chunks).astype(np.float64)
    peak=float(np.max(np.abs(values)))
    if peak<10:raise ValueError('Cue is silent')
    active=np.flatnonzero(abs(values)>max(25,peak*.012))
    values=values[max(0,active[0]-640):min(len(values),active[-1]+1280)]
    if not 800<=len(values)<=32000:raise ValueError('Trim the cue to 0.05–2 seconds before installing')
    values-=values.mean();values*=10000/max(float(np.max(np.abs(values))),1)
    fade=min(96,len(values)//4);values[:fade]*=np.linspace(0,1,fade);values[-fade:]*=np.linspace(1,0,fade)
    pcm=np.rint(values).astype('<i2')
    metadata=dict(rate=16000,channels=1,bits=16,seconds=round(len(pcm)/16000,3),peak=10000,
                  sha256=hashlib.sha256(pcm.tobytes()).hexdigest())
    return pcm.tobytes(),wav_bytes(16000,pcm),metadata


def generate_elevenlabs(key,prompt=PROMPT,client=None):
    """One explicit generation, with no retries or credential persistence.

    Only the supplied text prompt is sent. MP3 works without requiring the
    higher-tier raw-PCM output option; decoding/resampling happens locally.
    """
    if not key.strip():raise ValueError('Enter an ElevenLabs API key locally')
    sender=client or httpx.Client(timeout=90)
    try:
        result=sender.post('https://api.elevenlabs.io/v1/sound-generation',
            params={'output_format':'mp3_44100_128'},headers={'xi-api-key':key.strip()},
            json=dict(text=prompt,duration_seconds=1.0,prompt_influence=.7,
                      model_id='eleven_text_to_sound_v2',loop=False))
        if result.status_code!=200:
            raise ValueError(f'ElevenLabs returned HTTP {result.status_code}. Check key permissions and available credits.')
        if len(result.content)>1024*1024:raise ValueError('Generated audio was unexpectedly large')
        pcm,wav,metadata=decode_cue(result.content)
        metadata.update(source='ElevenLabs Sound Effects',model='eleven_text_to_sound_v2',prompt=prompt,
                        source_url='https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert')
        return pcm,wav,metadata
    finally:
        if client is None:sender.close()
