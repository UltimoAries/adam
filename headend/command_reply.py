"""Reply policy shared by explicit browser commands and wake requests."""


def command_reply(entry, result, deliver):
    if entry.get('trigger') == 'text':
        if not entry.get('speak_reply'):
            return result
        try:
            return deliver(result, entry['unit_id'])
        except Exception:
            return dict(result, delivery_error='The answer could not be played. The command was not retried.')
    return deliver(result, entry['unit_id'])
