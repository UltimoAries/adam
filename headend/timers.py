"""Persistent countdown timers: voice intents, dashboard API, and device panel sync."""
from concurrent.futures import ThreadPoolExecutor
import math
import re
import threading
import time
import unicodedata
import uuid

from fastapi import HTTPException
from pydantic import BaseModel, Field


MAX_DURATION = 86400
MIN_DURATION = 5
MAX_PER_UNIT = 8
RING_WINDOW = 60

_ONES = {
    'zero': 0, 'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
    'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10, 'eleven': 11,
    'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16,
    'seventeen': 17, 'eighteen': 18, 'nineteen': 19,
}
_TENS = {
    'twenty': 20, 'thirty': 30, 'forty': 40, 'fifty': 50,
    'sixty': 60, 'seventy': 70, 'eighty': 80, 'ninety': 90,
}


def _ascii_label(value: str, limit: int = 16) -> str:
    clean = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    clean = re.sub(r'[^A-Za-z0-9 _-]+', ' ', clean)
    return ' '.join(clean.split())[:limit]


def _norm(value: str) -> str:
    return re.sub(r'[^\w]+', ' ', value.casefold()).strip()


def _replace_number_words(text: str) -> str:
    text = re.sub(r'\bhalf\s+an?\s+hour\b', '30 minutes', text, flags=re.I)
    text = re.sub(r'\ba\s+quarter\s+(?:of\s+an?\s+)?hour\b', '15 minutes', text, flags=re.I)
    text = re.sub(r'\ban?\s+(hour|minute|second)\b', r'1 \1', text, flags=re.I)

    tokens = re.split(r'(\W+)', text)
    out = []
    i = 0
    while i < len(tokens):
        word = tokens[i].casefold()
        if word in _TENS:
            value = _TENS[word]
            j = i + 2
            if j < len(tokens) and re.fullmatch(r'[\s-]+', tokens[i + 1]) and tokens[j].casefold() in _ONES:
                value += _ONES[tokens[j].casefold()]
                out.append(str(value))
                i = j + 1
                continue
            out.append(str(value))
            i += 1
            continue
        if word in _ONES:
            out.append(str(_ONES[word]))
            i += 1
            continue
        out.append(tokens[i])
        i += 1
    converted = ''.join(out)
    converted = re.sub(
        r'(\d+)\s+and\s+a\s+half\s+(hours?|hr|hrs|minutes?|mins?|min)\b',
        lambda m: (f'{int(m[1]) * 60 + 30} minutes'
                   if m[2].casefold().startswith('h')
                   else f'{int(m[1])} minutes 30 seconds'),
        converted,
        flags=re.I,
    )
    return converted


def parse_duration_seconds(text: str) -> tuple[int, str]:
    """Return (total_seconds, remaining_text_without_duration)."""
    prepared = _replace_number_words(text)
    unit_rx = re.compile(
        r'(\d+(?:\.\d+)?)\s*(hours?|hrs?|hr|h|minutes?|mins?|min|m|seconds?|secs?|sec|s)\b',
        re.I,
    )
    matches = list(unit_rx.finditer(prepared))
    if not matches:
        return 0, prepared
    total = 0.0
    for m in matches:
        amount = float(m.group(1))
        unit = m.group(2).casefold()
        if unit.startswith('h'):
            total += amount * 3600
        elif unit.startswith('m'):
            total += amount * 60
        else:
            total += amount
    stripped = unit_rx.sub(' ', prepared)
    stripped = re.sub(r'\band\b', ' ', stripped, flags=re.I)
    return round(total), ' '.join(stripped.split())


def format_duration_spoken(seconds: int) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f'{hours} hour' + ('s' if hours != 1 else ''))
    if minutes:
        parts.append(f'{minutes} minute' + ('s' if minutes != 1 else ''))
    if secs or not parts:
        parts.append(f'{secs} second' + ('s' if secs != 1 else ''))
    if len(parts) == 3:
        return f'{parts[0]}, {parts[1]}, and {parts[2]}'
    if len(parts) == 2:
        return f'{parts[0]} and {parts[1]}'
    return parts[0]


def _clean_label(raw: str) -> str:
    label = re.sub(
        r'\b(?:hey|okay|ok|adam|jarvis|please|can|could|would|you|set|start|create|make|put|'
        r'add|extend|increase|for|to|by|on|my|the|a|an|timer|timers|countdown|named|called|labeled|labelled)\b',
        ' ',
        raw,
        flags=re.I,
    )
    label = re.sub(r'[^\w\s-]+', ' ', label)
    words = [w for w in label.split() if w]
    if not words:
        return ''
    joined = ' '.join(words)[:24].strip()
    return joined[:1].upper() + joined[1:] if joined else ''


def timer_intent(text: str) -> dict | None:
    """Parse wake/test utterances for countdown timer actions."""
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        return None
    cleaned = text.replace('\u2019', "'").strip()
    cleaned = re.sub(r'^\s*(?:(?:hey|okay|ok)[ ,]+)?(?:adam|jarvis)\b[\s,.:!?-]*', '', cleaned, flags=re.I)
    cleaned = re.sub(r'^\s*(?:please[ ,]+)?(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?', '', cleaned, flags=re.I)
    cleaned = cleaned.strip().rstrip('.!?').strip()
    if not cleaned:
        return None
    lower = _norm(cleaned)

    # Bare stop/silence only claims the session when a timer is actively ringing.
    if lower in ('stop', 'silence', 'quiet', 'dismiss'):
        return dict(action='stop_ringing_only')

    if re.fullmatch(r'(?:stop|silence|dismiss|quiet) (?:the |my )?timers?', lower):
        return dict(action='stop')

    if re.search(r'\b(?:do not|don t|dont|never)\b', lower):
        return None

    if re.fullmatch(r'(?:cancel|clear|delete|remove|stop) (?:all )?(?:of )?(?:my |the )?all timers', lower) or re.fullmatch(
        r'(?:cancel|clear|delete|remove|stop) all (?:of )?(?:my |the )?timers?', lower
    ):
        return dict(action='cancel_all')

    # Status / remaining time queries
    status_patterns = (
        r'(?:how much time|how long) (?:is )?(?:left|remaining)(?: on (?:the |my )?(.+?))?(?: timer)?',
        r'(?:what|whats|what s|check|show|list|status of) (?:are )?(?:the |my )?timers?',
        r'check (?:on )?(?:the |my )?(.+?) timer',
        r'(?:how much time|how long) (?:do i have )?(?:left )?(?:on|for) (?:the |my )?(.+?)(?: timer)?',
    )
    for pat in status_patterns:
        m = re.fullmatch(pat, lower)
        if m:
            raw_target = m.group(1) if m.lastindex else ''
            label = '' if _norm(raw_target or '') in ('', 'timer', 'timers', 'my timer', 'the timer', 'it') else _clean_label(raw_target)
            return dict(action='status', label=label)

    # Cancel specific or unnamed timer
    cancel_patterns = (
        r'(?:cancel|clear|delete|remove|stop) (?:the |my )?timer(?: (?:for|named|called) (.+))?',
        r'(?:cancel|clear|delete|remove|stop) (?:the |my )?(.+?) timer',
    )
    for pat in cancel_patterns:
        m = re.fullmatch(pat, lower)
        if m:
            label = _clean_label(m.group(1) or '')
            return dict(action='cancel', label=label)

    # Add / extend time on a timer
    if re.match(r'^(?:add|extend|increase)\b', lower) and (
        'timer' in lower or re.search(r'\b(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)\b', _replace_number_words(cleaned), re.I)
    ):
        seconds, remainder = parse_duration_seconds(cleaned)
        if seconds > 0 and ('timer' in lower or re.search(r'\bto\b', lower)):
            return dict(action='add', seconds=seconds, label=_clean_label(remainder))

    # Create timer
    if 'timer' in lower:
        seconds, remainder = parse_duration_seconds(cleaned)
        if seconds > 0:
            return dict(action='create', seconds=seconds, label=_clean_label(remainder))
        if re.match(r'^(?:set|start|create)\b', lower):
            return dict(action='invalid_duration')

    return None


class CreateTimerRequest(BaseModel):
    unit_id: str = Field(pattern=r'^[a-f0-9]{16}$')
    seconds: int = Field(ge=MIN_DURATION, le=MAX_DURATION)
    label: str = Field(default='', max_length=24)


class TimerQueryRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    unit_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')


class TimerService:
    source = 'Local Timers'

    def __init__(self, state, deliver, clock=time.time):
        self.state, self.deliver, self.clock = state, deliver, clock
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='timers')
        self.slots = threading.BoundedSemaphore(4)
        self.closed = False
        with state.db() as db:
            db.execute(
                '''CREATE TABLE IF NOT EXISTS timers (
                    id TEXT PRIMARY KEY,
                    unit_id TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    duration_seconds INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    end_unix INTEGER NOT NULL,
                    dismissed_at REAL
                )'''
            )

    def _prune(self, db, now: float):
        db.execute(
            'DELETE FROM timers WHERE dismissed_at IS NOT NULL OR end_unix < ?',
            (int(now) - 600,),
        )

    def _unit_names(self) -> dict[str, str]:
        return {u['id']: u['name'] for u in self.state.units()}

    def _active_rows(self, now: float, include_ringing: bool = True) -> list[dict]:
        cutoff = int(now) - (RING_WINDOW if include_ringing else 0)
        with self.lock, self.state.db() as db:
            self._prune(db, now)
            rows = db.execute(
                '''SELECT id, unit_id, label, duration_seconds, created_at, end_unix
                   FROM timers
                   WHERE dismissed_at IS NULL AND end_unix >= ?
                   ORDER BY end_unix ASC, created_at ASC''',
                (cutoff,),
            ).fetchall()
        names = self._unit_names()
        result = []
        for r in rows:
            rem = int(r['end_unix'] - math.floor(now))
            result.append(
                dict(
                    id=r['id'],
                    unit_id=r['unit_id'],
                    unit_name=names.get(r['unit_id'], 'Speaker'),
                    label=r['label'],
                    display_label=_ascii_label(r['label'] or 'TIMER').upper() or 'TIMER',
                    duration_seconds=r['duration_seconds'],
                    created_at=r['created_at'],
                    end_unix=r['end_unix'],
                    remaining_seconds=max(0, rem),
                    ringing=rem <= 0,
                )
            )
        return result

    def list_timers(self) -> list[dict]:
        return self._active_rows(self.clock(), include_ringing=True)

    def reconcile_poll(self, unit_id: str, dismissed_csv: str = '') -> list[dict]:
        now = self.clock()
        ids = [
            part.strip()
            for part in (dismissed_csv or '').split(',')
            if re.fullmatch(r'[a-f0-9]{16}', part.strip())
        ][:16]
        with self.lock, self.state.db() as db:
            if ids:
                placeholders = ','.join('?' for _ in ids)
                db.execute(
                    f'UPDATE timers SET dismissed_at=? WHERE unit_id=? AND id IN ({placeholders}) AND dismissed_at IS NULL',
                    (now, unit_id, *ids),
                )
            self._prune(db, now)
            rows = db.execute(
                '''SELECT id, label, duration_seconds, end_unix
                   FROM timers
                   WHERE unit_id=? AND dismissed_at IS NULL AND end_unix >= ?
                   ORDER BY end_unix ASC, created_at ASC
                   LIMIT 6''',
                (unit_id, int(now) - RING_WINDOW),
            ).fetchall()
        return [
            dict(
                id=r['id'],
                label=_ascii_label(r['label'] or 'TIMER').upper() or 'TIMER',
                duration_s=int(r['duration_seconds']),
                end_unix=int(r['end_unix']),
            )
            for r in rows
        ]

    def create_timer(self, unit_id: str, seconds: int, label: str = '') -> dict:
        if not MIN_DURATION <= seconds <= MAX_DURATION:
            raise ValueError('Timers must be between 5 seconds and 24 hours.')
        clean_label = _clean_label(label) if label else ''
        now = self.clock()
        with self.lock, self.state.db() as db:
            self._prune(db, now)
            count = db.execute(
                'SELECT COUNT(*) FROM timers WHERE unit_id=? AND dismissed_at IS NULL AND end_unix > ?',
                (unit_id, int(now)),
            ).fetchone()[0]
            if count >= MAX_PER_UNIT:
                raise ValueError(f'Up to {MAX_PER_UNIT} timers can run at once on one speaker.')
            timer_id = uuid.uuid4().hex[:16]
            end_unix = int(math.ceil(now)) + int(seconds)
            db.execute(
                'INSERT INTO timers (id, unit_id, label, duration_seconds, created_at, end_unix) VALUES (?,?,?,?,?,?)',
                (timer_id, unit_id, clean_label, int(seconds), now, end_unix),
            )
        return dict(
            id=timer_id,
            unit_id=unit_id,
            label=clean_label,
            display_label=_ascii_label(clean_label or 'TIMER').upper() or 'TIMER',
            duration_seconds=int(seconds),
            end_unix=end_unix,
            remaining_seconds=int(seconds),
            ringing=False,
        )

    def cancel_timer(self, timer_id: str) -> bool:
        if not re.fullmatch(r'[a-f0-9]{16}', timer_id):
            raise ValueError('Invalid timer identifier')
        now = self.clock()
        with self.lock, self.state.db() as db:
            updated = db.execute(
                'UPDATE timers SET dismissed_at=? WHERE id=? AND dismissed_at IS NULL',
                (now, timer_id),
            ).rowcount
        return bool(updated)

    def _match_timers(self, rows: list[dict], unit_id: str | None, label: str) -> list[dict]:
        if label:
            key = _norm(label)
            local = [r for r in rows if r['unit_id'] == unit_id and _norm(r['label']) == key]
            if local:
                return local
            fleet = [r for r in rows if _norm(r['label']) == key]
            if fleet:
                return fleet
            local_partial = [r for r in rows if r['unit_id'] == unit_id and key in _norm(r['label'])]
            if local_partial:
                return local_partial
            return [r for r in rows if key in _norm(r['label'])]
        local = [r for r in rows if r['unit_id'] == unit_id] if unit_id else []
        if local:
            return local
        return rows

    @staticmethod
    def _spoken_name(row: dict) -> str:
        return f"{row['label']} timer" if row['label'] else 'timer'

    def matches_utterance(self, text: str, unit_id: str | None = None) -> bool:
        parsed = timer_intent(text)
        if not parsed:
            return False
        if parsed['action'] == 'stop_ringing_only':
            now = self.clock()
            rows = self._active_rows(now, include_ringing=True)
            return any(r['ringing'] and (unit_id is None or r['unit_id'] == unit_id) for r in rows)
        return True

    def answer(self, text: str, unit_id: str | None = None) -> dict:
        parsed = timer_intent(text)
        if not parsed:
            return dict(status='ignored', text='')
        now = self.clock()
        action = parsed['action']

        if action == 'invalid_duration':
            return dict(status='unsupported', text='Please give a timer duration, such as 10 minutes or 45 seconds.')

        rows = self._active_rows(now, include_ringing=True)
        ringing_here = [r for r in rows if r['ringing'] and (unit_id is None or r['unit_id'] == unit_id)]

        if action == 'stop_ringing_only':
            if not ringing_here:
                return dict(status='ignored', text='')
            for r in ringing_here:
                self.cancel_timer(r['id'])
            return dict(status='answered', text='Timer stopped.', source=self.source)

        if action == 'stop':
            if ringing_here:
                for r in ringing_here:
                    self.cancel_timer(r['id'])
                return dict(status='answered', text='Timer stopped.', source=self.source)
            action = 'cancel'
            parsed = dict(action='cancel', label='')

        if action == 'create':
            seconds = parsed['seconds']
            if not MIN_DURATION <= seconds <= MAX_DURATION:
                return dict(status='unsupported', text='Timers must be between 5 seconds and 24 hours.')
            if not unit_id:
                units = self.state.units()
                if not units:
                    return dict(status='unavailable', text='Pair a speaker unit before setting a timer.')
                unit_id = units[0]['id']
            try:
                created = self.create_timer(unit_id, seconds, parsed['label'])
            except ValueError as exc:
                return dict(status='unsupported', text=str(exc))
            duration_words = format_duration_spoken(seconds)
            if created['label']:
                reply = f"{created['label']} timer set for {duration_words}."
            else:
                reply = f'Timer set for {duration_words}.'
            return dict(status='answered', text=reply, timer=created, source=self.source)

        running = [r for r in rows if not r['ringing']]

        if action == 'cancel_all':
            targets = [r for r in rows if unit_id is None or r['unit_id'] == unit_id] or rows
            if not targets:
                return dict(status='answered', text='No timers are running.', source=self.source)
            for r in targets:
                self.cancel_timer(r['id'])
            word = 'timer' if len(targets) == 1 else 'timers'
            return dict(status='answered', text=f'Cancelled {len(targets)} {word}.', source=self.source)

        if action == 'status':
            label = parsed.get('label', '')
            if label:
                matched = self._match_timers(rows, unit_id, label)
                if not matched:
                    return dict(status='answered', text=f'No {label} timer is running.', source=self.source)
                r = matched[0]
                if r['ringing']:
                    return dict(status='answered', text=f"The {self._spoken_name(r)} is done.", source=self.source)
                prefix = f"On {r['unit_name']}, the " if unit_id and r['unit_id'] != unit_id else 'The '
                return dict(
                    status='answered',
                    text=f"{prefix}{self._spoken_name(r)} has {format_duration_spoken(r['remaining_seconds'])} left.",
                    source=self.source,
                )
            local = [r for r in rows if r['unit_id'] == unit_id] if unit_id else rows
            targets = local or rows
            if not targets:
                return dict(status='answered', text='No timers are running.', source=self.source)
            other_room = bool(unit_id and not local)
            if len(targets) == 1:
                r = targets[0]
                if r['ringing']:
                    return dict(status='answered', text=f"The {self._spoken_name(r)} is done.", source=self.source)
                prefix = f"On {r['unit_name']}, " if other_room else ''
                subject = f"the {self._spoken_name(r)}" if r['label'] else 'your timer'
                sentence = f"{prefix}{subject} has {format_duration_spoken(r['remaining_seconds'])} left."
                return dict(status='answered', text=sentence[:1].upper() + sentence[1:], source=self.source)
            parts = []
            for r in targets[:3]:
                name = r['label'] or 'Timer'
                state_txt = 'done' if r['ringing'] else f"{format_duration_spoken(r['remaining_seconds'])} left"
                parts.append(f'{name}: {state_txt}')
            summary = f"You have {len(targets)} timers. " + '; '.join(parts) + '.'
            return dict(status='answered', text=summary[:195], source=self.source)

        if action == 'cancel':
            label = parsed.get('label', '')
            matched = self._match_timers(rows, unit_id, label)
            if not matched:
                msg = f'No {label} timer is running.' if label else 'No timers are running.'
                return dict(status='answered', text=msg, source=self.source)
            if len(matched) > 1:
                names = ', '.join(r['label'] or 'unnamed' for r in matched[:3])
                return dict(
                    status='ambiguous',
                    text=f'More than one timer is running ({names}). Name the timer or say cancel all timers.',
                    source=self.source,
                )
            target = matched[0]
            self.cancel_timer(target['id'])
            name = self._spoken_name(target)
            return dict(status='answered', text=f'Cancelled the {name}.', source=self.source)

        if action == 'add':
            add_sec = parsed['seconds']
            if not 1 <= add_sec <= MAX_DURATION:
                return dict(status='unsupported', text='Add between 1 second and 24 hours.')
            label = parsed.get('label', '')
            matched = self._match_timers(running, unit_id, label)
            if not matched:
                msg = f'No {label} timer is running.' if label else 'No timers are running.'
                return dict(status='answered', text=msg, source=self.source)
            if len(matched) > 1:
                return dict(
                    status='ambiguous',
                    text='More than one timer is running. Name which timer to extend.',
                    source=self.source,
                )
            target = matched[0]
            new_end = int(target['end_unix']) + int(add_sec)
            if new_end - int(now) > MAX_DURATION:
                return dict(status='unsupported', text='Timers cannot exceed 24 hours.')
            with self.lock, self.state.db() as db:
                db.execute(
                    'UPDATE timers SET end_unix=?, duration_seconds=duration_seconds+? WHERE id=? AND dismissed_at IS NULL',
                    (new_end, int(add_sec), target['id']),
                )
            rem = max(0, new_end - int(math.floor(now)))
            name = self._spoken_name(target)
            return dict(
                status='answered',
                text=f'Added {format_duration_spoken(add_sec)} to the {name}. {format_duration_spoken(rem)} left.',
                source=self.source,
            )

        return dict(status='ignored', text='')

    def submit(self, entry: dict, job: dict) -> bool:
        if self.closed or job.get('status') != 'done' or entry.get('trigger') != 'wake':
            return False
        if not self.matches_utterance(job.get('text', ''), entry.get('unit_id')):
            return False
        if not self.slots.acquire(blocking=False):
            entry['reply'] = dict(status='error', text='', error='Timer queue is full')
            return True
        entry['reply'] = dict(status='working', text='')
        try:
            self.pool.submit(self._run, entry, job['text'])
        except RuntimeError:
            self.slots.release()
            entry['reply'] = dict(status='error', text='', error='Head-end is stopping')
        return True

    def _run(self, entry: dict, text: str):
        try:
            started = time.monotonic()
            result = self.answer(text, entry.get('unit_id'))
            result['lookup_ms'] = round((time.monotonic() - started) * 1000)
            if not self.closed and result.get('text'):
                entry['reply'] = self.deliver(result, entry['unit_id'])
            else:
                entry['reply'] = result
        except Exception as exc:
            entry['reply'] = dict(status='error', text='', error=str(exc)[:160])
        finally:
            self.slots.release()

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)


def add_timer_routes(router, service: TimerService):
    @router.get('/api/timers')
    def get_timers():
        return dict(timers=service.list_timers())

    @router.post('/api/timers', status_code=201)
    def post_timer(options: CreateTimerRequest):
        if not any(u['id'] == options.unit_id for u in service.state.units()):
            raise HTTPException(404, 'Unknown speaker unit')
        try:
            return service.create_timer(options.unit_id, options.seconds, options.label)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.delete('/api/timers/{timer_id}')
    def delete_timer(timer_id: str):
        try:
            cancelled = service.cancel_timer(timer_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if not cancelled:
            raise HTTPException(404, 'Timer is no longer active')
        return {'cancelled': True}
