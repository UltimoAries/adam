"""Transport-independent, bounded audio sessions for manual and wake triggers."""
from dataclasses import dataclass, field
from enum import Enum
import re

RATE = 16_000
FRAME_MS = 20
FRAME_BYTES = RATE * 2 * FRAME_MS // 1000
MAX_SECONDS = 15
MAX_BYTES = RATE * 2 * MAX_SECONDS


class SessionError(ValueError):
    pass


class Trigger(str, Enum):
    TOUCH = 'touch'
    WAKE = 'wake'


@dataclass(frozen=True)
class Utterance:
    session_id: str
    trigger: Trigger
    model_id: str | None
    pcm: bytes = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.pcm, bytes) or not self.pcm or len(self.pcm) % 2:
            raise SessionError('Expected nonempty signed 16-bit little-endian PCM')
        if len(self.pcm) > MAX_BYTES:
            raise SessionError('Utterance exceeds 15 seconds')

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (RATE * 2)


class AudioSession:
    """One active utterance. Bad frames invalidate it; cancellation frees its buffer.

    The transport must authenticate devices, cap connections/message sizes,
    and enforce a wall-clock timeout. This core performs no network or disk I/O.
    """

    def __init__(self, session_id: str, trigger: Trigger, model_id: str | None = None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', session_id):
            raise SessionError('Invalid session identifier')
        self.trigger = Trigger(trigger)
        if self.trigger is Trigger.WAKE and not model_id:
            raise SessionError('Wake trigger requires a model identifier')
        if model_id is not None and not re.fullmatch(r'[A-Za-z0-9_.-]{1,96}', model_id):
            raise SessionError('Invalid model identifier')
        self.session_id, self.model_id = session_id, model_id
        self._pcm = bytearray()
        self._next_sequence = 0
        self.closed = False

    @property
    def buffered_bytes(self) -> int:
        return len(self._pcm)

    def append(self, sequence: int, pcm: bytes) -> None:
        if self.closed:
            raise SessionError('Session is closed')
        if type(sequence) is not int or sequence != self._next_sequence:
            self.cancel()
            raise SessionError('Audio sequence gap or duplicate; start a new session')
        if not isinstance(pcm, bytes) or not 0 < len(pcm) <= FRAME_BYTES or len(pcm) % 2:
            self.cancel()
            raise SessionError('Frame must contain 1..320 complete 16-bit samples')
        if len(self._pcm) + len(pcm) > MAX_BYTES:
            self.cancel()
            raise SessionError('Utterance exceeds 15 seconds')
        self._pcm.extend(pcm)
        self._next_sequence += 1

    def finish(self) -> Utterance:
        if self.closed:
            raise SessionError('Session is closed')
        try:
            return Utterance(self.session_id, self.trigger, self.model_id, bytes(self._pcm))
        finally:
            self.cancel()

    def cancel(self) -> None:
        self._pcm.clear()
        self.closed = True
