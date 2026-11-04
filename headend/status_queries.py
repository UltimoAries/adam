"""Read-only home/system status and short, isolated follow-up context."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import re
import threading
import time

from .command_reply import command_reply
from .ha_devices import normalized, status_words, summary_intent
from .model_catalog import catalog_json


BAD = {'unavailable', 'unknown', 'offline'}


def status_intent(text):
    words = status_words(text)
    if re.search(r'\b(turn|switch|set|dim|unlock|lock|restart|reboot|enable|disable|delete|stop|start|do not|don t)\b', words):
        return None
    if re.fullmatch(r'(?:(?:yes|yeah|yep|okay|ok) )?(?:check|check again|check that|check it|check them)', words):
        return dict(kind='recheck')
    if re.fullmatch(r'(?:(?:do we|do you) know )?why (?:it is|it s|is it|that is|that s|is that|they are|are they) (?:unavailable|offline|unknown)|'
                    r'why|why is that|what happened to it|tell me more|more details|(?:tell me more|more details|what do you know) (?:about it|about that)', words):
        return dict(kind='explain')
    category = summary_intent(text)
    if category:
        return dict(kind='summary', category=category)
    if re.fullmatch(r'(?:what|which) (?:brain|models?|ai|llm) (?:are you|is adam) (?:using|running|serving)|'
                    r'what are you running on|are you (?:using|running on) (?:the )?(?:gpu|pi|raspberry pi)|'
                    r'(?:what|which) models? (?:are running|are loaded|do you use)|'
                    r'(?:what|which) (?:speech|recognition|voice) model (?:are you using|do you use)|'
                    r'why are you (?:using|on) (?:the )?pi', words):
        topic = ('voice' if 'voice' in words else 'recognition' if re.search(r'\b(speech|recognition)\b', words)
                 else 'why' if words.startswith('why ') else 'loaded' if 'loaded' in words else 'all' if 'models' in words else 'brain')
        return dict(kind='models', topic=topic)
    if re.fullmatch(r'(?:which|what) (?:one|ones)(?: (?:is|are|was|were) (?:unavailable|offline|unknown))?|'
                    r'what is the unavailable (?:one|light|device|speaker)|'
                    r'(?:is it|are they) (?:back )?(?:online|available|working)|'
                    r'(?:what|how) about (?:it|them)', words):
        return dict(kind='followup', requested_bad=bool(re.search(r'\b(unavailable|offline|unknown)\b', words)))
    match = re.fullmatch(r'(?:which|what|are any|show) (?:of )?(?:the )?(speakers?|units?|devices?|lights?) '
                         r'(?:(?:are|is) )?(offline|unavailable|unknown|online)|'
                         r'which (speakers?|units?) are connected', words)
    if match:
        subject = match[1] or match[3]
        return dict(kind='speakers' if subject.startswith(('speaker', 'unit')) else 'availability',
                    category='lights' if subject.startswith('light') else 'devices', state=match[2] or 'online')
    match = re.fullmatch(r'(?:which|what) (devices?|speakers?|lights?) (?:was|were) (?:offline|unavailable)', words)
    if match:
        return dict(kind='history', subject=match[1])
    match = re.fullmatch(r'(?:what|how) about (.+)', words)
    if match:
        return dict(kind='follow_named', name=match[1])
    match = re.fullmatch(r'is (.+?) (?:back )?(online|offline|available|unavailable|working)', words)
    if match:
        return dict(kind='named', name=match[1])
    return None


def names(rows):
    shown = [r['name'] for r in rows[:3]]
    if not shown:
        return ''
    text = ' and '.join(shown) if len(shown) < 3 else ', '.join(shown)
    return text + (f', and {len(rows)-3} more' if len(rows) > 3 else '')


class StatusQueries:
    def __init__(self, fleet, clock=time.monotonic):
        self.fleet, self.clock = fleet, clock
        self.models = None  # Bound to the server's existing ModelServices instance.
        self.lock = threading.RLock()
        self.contexts, self.latest = {}, {}
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='status')
        self.slots = threading.BoundedSemaphore(4)
        self.closed = False
        self.model_cache = None

    def key(self, entry):
        if entry.get('trigger') in ('wake', 'followup') and entry.get('unit_id'):
            return 'speaker:' + entry['unit_id']
        return entry.get('context_key')

    def invalidate(self, entry):
        key = self.key(entry)
        with self.lock:
            self.contexts.pop(key, None)
            if key:
                self.latest[key] = entry.get('id')

    def submit(self, entry, job):
        if entry.get('trigger') not in ('wake', 'followup', 'text') or job.get('status') != 'done':
            return False
        intent = status_intent(job['text'])
        key = self.key(entry)
        prior = self.fleet.dialogue.previous(entry)
        with self.lock:
            now = self.clock()
            self.contexts = {k:v for k,v in self.contexts.items() if now-v['time'] < 90}
            previous = self.contexts.get(key)
            if prior and self.latest.get(key) != prior.get('id'):
                previous = None
            reply = prior.get('reply', {})
            if not previous and reply.get('source') == 'Home Assistant' and reply.get('entity_id') and reply.get('source_url'):
                previous = self.context([dict(id=reply['entity_id'], name=reply.get('name', reply['entity_id']),
                                              state=reply.get('state', 'unknown'))], 'ha', reply['source_url'])
            if key:
                self.latest[key] = entry['id']
                self.contexts.pop(key, None)
            # Bound tab/speaker bookkeeping, including unanswered unrelated commands.
            while len(self.latest) > 128:
                old = next(iter(self.latest)); self.latest.pop(old); self.contexts.pop(old, None)
        if intent and intent['kind'] in ('follow_named', 'explain') and not previous and entry.get('conversation_history'):
            return False
        if not intent:
            return False
        if self.closed or not self.slots.acquire(blocking=False):
            entry['reply'] = dict(status='error', text='Status checks are busy. Please try again shortly.')
            return True
        entry['reply'] = dict(status='working', text='')
        try:
            self.pool.submit(self.run, entry, intent, previous, key)
        except RuntimeError:
            self.slots.release()
            entry['reply'] = dict(status='error', text='ADAM is restarting. Please try again shortly.')
        return True

    def run(self, entry, intent, previous, key):
        try:
            if time.time()-entry['started'] > 30:
                raise ValueError('expired')
            result, context = self.answer(intent, previous)
            result.setdefault('checked_at', time.time())
            # Full structured details stay on the page; speech remains short.
            if len(result.get('text', '')) > 220:
                result['text'] = result['text'][:217].rsplit(' ', 1)[0].rstrip(' ,;.') + '.'
            with self.lock:
                if key and context and self.latest.get(key) == entry['id']:
                    self.contexts[key] = dict(context, time=self.clock())
            entry['reply'] = command_reply(entry, result, self.fleet.queries.deliver)
        except Exception:
            entry['reply'] = dict(status='unavailable', text='I could not check that status just now. Please try again. No devices were changed.')
        finally:
            self.slots.release()

    def ha_rows(self, category='devices', origin=None, ids=None):
        service = self.fleet.home_assistant
        with service.lock:
            settings, revision = service.settings(), service.revision
        if not settings.enabled:
            return None, 'Home Assistant is disabled. Enable it to check device status.', None
        if origin and origin != settings.url:
            return None, 'Home Assistant settings changed. Please ask the full question again.', None
        inventory = service.inventory.rows(settings)
        targets = (service._summary_targets(inventory, category) if category != 'devices' else
                   [r for r in inventory if r['imported'] and r['enabled']])
        if ids is not None:
            targets = [r for r in targets if r['entity_id'] in ids]
        live = {r['entity_id']:r['state'] for r in service.client.entities(settings, all_domains=True)}
        with service.lock:
            if revision != service.revision or not service.settings().enabled:
                return None, 'Home Assistant settings changed. Please ask again.', None
        rows = [dict(id=r['entity_id'], name=r['name'], state=live.get(r['entity_id'], 'unavailable'),
                     spoken_names=r.get('spoken_names', [r['name']])) for r in targets]
        return rows, None, settings.url

    @staticmethod
    def context(rows, kind, origin=None):
        bad = [r for r in rows if r['state'] in BAD]
        return dict(kind=kind, ids=[r['id'] for r in rows], focus=[r['id'] for r in bad or rows], origin=origin,
                    states={r['id']:r['state'] for r in rows})

    @staticmethod
    def result(rows, text, source='Home Assistant'):
        return dict(status='answered', text=text, source=source,
                    details=[f"{r['name']}: {r['state']}" for r in rows[:100]],
                    devices=[dict(id=r['id'], name=r['name'], state=r['state']) for r in rows[:100]])

    def speakers(self):
        return [dict(id=u['id'], name=u['name'], state='online' if u['online'] else 'offline', seen=u['seen'])
                for u in self.fleet.state.units()]

    def answer(self, intent, previous):
        kind = intent['kind']
        if kind == 'recheck':
            if not previous:
                return dict(status='unsupported', text='What should I check? Name a device or ask which lights are on.', needs_clarification=True), None
            if previous['kind'] == 'ha' and previous.get('category') in ('lights', 'doors', 'windows', 'openings', 'locks', 'leaks'):
                if previous['origin'] != self.fleet.home_assistant.settings().url:
                    return dict(status='unsupported', text='Home Assistant changed. Please ask the full question again.'), None
                return self.answer(dict(kind='summary', category=previous['category']), previous)
            return self.answer(dict(kind='followup'), previous)
        if kind == 'explain':
            return self.explain(previous)
        if kind == 'models':
            return self.model_answer(intent.get('topic', 'brain')), None
        if kind in ('followup', 'history', 'follow_named'):
            if not previous:
                text = ('I do not have an availability history yet. Ask which devices or speakers are offline now.'
                        if kind == 'history' else 'Which device do you mean? Ask about lights or offline speakers first, or use its name.')
                return dict(status='unsupported', text=text), None
            if kind == 'history' and ((intent['subject'].startswith('speaker') and previous['kind'] != 'speakers') or
                                      (intent['subject'].startswith('light') and previous.get('category') != 'lights')):
                return dict(status='unsupported', text='That was a different group of devices. Please ask which devices or speakers are offline now.'), None
            if kind == 'history' and not any(s in BAD for s in previous.get('states', {}).values()):
                return dict(status='answered', text='No devices were reported offline or unavailable in that recent answer. I do not have earlier outage history.'), None
            if kind == 'follow_named':
                if previous['kind'] == 'speakers':
                    rows = self.speakers(); origin = None
                else:
                    rows, error, origin = self.ha_rows(previous.get('category', 'devices'), origin=previous['origin'])
                    if error:
                        return dict(status='unavailable', text=error), None
                target = intent['name']
                rows = [r for r in rows if any(normalized(n) == target or
                        normalized(n) in (target+' light', target+' lights', target+' speaker')
                        for n in r.get('spoken_names', [r['name']]))]
                if len(rows) != 1:
                    return dict(status='unsupported', text='Please use the exact name of one device or speaker.'), None
                context = self.context(rows, previous['kind'], origin)
                context['category'] = previous.get('category', 'devices')
                return self.result(rows, f"{rows[0]['name']} is {rows[0]['state']}.",
                                   'ADAM speakers' if previous['kind']=='speakers' else 'Home Assistant'), context
            had_bad = any(s in BAD for s in previous.get('states', {}).values())
            target_ids = previous['ids'] if intent.get('requested_bad') and not had_bad else previous['focus']
            if previous['kind'] == 'speakers':
                rows = [r for r in self.speakers() if r['id'] in target_ids]
                origin, error, source = None, None, 'ADAM speakers'
            else:
                rows, error, origin = self.ha_rows(origin=previous['origin'], ids=target_ids)
                source = 'Home Assistant'
            if error:
                return dict(status='unavailable', text=error), None
            if not rows:
                return dict(status='unsupported', text='Those devices are no longer in the enabled inventory. Please ask the full question again.'), None
            if intent.get('requested_bad') and not had_bad:
                bad_rows = [r for r in rows if r['state'] in BAD]
                if not bad_rows:
                    return self.result(rows, 'None of the devices from that answer are unavailable or unknown now.', source), self.context(rows, previous['kind'], origin)
                rows = bad_rows
            sentences = []
            for row in rows[:3]:
                old = previous.get('states', {}).get(row['id'])
                if old in BAD and row['state'] not in BAD and previous['kind'] != 'speakers':
                    sentences.append(f"{row['name']} is available again and is {row['state']}.")
                else:
                    sentences.append(f"{row['name']} is {row['state']}.")
            context = self.context(rows, previous['kind'], origin)
            context['category'] = previous.get('category', 'devices')
            concise = names(rows)+'.' if intent.get('requested_bad') and had_bad and all(r['state'] in BAD for r in rows) else ' '.join(sentences)
            return self.result(rows, concise, source), context
        if kind == 'summary':
            service = self.fleet.home_assistant
            with service.lock:
                settings, revision = service.settings(), service.revision
            if not settings.enabled:
                return dict(status='unavailable', text='Home Assistant is disabled. Enable it to check device status.'), None
            result = service._answer_summary(intent['category'], settings)
            if not service.valid(revision):
                return dict(status='unavailable', text='Home Assistant settings changed. Please ask again.'), None
            rows = [dict(id=r['entity_id'], name=r['name'], state=r['state']) for r in result.get('devices', [])]
            context = self.context(rows, 'ha', settings.url) if rows else None
            if context:
                context['category'] = intent['category']
                active = [r['id'] for r in rows if r['state'] in ('on', 'open', 'unlocked', 'wet')]
                if intent['category'] == 'lights':
                    context['light_group'] = active
                    context['revision'] = revision
                # Status follow-ups prefer the unavailable item, but a control
                # must disambiguate when the summary mentioned both active and
                # unavailable devices.
                context['action_focus'] = [r['id'] for r in rows if r['id'] in active or r['state'] in BAD] or context['focus']
                if active and not any(r['state'] in BAD for r in rows):
                    context['focus'] = active
            return result, context
        if kind == 'speakers':
            rows = self.speakers()
            selected = [r for r in rows if (r['state'] == 'online') == (intent['state'] == 'online')]
            label = 'online' if intent['state'] == 'online' else 'offline'
            text = f"{names(selected)} {'is' if len(selected)==1 else 'are'} {label}." if selected else f'No registered speakers are {label}.'
            result = self.result(rows, text, 'ADAM speakers')
            result['details'] = [f"{r['name']}: {r['state']}; " +
                                 (f"last seen {max(0, int(time.time()-r['seen']))} seconds ago" if r['seen'] else 'not seen yet') for r in rows]
            return result, self.context(selected or rows, 'speakers')
        if kind == 'named':
            speakers = [r for r in self.speakers() if normalized(r['name']) == intent['name']]
            if speakers:
                return self.result(speakers, ' '.join(f"{r['name']} is {r['state']}." for r in speakers), 'ADAM speakers'), self.context(speakers, 'speakers')
        rows, error, origin = self.ha_rows(intent.get('category', 'devices'))
        if error:
            return dict(status='unavailable', text=error), None
        if kind == 'named':
            rows = [r for r in rows if intent['name'] in {normalized(n) for n in r['spoken_names']}]
            if len(rows) != 1:
                return dict(status='unsupported', text='Please use the exact name of one enabled device or speaker.'), None
            return self.result(rows, f"{rows[0]['name']} is {rows[0]['state']} in Home Assistant."), self.context(rows, 'ha', origin)
        want_online = intent['state'] == 'online'
        selected = [r for r in rows if (r['state'] not in BAD) == want_online]
        if want_online:
            text = f'{len(selected)} enabled devices have a known state in Home Assistant.'
        else:
            text = 'No enabled devices are unavailable or unknown.' if not selected else \
                f"Unavailable or unknown: {names(selected)}."
        context = self.context(selected or rows, 'ha', origin)
        context['category'] = intent.get('category', 'devices')
        return self.result(selected, text), context

    def explain(self, previous):
        if not previous or len(previous['focus']) != 1:
            return dict(status='unsupported', text='Which device do you mean? Ask about one device first.', needs_clarification=True), previous
        target = previous['focus'][0]
        if previous['kind'] == 'speakers':
            rows = [r for r in self.speakers() if r['id'] == target]
            if not rows:
                return dict(status='unsupported', text='That speaker is no longer registered.'), None
            row = rows[0]
            text = (f"{row['name']} is responding now." if row['state'] == 'online' else
                    f"{row['name']} stopped checking in. I cannot tell whether it lost power or its network connection.")
            result = self.result(rows, text, 'ADAM speakers')
            result['details'].append(f"Last check-in: {max(0, int(time.time()-row['seen']))} seconds ago." if row['seen'] else 'No check-in has been recorded.')
            return result, self.context(rows, 'speakers')
        rows, error, origin = self.ha_rows(origin=previous['origin'], ids=[target])
        if error:
            return dict(status='unavailable', text=error), None
        if not rows:
            return dict(status='unsupported', text='That device is no longer enabled. Please ask about another device.'), None
        service = self.fleet.home_assistant
        with service.lock:
            settings, revision = service.settings(), service.revision
        if settings.url != origin:
            return dict(status='unavailable', text='Home Assistant changed. Please ask again.'), None
        current = service.client.device_state(settings, target)
        if not service.valid(revision):
            return dict(status='unavailable', text='Home Assistant settings changed. Please ask again.'), None
        row = dict(rows[0], state=current['state'])
        details = current.get('diagnostics', [])
        if row['state'] not in BAD:
            text = f"{row['name']} now reports {row['state']}. I do not have the cause of its earlier outage."
        else:
            text = f"Home Assistant reports {row['name']} {row['state']}, but has not provided a cause."
        result = self.result([row], text)
        result['details'].extend(details)
        context = self.context([row], 'ha', origin)
        context['category'] = previous.get('category', 'devices')
        return result, context

    def model_answer(self, topic='brain'):
        chat = self.fleet.conversation.public()
        speech = self.models.public() if self.models else {}
        last = chat['last_provider']
        selected = chat['remote_model'] if chat['mode'] != 'local' else chat['local_model']
        host_label = 'GPU' if chat['mode'] != 'local' else 'Pi'
        model_url = chat['remote_url'] if chat['mode'] != 'local' else chat['local_url']
        loaded_names = None
        details = [f"Conversation preference: {chat['mode']}; GPU: {chat['remote_model']}; Pi: {chat['local_model']}.",
                   f"Last conversation provider: {last or 'no requests since restart/settings change'}."]
        # Read metadata only. Never generate, download or load a model for status.
        if chat['enabled'] and topic not in ('voice', 'recognition'):
            cache_key = (model_url, selected)
            with self.lock:
                cache = self.model_cache
            if not cache or cache['key'] != cache_key or self.clock()-cache['time'] > 10:
                async def metadata():
                    return await asyncio.gather(*(catalog_json(model_url+p, self.fleet.conversation.transport)
                                                  for p in ('/api/tags', '/api/ps')))
                try:
                    tags, loaded = asyncio.run(metadata())
                    tag = next((m for m in tags.get('models', []) if m.get('name') == selected), {})
                    aliases = [m['name'] for m in tags.get('models', []) if m.get('name') != selected and tag.get('digest') and m.get('digest') == tag['digest']]
                    resolved = aliases[0] if selected == 'adam:latest' and len(aliases) == 1 else selected
                    loaded_names = [m.get('name', '?') for m in loaded.get('models', [])][:20]
                    cache = dict(key=cache_key, time=self.clock(), resolved=resolved, loaded=loaded_names)
                except Exception:
                    cache = dict(key=cache_key, time=self.clock(), resolved=selected, loaded=None)
                with self.lock:
                    self.model_cache = cache
            selected = cache['resolved']
            loaded_names = cache['loaded']
            details.append(host_label+' loaded models: ' + (', '.join(loaded_names) or 'none') if loaded_names is not None else host_label+' loaded-model check unavailable; configured preference does not prove availability.')
            details.append(f"Selected {host_label} model: {cache_key[1]}; current resolved name: {selected}.")
            details.append(f'Model metadata checked {max(0, int(self.clock()-cache["time"]))} seconds ago.')
        for kind, title in (('stt', 'Recognition'), ('tts', 'Voice')):
            profile = speech.get(kind, {})
            model = profile.get('model') if profile.get('mode') != 'local' else ('base.en' if kind == 'stt' else 'Piper')
            details.append(f"{title}: {profile.get('mode', 'local')}; selected model: {model or 'local default'}; last provider: {profile.get('last_provider') or 'not used yet'}.")
        retry = max(chat['retry_in_seconds'], self.fleet.conversation.gpu_failover.remaining() if self.fleet.conversation.gpu_failover else 0)
        if retry:
            details.append(f'GPU retry cooldown: {retry} seconds. A cooldown does not prove the whole computer is offline.')
        if not chat['enabled']:
            text = 'Conversation is disabled. Home controls and status checks still run directly on the Pi.'
        elif last in ('remote', 'local'):
            location = 'GPU' if last == 'remote' else 'Pi'
            model = selected if last == 'remote' else chat['local_model']
            text = f'My last conversation used the {location}. The selected model there is {model}. This status answer comes directly from ADAM.'
        else:
            text = f"I prefer {'the Pi' if chat['mode']=='local' else 'the GPU'}, using {selected}. I have no successful recent conversation route to report."
        if topic == 'recognition':
            profile = speech.get('stt', {})
            last_stt = {'remote':'the GPU', 'local':'the Pi'}.get(profile.get('last_provider'), 'no successful recent provider')
            preferred = profile.get('model', 'not configured') if profile.get('mode') == 'prefer_remote' else 'base.en on the Pi'
            text = f'Recognition last used {last_stt}. Its preferred model is {preferred}. The local recognition model is base.en.'
        elif topic == 'voice':
            profile = speech.get('tts', {})
            text = ('My speaking voice runs locally using Piper.' if profile.get('mode', 'local') == 'local' else
                    f"My voice prefers the remote model {profile.get('model') or 'not configured'}, with a local voice fallback.")
        elif topic == 'why' and retry:
            text = f'GPU retries are paused for {retry} seconds after a service failure. Requests can use the Pi fallback. This does not prove the GPU computer is offline.'
        elif topic == 'why' and chat['mode'] == 'local':
            text = f'Pi-only processing is selected. The local conversation model is {chat["local_model"]}.'
        elif topic == 'loaded' and chat['enabled']:
            text = (f'Loaded on the {host_label}: ' + (', '.join(loaded_names) or 'no conversation models') + '.' if loaded_names is not None else
                    f'I cannot confirm loaded models on the {host_label}. The configured conversation model is {selected}.')
        elif topic == 'all' and chat['enabled']:
            recognition = speech.get('stt', {})
            recognition_route = 'GPU' if recognition.get('mode') == 'prefer_remote' else 'Pi'
            last_route = {'remote':'GPU', 'local':'Pi'}.get(last, 'not used yet')
            text = f'Conversation is configured for {selected}; last route: {last_route}. Recognition prefers the {recognition_route}. Check the status details for model IDs and loaded models.'
        return dict(status='answered', text=text, source='ADAM model services', details=details)

    def close(self):
        self.closed = True
        self.pool.shutdown(wait=True, cancel_futures=True)
