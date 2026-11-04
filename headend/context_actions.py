"""Resolve narrowly phrased follow-up controls to stable, enabled HA identities."""
import re

from .conversation import question
from .ha_devices import MAX_COMPOUND_CLAUSES, has_negation, normalized, strip_wake_and_polite


NUMERIC = {'level', 'brightness', 'speed', 'position', 'volume', 'temperature',
           'heating_temperature', 'cooling_temperature', 'value', 'color_temperature'}


def resolve(fleet, entry, text):
    if has_negation(text):
        return None
    previous = fleet.dialogue.previous(entry)
    reply = previous.get('reply', {})
    successful = reply.get('answer_status', reply.get('status')) in ('answered', 'sent')
    prior = reply if successful and reply.get('entity_id') and reply.get('action') != 'status' else {}
    raw = strip_wake_and_polite(question(text))
    words = normalized(raw)
    plural = r'(?:those|them|these|those lights|these lights|all of them|all of those)'
    group_toggle = re.fullmatch(r'(?:turn|switch) '+plural+r' (on|off)|(?:turn|switch) (on|off) '+plural, words)
    if group_toggle:
        return resolve_light_group(fleet, entry, previous, group_toggle[1] or group_toggle[2], successful)
    pronoun = r'(?:it|that|that one|this one|that light|that device)'
    toggle = re.fullmatch(r'(?:turn|switch) '+pronoun+r' (on|off)|(?:turn|switch) (on|off) '+pronoun, words)
    numeric = re.fullmatch(r'(?:make that|(?:set|dim) '+pronoun+r' to) (-?\d+(?:\.\d+)?)(?: degrees| percent|\s*%)?', raw, re.I)
    correction = re.fullmatch(r'(?:actually|no i meant|i meant) (?:the )?(.+)', words)
    pending = previous.get('pending_action')
    candidate = re.sub(r'^(?:the )', '', words)
    if not (toggle or numeric or correction or pending):
        return None
    service = fleet.home_assistant
    with service.lock:
        settings, revision = service.settings(), service.revision
        rows = [r for r in service.inventory.rows(settings) if r['imported'] and r['enabled'] and r['present']]
    def error(message):
        return dict(status='unsupported', text=message, needs_clarification=True)
    if not settings.enabled:
        return error('Home Assistant is disabled. No command was sent.')
    if pending and not (toggle or numeric or correction):
        if pending['origin'] != settings.url or pending['revision'] != revision:
            return error('Device settings changed. Please say the full command again.')
        matches = [r for r in rows if r['entity_id'] in pending['ids'] and named(r, candidate)]
        if len(matches) == 1:
            action, value, targets = pending['action'], pending['value'], matches
        else:
            # A different question cancels the pending control rather than being
            # interpreted as an affirmative answer to an old action.
            return None
    elif correction:
        if not prior or prior.get('action') not in NUMERIC | {'on', 'off'}:
            return error('Which action should I apply to that device?')
        action, value = prior['action'], prior.get('value')
        targets = [r for r in rows if named(r, correction[1])]
        entry['correction_from'] = prior.get('name') if prior.get('entity_id') not in [r['entity_id'] for r in targets] else None
    else:
        key = fleet.status_queries.key(entry)
        with fleet.status_queries.lock:
            context = fleet.status_queries.contexts.get(key)
            if context and (fleet.status_queries.clock()-context['time'] >= 90 or
                            fleet.status_queries.latest.get(key) != previous.get('id')):
                context = None
        if prior:
            ids, origin = [prior['entity_id']], prior.get('source_url')
        elif reply.get('entity_id') and successful:
            ids, origin = [reply['entity_id']], reply.get('source_url')
        elif context and context['kind'] == 'ha':
            ids, origin = context.get('action_focus', context['focus']), context['origin']
        else:
            ids, origin = [], settings.url
        if origin != settings.url:
            return error('Home Assistant changed. Please say the full command again.')
        targets = [r for r in rows if r['entity_id'] in ids]
        if toggle:
            action, value = toggle[1] or toggle[2], None
        elif numeric:
            value = float(numeric[1])
            if raw.lower().startswith('make that'):
                if not prior or prior.get('action') not in NUMERIC:
                    return error('Which setting should I change? Say the device and setting, such as heating or cooling.')
                action = prior['action']
            else:
                action = 'brightness' if raw.lower().startswith('dim ') else 'temperature' if 'degrees' in raw.lower() else 'level'
        else:
            return None
    if len(targets) != 1:
        candidates = targets or ([] if correction else rows)
        entry['pending_action'] = dict(action=action, value=value, ids=[r['entity_id'] for r in candidates],
                                       origin=settings.url, revision=revision)
        names = ', '.join(r['name'] for r in candidates[:3])
        return error('Which device do you mean' + (': '+names if names else '') + '? No command was sent.')
    if prior and prior.get('source_url') != settings.url:
        return error('Home Assistant changed. Please say the full command again.')
    entry['context_action'] = dict(entity_id=targets[0]['entity_id'], action=action, value=value,
                                  origin=settings.url, revision=revision)
    return None


def resolve_light_group(fleet, entry, previous, action, successful):
    """Use the exact recent light-summary group; never expand to today's inventory."""
    def error(message):
        return dict(status='unsupported', text=message, needs_clarification=True)
    with fleet.status_queries.lock:
        key = fleet.status_queries.key(entry)
        context = fleet.status_queries.contexts.get(key)
        if (not successful or not context or context.get('kind') != 'ha'
                or 'light_group' not in context
                or fleet.status_queries.latest.get(key) != previous.get('id')
                or fleet.status_queries.clock()-context['time'] >= 90):
            return error('Which lights do you mean? Ask which lights are on first, or name the lights. No commands were sent.')
        context = dict(context)
    if any(state in ('unavailable', 'unknown', 'offline') for state in context['states'].values()):
        return error('Some lights in that summary were unavailable. Please name the lights to change. No commands were sent.')
    ids = context['light_group']
    if not ids:
        return error('No lights were reported on in that summary. Please name the lights to change. No commands were sent.')
    if len(ids) > MAX_COMPOUND_CLAUSES:
        return error(f'That summary includes more than {MAX_COMPOUND_CLAUSES} lights on. Please name up to {MAX_COMPOUND_CLAUSES} lights at a time. No commands were sent.')
    service = fleet.home_assistant
    with service.lock:
        settings, revision = service.settings(), service.revision
        if not settings.enabled or context['origin'] != settings.url or context['revision'] != revision:
            return error('Device settings changed. Please ask which lights are on again. No commands were sent.')
        rows = [r for r in service.inventory.rows(settings) if r['entity_id'] in ids
                and r['imported'] and r['enabled'] and r['present']]
        if len(rows) != len(ids):
            return error('A light from that summary is no longer available in the inventory. Please ask again. No commands were sent.')
        entry['context_action'] = dict(entity_ids=list(ids), action=action, value=None,
                                      origin=settings.url, revision=revision)
    return None


def named(row, words):
    return any(words == normalized(n) or normalized(n) in (words+' light', words+' lights')
               for n in row['spoken_names'])
