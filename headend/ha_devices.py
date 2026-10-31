"""Bounded Home Assistant controls, sensor reads, and exact-name speech matching.

The browser receives controls, never arbitrary service-call authority. Each action
is rebuilt from the entity's current attributes before a request is sent to HA.
"""
import math
import re

ENTITY_PATTERN = r'[a-z_]+\.[a-z0-9_]+'

CURATED_SENSOR_CLASSES = {
    'temperature', 'humidity', 'aqi', 'pm25', 'pm10',
    'carbon_dioxide', 'carbon_monoxide', 'volatile_organic_compounds',
    'power', 'energy',
}
CURATED_BINARY_CLASSES = {
    'door', 'garage_door', 'window', 'opening', 'lock',
    'moisture', 'problem', 'smoke', 'gas', 'motion', 'occupancy',
}
DIAGNOSTIC_SUFFIXES = (
    '_led', '_auto_update_enabled', '_auto_off_enabled', '_emergency_heat',
    '_one_touch_locking', '_rssi', '_signal_strength', '_uptime', '_ip', '_diagnostic',
)
SPOKEN_UNITS = {
    '°f': 'degrees Fahrenheit',
    'f': 'degrees Fahrenheit',
    '°c': 'degrees Celsius',
    'c': 'degrees Celsius',
    '%': 'percent',
    'w': 'watts',
    'kw': 'kilowatts',
    'kwh': 'kilowatt hours',
    'wh': 'watt hours',
    'ppm': 'parts per million',
    'ppb': 'parts per billion',
    'µg/m³': 'micrograms per cubic meter',
    'ug/m3': 'micrograms per cubic meter',
}
BINARY_CLASS_STATES = {
    'door': ('open', 'closed'),
    'garage_door': ('open', 'closed'),
    'window': ('open', 'closed'),
    'opening': ('open', 'closed'),
    'lock': ('unlocked', 'locked'),
    'moisture': ('wet', 'dry'),
    'motion': ('detected', 'clear'),
    'occupancy': ('occupied', 'clear'),
    'problem': ('problem detected', 'clear'),
    'smoke': ('smoke detected', 'clear'),
    'gas': ('gas detected', 'clear'),
}


def normalized(text):
    return re.sub(r'[^\w]+', ' ', text.casefold()).strip()


def attributes_for_inventory(attributes):
    keys = ('supported_features', 'supported_color_modes', 'brightness', 'color_temp_kelvin',
            'min_color_temp_kelvin', 'max_color_temp_kelvin', 'percentage', 'current_position',
            'temperature', 'current_temperature', 'target_temp_low', 'target_temp_high',
            'min_temp', 'max_temp', 'target_temp_step', 'hvac_modes', 'options', 'min', 'max',
            'step', 'unit_of_measurement', 'volume_level', 'device_class', 'code_format')
    result = {}
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            result[key] = value
        elif isinstance(value, str):
            result[key] = value[:150]
        elif isinstance(value, list):
            result[key] = [v[:100] for v in value[:100] if isinstance(v, str)]
    return result


def controls(entity_id, attrs):
    domain = entity_id.split('.')[0]
    a = attributes_for_inventory(attrs)
    features = int(a.get('supported_features', 0))
    result = []

    def add(key, label, service, field=None, **extra):
        result.append(dict(key=key, label=label, service=service, field=field, **extra))

    def power(on=True, off=True):
        if on: add('on', 'Turn on', 'turn_on', expected='on')
        if off: add('off', 'Turn off', 'turn_off', expected='off')

    if domain in ('light', 'switch', 'input_boolean'):
        power()
    elif domain == 'fan':
        power(bool(features & 32), bool(features & 16))
        if features & 1: add('speed', 'Speed %', 'set_percentage', 'percentage', kind='number', min=0, max=100, step=1)
    elif domain == 'cover':
        for bit, key, label, state in [(1, 'open', 'Open', 'open'), (2, 'close', 'Close', 'closed'), (8, 'stop', 'Stop', None)]:
            if features & bit: add(key, label, key+'_cover', expected=state)
        if features & 4: add('position', 'Open %', 'set_cover_position', 'position', kind='number', min=0, max=100, step=1)
    elif domain == 'lock':
        # PIN-based locks need a separate credential flow; do not collect codes here.
        if not a.get('code_format'):
            add('lock', 'Lock', 'lock', expected='locked')
            add('unlock', 'Unlock', 'unlock', expected='unlocked')
    elif domain == 'climate':
        if features & 1:
            add('temperature', 'Target temperature', 'set_temperature', 'temperature', kind='number',
                min=a.get('min_temp', 7), max=a.get('max_temp', 35), step=a.get('target_temp_step', .5))
        if features & 2:
            add('temperature_range', 'Low / high temperature', 'set_temperature', kind='range',
                min=a.get('min_temp', 7), max=a.get('max_temp', 35), step=a.get('target_temp_step', .5))
        if a.get('hvac_modes'):
            add('mode', 'Mode', 'set_hvac_mode', 'hvac_mode', kind='select', options=a['hvac_modes'])
    elif domain == 'media_player':
        power(bool(features & 128), bool(features & 256))
        for bit, key, service in [(16384, 'play', 'media_play'), (1, 'pause', 'media_pause'), (4096, 'stop', 'media_stop')]:
            if features & bit: add(key, key.title(), service)
        if features & 4: add('volume', 'Volume %', 'volume_set', 'volume_level', kind='number', min=0, max=100, step=1)
    elif domain in ('scene', 'script'):
        add('activate', 'Activate' if domain == 'scene' else 'Run', 'turn_on')
    elif domain in ('button', 'input_button'):
        add('press', 'Press', 'press')
    elif domain in ('select', 'input_select') and a.get('options'):
        add('option', 'Option', 'select_option', 'option', kind='select', options=a['options'])
    elif domain in ('number', 'input_number') and 'min' in a and 'max' in a:
        add('value', 'Value', 'set_value', 'value', kind='number', min=a['min'], max=a['max'], step=a.get('step', 1))
    if domain == 'light':
        modes = set(a.get('supported_color_modes', []))
        if modes & {'brightness', 'color_temp', 'white', 'hs', 'xy', 'rgb', 'rgbw', 'rgbww'}:
            add('brightness', 'Brightness %', 'turn_on', 'brightness_pct', kind='number', min=0, max=100, step=1)
        if 'color_temp' in modes and 'min_color_temp_kelvin' in a and 'max_color_temp_kelvin' in a:
            add('color_temperature', 'Color temperature (K)', 'turn_on', 'color_temp_kelvin', kind='number',
                min=a['min_color_temp_kelvin'], max=a['max_color_temp_kelvin'], step=1)
        if modes & {'hs', 'xy', 'rgb', 'rgbw', 'rgbww'}:
            add('color', 'Color', 'turn_on', 'rgb_color', kind='color')
    return result


def capabilities(entity_id, attrs):
    keys = [c['key'] for c in controls(entity_id, attrs)]
    if not keys and entity_id.split('.')[0] in ('sensor', 'binary_sensor'):
        return ['read']
    result = ['on_off'] if 'on' in keys and 'off' in keys else []
    result += [k for k in keys if k not in ('on', 'off') or 'on_off' not in result]
    # Report color-temperature capability even if this integration omits its bounds.
    if entity_id.startswith('light.') and 'color_temp' in attrs.get('supported_color_modes', []) and 'color_temperature' not in result:
        result.append('color_temperature')
    order = ['on_off', 'brightness', 'color_temperature', 'color']
    return sorted(result, key=lambda k: order.index(k) if k in order else len(order))


def default_import(entity_id, attrs):
    # Configuration and diagnostic entities remain visible for deliberate import.
    domain = entity_id.split('.')[0]
    setting = entity_id.endswith(DIAGNOSTIC_SUFFIXES)
    if setting:
        return False
    if domain == 'sensor':
        return attrs.get('device_class') in CURATED_SENSOR_CLASSES
    if domain == 'binary_sensor':
        return attrs.get('device_class') in CURATED_BINARY_CLASSES
    return bool(controls(entity_id, attrs)) and domain in (
        'light', 'switch', 'fan', 'cover', 'climate', 'lock', 'media_player', 'scene', 'script', 'input_boolean'
    )


def format_entity_state(entity_id: str, name: str, current: str, attrs: dict) -> str:
    domain = entity_id.split('.')[0]
    dev_class = str(attrs.get('device_class', '')).casefold()
    if domain == 'binary_sensor' and dev_class in BINARY_CLASS_STATES and current in ('on', 'off'):
        on_word, off_word = BINARY_CLASS_STATES[dev_class]
        return f'{name} is {on_word if current == "on" else off_word}.'
    if domain == 'sensor':
        unit = str(attrs.get('unit_of_measurement', '')).strip()
        spoken_unit = SPOKEN_UNITS.get(unit.casefold(), unit)
        value_txt = current
        try:
            num = float(current)
            if math.isfinite(num):
                rounded = round(num, 1)
                value_txt = str(int(rounded)) if rounded == int(rounded) else str(rounded)
        except ValueError:
            pass
        suffix = f' {spoken_unit}' if spoken_unit else ''
        return f'{name} is {value_txt}{suffix}.'
    detail = f'{name} is {current}.'
    if 'current_temperature' in attrs:
        detail += f" Current temperature is {attrs['current_temperature']}."
    return detail


def summary_intent(text: str) -> str | None:
    """Match read-only multi-entity status questions (doors/windows, locks, lights, leaks)."""
    if not isinstance(text, str) or len(text) > 300:
        return None
    cleaned = re.sub(r'^\s*(?:hey|okay|ok)[ ,]+(?:jarvis|adam)\b[\s,.!?]*', '', text, flags=re.I)
    cleaned = re.sub(r'^\s*(?:please[ ,]+)?(?:can|could|would)\s+you[ ,]+(?:please[ ,]+)?', '', cleaned, flags=re.I)
    lower = normalized(cleaned)
    if re.search(r'\b(?:do not|don t|dont|never|turn|switch|set|dim)\b', lower):
        return None
    if re.fullmatch(
        r'(?:are any|which|what|how many) (?:of the )?(?:doors?|windows?|doors? or windows?|doors? and windows?) (?:are )?(?:open|closed|ajar)|'
        r'are all (?:of )?(?:the )?(?:doors?|windows?|doors? and windows?|doors? or windows?) (?:closed|shut)|'
        r'check (?:all )?(?:the )?(?:doors?|windows?|doors? and windows?)',
        lower,
    ):
        if 'window' in lower and 'door' not in lower:
            return 'windows'
        if 'door' in lower and 'window' not in lower:
            return 'doors'
        return 'openings'
    if re.fullmatch(
        r'(?:are all|are the) (?:of )?(?:the )?(?:doors?|locks?) locked|'
        r'(?:are any|which|what) (?:of the )?(?:doors?|locks?) (?:are )?unlocked|'
        r'check (?:all )?(?:the )?locks?',
        lower,
    ):
        return 'locks'
    if re.fullmatch(
        r'(?:are any|which|what|how many) (?:of the )?lights? (?:are )?on|'
        r'are all (?:of )?(?:the )?lights? off|'
        r'check (?:all )?(?:the )?lights?',
        lower,
    ):
        return 'lights'
    if re.fullmatch(
        r'(?:are there any|any|check for) (?:water )?leaks?',
        lower,
    ):
        return 'leaks'
    return None


def _supports_metric(metric: str, target_raw: str, entity_id: str, attrs: dict) -> bool:
    if metric in target_raw:
        return True
    domain = entity_id.split('.')[0] if entity_id else ''
    dev_class = str((attrs or {}).get('device_class', '')).casefold()
    if metric == 'temperature':
        return domain == 'climate' or dev_class == 'temperature' or 'current_temperature' in (attrs or {}) or str((attrs or {}).get('unit_of_measurement', '')).casefold() in ('°f', '°c', 'f', 'c')
    if metric == 'humidity':
        return dev_class == 'humidity'
    if metric in ('air quality', 'co2', 'carbon dioxide'):
        return dev_class in ('aqi', 'pm25', 'pm10', 'carbon_dioxide', 'carbon_monoxide', 'volatile_organic_compounds')
    if metric in ('power', 'energy'):
        return dev_class in ('power', 'energy')
    if metric == 'battery':
        return dev_class == 'battery'
    return True


def intent(text, name, entity_id='', attrs=None):
    text = re.sub(r'^\s*(?:hey|okay|ok)[ ,]+(?:jarvis|adam)\b[\s,.!?]*', '', text, flags=re.I)
    text = re.sub(r'^\s*(?:please[ ,]+)?(?:can|could|would)\s+you[ ,]+(?:please[ ,]+)?', '', text, flags=re.I)
    # Preserve signed/decimal numbers for parameterized commands. A minus sign
    # must never silently turn a negative setpoint into a positive one.
    numeric_text = re.sub(r'[^\w.\-]+', ' ', text.casefold()).strip().rstrip('.!?')
    numeric_text = re.sub(r'^please\s+|\s+please$', '', numeric_text)
    text = normalized(text)
    text = re.sub(r'^please\s+|\s+please$', '', text)
    if re.search(r'\b(?:do not|don t|dont|never)\b', text):
        return None
    target_raw = normalized(name)
    if not target_raw: return None
    target = r'(?:the )?'+re.escape(target_raw)+r'(?: switch| lights?| smart switch| sensor)?'
    for pattern in (r'(?:turn|switch) (on|off) '+target, r'(?:turn|switch) '+target+r' (on|off)'):
        match = re.fullmatch(pattern, text)
        if match: return match[1], None
    if re.fullmatch(
        r'(?:is|are) '+target+r' (?:on|off|open|closed|locked|unlocked|wet|dry|clear|occupied|detected|running|active)',
        text,
    ) or re.fullmatch(
        r'(?:what is|what s|whats|check|read) (?:the )?(?:status|state|level|value|reading) (?:of|in|for|at) '+target,
        text,
    ) or re.fullmatch(
        r'(?:what is|what s|whats) (?:the )?'+target,
        text,
    ):
        return 'status', None
    metric_match = re.fullmatch(
        r'(?:what is|what s|whats|check|read) (?:the )?(temperature|humidity|air quality|co2|carbon dioxide|power|energy|battery) (?:of|in|for|at) '+target,
        text,
    )
    if metric_match and _supports_metric(metric_match.group(1), target_raw, entity_id, attrs or {}):
        return 'status', None

    # Allow room-first phrasing ("what's the temperature in the bedroom?" / "how warm is the bedroom?")
    # when the entity is named "<Room> Temperature" or "<Room> Humidity".
    for metric, adj in (
        ('temperature', r'(?:warm|hot|cold|cool|chilly)'),
        ('humidity', r'humid'),
        ('air quality', None),
        ('power', None),
    ):
        suffix = ' ' + metric
        if target_raw.endswith(suffix) and len(target_raw) > len(suffix):
            room = re.escape(target_raw[:-len(suffix)].strip())
            if re.fullmatch(
                r'(?:what is|what s|whats|check) (?:the )?'+re.escape(metric)+r' (?:in|of|for|at) (?:the )?'+room,
                text,
            ):
                return 'status', None
            if adj and re.fullmatch(r'how '+adj+r' is (?:it in )?(?:the )?'+room, text):
                return 'status', None

    match = re.fullmatch(r'(open|close|stop|lock|unlock|play|pause|activate|run|press) '+target, text)
    if match: return ('activate' if match[1] == 'run' else match[1]), None
    clean = numeric_text
    numeric_target = r'(?:the )?'+re.escape(re.sub(r'[^\w.\-]+', ' ', name.casefold()).strip())+r'(?: switch| lights?| smart switch)?'
    number = r'(-?\d+(?:\.\d+)?)'
    match = re.fullmatch(r'set '+numeric_target+r' to '+number+r' degrees', clean)
    if match: return 'temperature', float(match[1])
    for key, prefix in [('level', r'set '), ('brightness', r'dim '), ('brightness', r'set (?:the )?brightness of '), ('speed', r'set (?:the )?speed of '),
                        ('position', r'set (?:the )?position of '), ('volume', r'set (?:the )?volume of ')]:
        match = re.fullmatch(prefix+numeric_target+r' to '+number+r'(?: percent)?', clean)
        if match: return key, float(match[1])
    for key, prefix in [('temperature', 'temperature'), ('color_temperature', 'color temperature'), ('value', 'value')]:
        match = re.fullmatch(r'set (?:the )?'+prefix+r' of '+numeric_target+r' to '+number+r'(?: degrees| kelvin)?', clean)
        if match: return key, float(match[1])
    match = re.fullmatch(r'set (?:the )?(mode|option) of '+target+r' to (.+)', text)
    if match: return match[1], match[2]
    match = re.fullmatch(r'set (?:the )?temperature range of '+numeric_target+r' to '+number+r' (?:to|and) '+number+r'(?: degrees)?', clean)
    if match: return 'temperature_range', [float(match[1]), float(match[2])]
    match = re.fullmatch(r'set (?:the )?color of '+target+r' to (red|green|blue|white|orange|yellow|purple)', text)
    if match: return 'color', dict(red='#ff0000', green='#00ff00', blue='#0000ff', white='#ffffff', orange='#ff8000', yellow='#ffff00', purple='#800080')[match[1]]
    return None


def action_payload(entity_id, attrs, action, value):
    control = next((c for c in controls(entity_id, attrs) if c['key'] == action), None)
    if not control: raise ValueError('This action is not supported by the device in Home Assistant.')
    body = dict(entity_id=entity_id)
    kind = control.get('kind')
    if kind in ('number', 'range'):
        values = value if kind == 'range' and isinstance(value, list) and len(value) == 2 else [value]
        if kind == 'range' and len(values) != 2: raise ValueError('Enter both low and high temperatures.')
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)
               or not control['min'] <= v <= control['max'] for v in values):
            raise ValueError(f"Use a value from {control['min']} to {control['max']}.")
        if kind == 'range':
            if values[0] > values[1]: raise ValueError('Low temperature must not exceed high temperature.')
            body.update(target_temp_low=values[0], target_temp_high=values[1])
        else:
            if action == 'temperature' and attrs.get('hvac_mode', attrs.get('_state')) == 'heat_cool':
                raise ValueError('This thermostat is in heat/cool mode. Set its low and high temperature range.')
            if action in ('brightness', 'speed', 'position', 'color_temperature') and value != int(value):
                raise ValueError('Use a whole number for this control.')
            body[control['field']] = value/100 if action == 'volume' else value
    elif kind == 'select':
        matches = [v for v in control['options'] if isinstance(value, str) and normalized(v) == normalized(value)]
        if len(matches) != 1: raise ValueError('Choose one of the options currently offered by Home Assistant.')
        body[control['field']] = matches[0]
    elif kind == 'color':
        if not isinstance(value, str) or not re.fullmatch(r'#[a-fA-F0-9]{6}', value): raise ValueError('Choose a valid color.')
        body[control['field']] = [int(value[i:i+2], 16) for i in (1, 3, 5)]
    elif value is not None:
        raise ValueError('This control does not accept a value.')
    return control, body
