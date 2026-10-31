"""Local names are keyed by HA origin and entity ID, never by display name."""
import json
import time
from .ha_devices import default_import, normalized, intent


class HomeAssistantInventory:
    def __init__(self, state):
        self.state = state
        with state.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS home_assistant_inventory (
                origin TEXT NOT NULL, entity_id TEXT NOT NULL, name TEXT NOT NULL DEFAULT '',
                aliases TEXT NOT NULL DEFAULT '[]', use_default_name INTEGER NOT NULL DEFAULT 1,
                imported INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1,
                touched INTEGER NOT NULL DEFAULT 0,
                present INTEGER NOT NULL DEFAULT 1, updated REAL NOT NULL DEFAULT 0,
                data TEXT NOT NULL, PRIMARY KEY(origin,entity_id))''')

    def legacy(self, settings):
        if not settings.entity_id: return
        row = dict(entity_id=settings.entity_id, name=settings.name, state='unknown',
                   attributes={}, capabilities=['on_off'], controls=[])
        with self.state.db() as db:
            db.execute('''INSERT OR IGNORE INTO home_assistant_inventory
                (origin,entity_id,name,imported,touched,data) VALUES(?,?,?,1,1,?)''',
                (settings.url, settings.entity_id, settings.name, json.dumps(row)))

    def sync(self, settings, rows):
        if len(rows) > 4000: raise ValueError('Home Assistant inventory exceeds 4,000 entities.')
        with self.state.db() as db:
            db.execute('UPDATE home_assistant_inventory SET present=0 WHERE origin=?', (settings.url,))
            for row in rows:
                imported = settings.auto_import and default_import(row['entity_id'], row['attributes'])
                db.execute('''INSERT INTO home_assistant_inventory
                    (origin,entity_id,imported,present,updated,data) VALUES(?,?,?,1,?,?)
                    ON CONFLICT(origin,entity_id) DO UPDATE SET present=1,updated=excluded.updated,data=excluded.data,
                    imported=CASE WHEN touched=0 AND excluded.imported=1 THEN 1 ELSE imported END''',
                    (settings.url, row['entity_id'], int(imported), time.time(), json.dumps(row)))

    def rows(self, settings):
        with self.state.db() as db:
            records = db.execute('SELECT * FROM home_assistant_inventory WHERE origin=?', (settings.url,)).fetchall()
        result = []
        names = {}
        for record in records:
            row = json.loads(record['data'])
            row.update(default_name=row['name'], name=record['name'] or row['name'], custom_name=record['name'],
                       aliases=json.loads(record['aliases']), use_default_name=bool(record['use_default_name']),
                       imported=bool(record['imported']), enabled=bool(record['enabled']),
                       present=bool(record['present']), updated=record['updated'])
            row['spoken_names'] = list(dict.fromkeys([row['name']]+row['aliases']+([row['default_name']] if row['use_default_name'] else [])))
            row['suggested'] = default_import(row['entity_id'], row['attributes'])
            row['domain'] = row['entity_id'].split('.')[0]
            if row['imported'] and row['enabled'] and row['present']:
                for name in {normalized(n) for n in row['spoken_names']}:
                    names.setdefault(name, set()).add(row['entity_id'])
            result.append(row)
        for row in result:
            row['conflicts'] = [n for n in row['spoken_names'] if len(names.get(normalized(n), ())) > 1]
        return sorted(result, key=lambda r: (not r['imported'], r['name'].casefold(), r['entity_id']))

    def save(self, settings, entity_id, options):
        with self.state.db() as db:
            count = db.execute('''UPDATE home_assistant_inventory
                SET name=?,aliases=?,use_default_name=?,imported=?,enabled=?,touched=1 WHERE origin=? AND entity_id=?''',
                (options.name, json.dumps(options.aliases), int(options.use_default_name), int(options.imported),
                 int(options.enabled), settings.url, entity_id)).rowcount
        if not count: raise ValueError('Refresh the inventory before editing this device.')
        return next(r for r in self.rows(settings) if r['entity_id'] == entity_id)

    def matches(self, settings, text):
        if len(text) > 300: return []
        result = []
        for row in self.rows(settings):
            if not row['imported'] or not row['enabled'] or not row['present']: continue
            for name in row['spoken_names']:
                action = intent(text, name, row['entity_id'], row.get('attributes'))
                if action:
                    result.append((row, action))
                    break
        # If multiple read-only temperature/sensor matches occur because both a dedicated
        # sensor ("Bedroom Temperature") and a thermostat ("Bedroom") match a room query,
        # prefer the dedicated sensor while preserving strict ambiguity on all actions.
        if len(result) > 1 and all(act[0] == 'status' for _, act in result):
            sensors = [(r, a) for r, a in result if r['entity_id'].startswith(('sensor.', 'binary_sensor.'))]
            if len(sensors) == 1:
                return sensors
        return result
