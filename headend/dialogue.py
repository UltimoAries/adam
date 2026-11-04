"""Bounded topic memory and one-use, reply-scoped follow-up listening grants."""
import re
import threading
import time

from .ha_devices import status_words
from .queries import weather_intent


def context_key(entry):
    if entry.get('trigger') in ('wake', 'followup') and entry.get('unit_id'):
        return 'speaker:' + entry['unit_id']
    return entry.get('context_key')


class Dialogue:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.RLock()
        self.contexts = {}
        self.grants = {}

    def previous(self, entry):
        with self.lock:
            history = self.contexts.get(context_key(entry), (0, []))[1]
            for i, item in enumerate(history):
                if item is entry:
                    return history[i-1] if i else {}
        return {}

    def prepare(self, entry, job):
        key, now = context_key(entry), self.clock()
        words = status_words(job['text'])
        with self.lock:
            self.contexts = {k:v for k,v in self.contexts.items() if now-v[0] < 90}
            history = self.contexts.get(key, (now, []))[1]
            if re.fullmatch(r'(?:ok |okay |alright |all right |no )?'
                            r'(?:that s all|that is all|that s it|that is it|i m good|i am good|we re good|we are good|'
                            r'never mind|nevermind|stop listening|end conversation|goodbye|thanks|thank you)'
                            r'(?: adam| thanks| thank you)?', words):
                self.contexts.pop(key, None)
                entry['end_conversation'] = True
                return job
            previous = history[-1] if history else {}
            reply = previous.get('reply', {})
            answered = reply.get('answer_status', reply.get('status')) == 'answered'
            choices = reply.get('location_choices', [])
            topic = previous.get('weather_context') if answered or choices else None
            match = re.fullmatch(r'(?:what|how) about (.{1,80})', words)
            if topic and choices:
                target = match[1] if match else words
                exact = [c for c in choices if (' '+target+' ') in (' '+status_words(c)+' ')]
                if not exact and not re.search(r'\b(turn|set|switch|start|stop|open|close|lock|unlock|call)\b', target):
                    # Short city names can be misheard ("Seattle" as "see you
                    # at"). A complete, unique offered region is sufficient for
                    # this read-only clarification, without guessing a device.
                    exact = [c for c in choices if len(c.split(', ')) >= 2 and
                             (' '+status_words(c.split(', ')[1])+' ') in (' '+target+' ')]
                if len(exact) == 1:
                    # The provider's label includes a country code; the existing
                    # city/state lookup accepts its first two components.
                    place = ', '.join(exact[0].split(', ')[:2])
                    resolved = 'weather ' + topic['day'] + ' in ' + place
                    entry['resolved_text'] = resolved
                    job = dict(job, text=resolved)
                    match = None
            if topic and match:
                target = match[1]
                # Resolve only a day/place, never transform a device control.
                if not re.search(r'\b(turn|set|switch|stop|start|lock|unlock|lights?|devices?|speakers?|it|them|and)\b', target):
                    day, place = topic['day'], topic['place']
                    if target in ('today', 'tomorrow', 'now', 'right now'):
                        day = 'current' if target in ('now', 'right now') else target
                    else:
                        day_match = re.search(r'\b(today|tomorrow)\b', target)
                        if day_match:
                            day = day_match[1]
                            target = re.sub(r'\b(today|tomorrow)\b', '', target).strip()
                        place = re.sub(r'^in ', '', target)
                    resolved = 'weather ' + (day if day != 'current' else 'now') + (' in ' + place if place else '')
                    entry['resolved_text'] = resolved
                    job = dict(job, text=resolved)
            # Only consecutive successful LLM exchanges enter model context.
            messages = []
            for old in reversed(history):
                result = old.get('reply', {})
                if not result.get('provider') or result.get('answer_status', result.get('status')) != 'answered':
                    break
                messages[0:0] = [{'role':'user', 'content':old.get('dialogue_text', '')[:800]},
                                 {'role':'assistant', 'content':result['text'][:220]}]
                if len(messages) >= 6:
                    break
            entry['conversation_history'] = messages
            entry['dialogue_text'] = job['text']
            # Explicit outdoor weather only: indoor temperature must not seed this.
            weather = weather_intent(job['text'])
            if weather and not weather.get('unsupported') and re.search(r'\b(weather|forecast|rain|snow|outside)\b', job['text'], re.I):
                entry['weather_context'] = weather
            if key:
                self.contexts[key] = (now, [*history[-5:], entry])
                while len(self.contexts) > 128:
                    self.contexts.pop(next(iter(self.contexts)))
        return job

    def offer(self, unit_id, page_id, entry, adaptive=False):
        now = self.clock()
        with self.lock:
            self.grants = {k:v for k,v in self.grants.items() if now-v['created'] < 120}
            if (entry.get('trigger') not in ('wake', 'followup') or entry.get('end_conversation')
                    or time.time()-entry.get('started', 0) > 60
                    or entry.get('followup_count', 0) >= 6):
                return False
            chain_end = entry.get('conversation_deadline', now+120)
            if now >= chain_end:
                return False
            reply = entry.get('reply', {})
            seconds = (12 if reply.get('needs_clarification') or reply.get('location_choices') else
                       5 if reply.get('action') and reply['action'] != 'status' else 8) if adaptive else 8
            if page_id not in self.grants:
                while len(self.grants) >= 256:
                    self.grants.pop(next(iter(self.grants)))
                self.grants[page_id] = dict(unit=unit_id, created=now, phase='offered',
                    deadline=chain_end, count=entry.get('followup_count', 0)+1, seconds=seconds)
            return self.grants[page_id]['seconds'] if self.grants[page_id]['phase'] == 'offered' else 0

    def ack(self, unit_id, page_id, status):
        with self.lock:
            grant = self.grants.get(page_id)
            if grant and grant['unit'] == unit_id and grant['phase'] == 'offered':
                if status == 'delivered':
                    grant.update(phase='ready', expires=min(self.clock()+grant['seconds']+4, grant['deadline']))
                elif status in ('failed', 'interrupted'):
                    grant['phase'] = 'spent'

    def consume(self, unit_id, page_id):
        with self.lock:
            grant = self.grants.get(page_id)
            if not grant or grant['unit'] != unit_id or grant['phase'] != 'ready' or self.clock() >= grant['expires']:
                raise ValueError('Follow-up listening expired; say Hey Adam again')
            grant['phase'] = 'spent'
            return dict(followup_count=grant['count'], conversation_deadline=grant['deadline'])

    def clear_grants(self, unit_id):
        with self.lock:
            for grant in self.grants.values():
                if grant['unit'] == unit_id:
                    grant['phase'] = 'spent'
