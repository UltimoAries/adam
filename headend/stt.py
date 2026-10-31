"""Local transcription boundary; no model download, listener or home-control action."""
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from .session import Utterance


@dataclass(frozen=True)
class Transcript:
    session_id: str
    text: str
    language: str = 'en'


class Transcriber(Protocol):
    def transcribe(self, utterance: Utterance) -> Transcript: ...


class FasterWhisperSTT:
    """CPU INT8 provider. Load an explicitly provisioned local model directory.

    Calls are synchronous: a future service must run them in a bounded worker,
    outside its network event loop. Model acquisition is a separate setup step.
    """

    def __init__(self, model_directory: Path, cpu_threads: int = 4):
        directory = Path(model_directory).resolve()
        # faster-whisper can fetch a tokenizer even with local_files_only=True
        # when tokenizer.json is absent. Require it before importing the backend.
        required = ('model.bin', 'config.json', 'tokenizer.json')
        if not directory.is_dir() or any(not (directory / name).is_file() for name in required):
            raise ValueError('Local model requires model.bin, config.json and tokenizer.json')
        if not 1 <= cpu_threads <= 16:
            raise ValueError('cpu_threads must be between 1 and 16')
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise RuntimeError('faster-whisper is not installed in this Python environment') from exc
        self._model = WhisperModel(str(directory), device='cpu', compute_type='int8',
                                   cpu_threads=cpu_threads, num_workers=1, local_files_only=True)

    def transcribe(self, utterance: Utterance) -> Transcript:
        import numpy as np
        samples = np.frombuffer(utterance.pcm, dtype='<i2').astype(np.float32) / 32768.0
        segments, _ = self._model.transcribe(samples, language='en', task='transcribe',
                                             beam_size=1, condition_on_previous_text=False,
                                             vad_filter=True)
        text = ' '.join(segment.text.strip() for segment in segments).strip()
        return Transcript(utterance.session_id, text)
