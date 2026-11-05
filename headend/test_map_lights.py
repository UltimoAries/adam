"""Map migration, marker persistence, origin isolation, status, and bounded controls."""
import unittest
import json
import struct
import zlib
from unittest.mock import patch

from . import test_ha_inventory
from .map_lights import MapLights, LAYOUT
from .home_assistant import HomeAssistantSettings, HomeAssistantUnavailable
from .map_layout import load_private_layout


class MapLightTests(unittest.TestCase):
    refresh = test_ha_inventory.InventoryTests.refresh
    save = test_ha_inventory.InventoryTests.save
    tearDown = test_ha_inventory.InventoryTests.tearDown

    def setUp(self):
        test_ha_inventory.InventoryTests.setUp(self)
        self.map = self.app.state.fleet.map_lights
        self.now = 0
        self.map.clock = lambda: self.now

    def view(self):
        response = self.client.get('/api/map/lights')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertNotIn('test-token', response.text)
        self.assertNotIn('ha.local', response.text)
        return response.json()

    def place(self, entity='light.lounge', room='living', version=None):
        return self.client.put('/api/map/lights/'+entity, json={
            'room_id': room, 'version': version or self.view()['version']})

    def room(self, key='living'):
        return next(r for r in self.view()['rooms'] if r['id'] == key)

    def test_persistent_assignment_move_remove_and_identity_after_rename(self):
        self.assertEqual(self.place().status_code, 200)
        self.save('light.lounge', name='Renamed light')
        restored = MapLights(self.app.state.auth, self.service)
        device = next(d for d in restored.snapshot()['devices'] if d['entity_id'] == 'light.lounge')
        self.assertEqual((device['room_id'], device['name']), ('living', 'Renamed light'))
        self.assertEqual(self.place(room='kitchen').status_code, 200)
        self.assertEqual(self.room()['assigned_count'], 0)
        self.assertEqual(self.room('kitchen')['assigned_count'], 1)
        for _ in range(2):
            self.assertEqual(self.place(room=None).status_code, 200)
        self.assertEqual(self.room('kitchen')['state'], 'unassigned')
        self.assertEqual(self.actions, [])

    def test_any_on_lights_room_and_all_off_darken_it(self):
        self.place(); self.place('light.bedroom')
        self.assertEqual(self.room()['state'], 'off')
        self.rows[0]['state'] = 'on'
        self.rows[0]['attributes']['brightness'] = 128
        self.now += 5
        room = self.room()
        self.assertEqual((room['state'], room['on_count'], room['assigned_count']), ('on', 1, 2))
        self.assertAlmostEqual(room['brightness'], 128/255, places=3)
        self.assertEqual(self.room('bedroom')['state'], 'unassigned')
        self.rows[0]['state'] = 'off'; self.now += 5
        self.assertEqual(self.room()['state'], 'off')
        self.assertEqual(self.room()['brightness'], 0)
        self.assertEqual(self.actions, [])

    def test_unavailable_is_not_off_and_missing_live_entity_is_unknown(self):
        self.place(); self.place('light.bedroom')
        self.rows[1]['state'] = 'unavailable'; self.now += 5
        self.assertEqual(self.room()['state'], 'unknown')
        self.rows[0]['state'] = 'on'; self.now += 5
        room = self.room()
        self.assertEqual((room['state'], room['on_count'], room['unknown_count']), ('on', 1, 1))
        self.rows[:] = [r for r in self.rows if r['entity_id'] != 'light.lounge']; self.now += 5
        self.assertEqual(self.room()['unknown_count'], 2)

    def test_cache_failure_clears_old_on_state_and_recovers(self):
        self.rows[0]['state'] = 'on'
        self.place()
        with patch.object(self.service.client, 'entities', wraps=self.service.client.entities) as read:
            self.now += 5
            self.assertEqual(self.room()['state'], 'on')
            self.view(); self.view()
            self.assertEqual(read.call_count, 1)
            read.side_effect = HomeAssistantUnavailable('private connection details')
            self.now += 5
            self.assertEqual(self.room()['state'], 'unknown')
            failed = self.view()
            self.assertIsNone(failed['checked_at'])
            self.assertNotIn('private', failed['error'])
            self.assertEqual(read.call_count, 2)
            read.side_effect = None; self.now += 5
            self.assertEqual(self.room()['state'], 'on')
        self.assertEqual(self.actions, [])

    def test_origin_and_old_browser_versions_cannot_retarget(self):
        original = self.view()['version']; self.place()
        self.client.put('/api/home-assistant/settings', json={**self.config, 'url': 'http://other-ha.local'})
        self.refresh()
        self.assertEqual(self.room()['assigned_count'], 0)
        self.assertEqual(self.place(version=original).status_code, 409)
        self.client.put('/api/home-assistant/settings', json=self.config)
        self.assertEqual(self.room()['assigned_count'], 1)
        self.assertEqual(self.place(version=original).status_code, 409)

    def test_disabled_removed_and_unimported_devices_remain_removable(self):
        self.place()
        self.save('light.lounge', enabled=False)
        self.assertEqual(self.room()['state'], 'unknown')
        self.assertEqual(self.place().status_code, 409)
        self.assertEqual(self.place(room=None).status_code, 200)
        self.save('light.bedroom', imported=False)
        self.assertEqual(self.place('light.bedroom').status_code, 409)
        self.save('light.lounge'); self.place()
        self.rows[:] = [r for r in self.rows if r['entity_id'] != 'light.lounge']; self.refresh()
        self.assertEqual(self.room()['state'], 'unknown')
        self.assertEqual(self.place().status_code, 409)
        self.assertEqual(self.place(room=None).status_code, 200)

    def test_disabled_ha_never_reads_or_displays_cached_on(self):
        self.rows[0]['state'] = 'on'; self.place()
        self.client.put('/api/home-assistant/settings', json={**self.config, 'enabled': False})
        with patch.object(self.service.client, 'entities') as read:
            self.assertEqual(self.room()['state'], 'unknown')
            self.assertEqual(self.place().status_code, 409)
            self.assertEqual(self.place(room=None).status_code, 200)
            read.assert_not_called()

    def test_settings_change_during_read_discards_result(self):
        def change(*args, **kwargs):
            self.service.save(HomeAssistantSettings(**{**self.config, 'url': 'http://other-ha.local'}))
            return []
        with patch.object(self.service.client, 'entities', side_effect=change):
            response = self.client.get('/api/map/lights')
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('Living Room', response.text)

    def test_validation_authentication_and_cross_origin(self):
        version = self.view()['version']
        self.assertEqual(self.place(room='invented').status_code, 422)
        self.assertEqual(self.place(entity='switch.lounge_led').status_code, 422)
        self.assertEqual(self.place(entity='light.missing').status_code, 409)
        self.assertEqual(self.client.put('/api/map/lights/light.lounge', json={
            'room_id': 'living', 'version': version, 'action': 'turn_on'}).status_code, 422)
        self.assertEqual(self.client.put('/api/map/lights/light.lounge', headers={'Origin': 'http://untrusted.local'},
            json={'room_id': 'living', 'version': version}).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/map/lights').status_code, 401)
        self.assertEqual(self.place(version=version).status_code, 401)
        self.assertEqual(self.actions, [])

    def test_marker_coordinates_persist_and_stay_in_room(self):
        version = self.view()['version']
        path = '/api/map/lights/light.lounge'
        body = dict(room_id='kitchen', version=version, x=500.5, y=250.25)
        self.assertEqual(self.client.put(path, json=body).status_code, 200)
        restored = MapLights(self.app.state.auth, self.service).snapshot()
        light = next(d for d in restored['devices'] if d['entity_id'] == 'light.lounge')
        self.assertEqual((light['x'], light['y'], light['room_id']), (500.5, 250.25, 'kitchen'))
        for invalid in ({'x': -1}, {'x': 1001}, {'y': 701}, {'x': 100}, {'x': None}, {'y': True}, {'room_id': None}):
            self.assertEqual(self.client.put(path, json={**body, **invalid}).status_code, 422, invalid)
        self.assertEqual(self.actions, [])
        self.place(room=None)
        self.assertIsNone(next(d for d in self.view()['devices'] if d['entity_id'] == 'light.lounge')['x'])

    def test_old_room_assignments_migrate_once_without_resurrection(self):
        with self.app.state.auth.db() as db:
            db.execute('DROP TABLE map_light_placements')
            db.execute('''CREATE TABLE map_light_placements (
                origin TEXT NOT NULL, layout TEXT NOT NULL, entity_id TEXT NOT NULL,
                room_id TEXT NOT NULL, name TEXT NOT NULL, PRIMARY KEY(origin,layout,entity_id))''')
            db.execute('INSERT INTO map_light_placements VALUES(?,?,?,?,?)',
                       ('http://ha.local', 'sample-home-v1', 'light.lounge', 'study', 'Lounge'))
            # A rollback may leave old and new records together. Keep the newer
            # placement rather than failing startup or overwriting its room.
            db.execute('INSERT INTO map_light_placements VALUES(?,?,?,?,?)',
                       ('http://ha.local', 'sample-home-v1', 'light.bedroom', 'living', 'Bedroom'))
            db.execute('INSERT INTO map_light_placements VALUES(?,?,?,?,?)',
                       ('http://ha.local', LAYOUT, 'light.bedroom', 'bedroom', 'Bedroom'))
        migrated = MapLights(self.app.state.auth, self.service)
        snapshot = migrated.snapshot()
        self.assertEqual(snapshot['layout'], LAYOUT)
        self.assertEqual(len(snapshot['rooms']), 8)
        light = next(d for d in snapshot['devices'] if d['entity_id'] == 'light.lounge')
        self.assertEqual(light['room_id'], 'study')
        self.assertIsNotNone(light['x'])
        self.assertEqual(next(d for d in snapshot['devices'] if d['entity_id']=='light.bedroom')['room_id'], 'bedroom')
        self.place(room=None)
        restarted = MapLights(self.app.state.auth, self.service).snapshot()
        self.assertIsNone(next(d for d in restarted['devices'] if d['entity_id'] == 'light.lounge')['room_id'])

    def act(self, entity='light.lounge', action='on', value=None, version=None, **kwargs):
        return self.client.post('/api/map/lights/'+entity+'/actions', json=dict(
            action=action, value=value, version=version or self.view()['version']), **kwargs)

    def test_light_controls_reuse_validation_and_invalidate_snapshot(self):
        before = self.view()
        self.assertIn('brightness', [c['key'] for c in next(d for d in before['devices'] if d['entity_id']=='light.lounge')['controls']])
        result = self.act()
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['state'], 'on')
        self.assertNotIn('source_url', result.json())
        self.assertEqual(self.actions, [('/api/services/light/turn_on', {'entity_id': 'light.lounge'})])
        self.assertEqual(next(d for d in self.view()['devices'] if d['entity_id']=='light.lounge')['state'], 'on')
        self.assertEqual(self.act(action='brightness', value=42).status_code, 200)
        self.assertEqual(self.actions[-1][1]['brightness_pct'], 42)
        count = len(self.actions)
        self.assertEqual(self.act(action='brightness', value=101).status_code, 400)
        self.assertEqual(self.act(action='unlock').status_code, 422)
        self.assertEqual(self.act(entity='lock.front', action='unlock').status_code, 422)
        self.assertEqual(len(self.actions), count)

    def test_stale_disabled_cross_origin_and_unauthenticated_actions_never_send(self):
        version = self.view()['version']
        self.save('light.lounge', enabled=False)
        self.assertEqual(self.act(version=version).status_code, 409)
        self.assertEqual(self.act().status_code, 409)
        self.save('light.lounge')
        version = self.view()['version']
        self.assertEqual(self.act(headers={'Origin': 'http://untrusted.local'}).status_code, 403)
        self.rows[0]['state'] = 'unavailable'
        self.assertEqual(self.act().json()['status'], 'unavailable')
        self.client.cookies.clear()
        self.assertEqual(self.act(version=version).status_code, 401)
        self.assertEqual(self.actions, [])

    def test_changed_origin_between_map_validation_and_action_is_rejected(self):
        original = self.service.device_action
        def changed(*args, **kwargs):
            self.service.save(HomeAssistantSettings(**{**self.config, 'url': 'http://other-ha.local'}))
            return original(*args, **kwargs)
        with patch.object(self.service, 'device_action', side_effect=changed):
            self.assertEqual(self.act().status_code, 409)
        self.assertEqual(self.actions, [])

    def test_map_page_and_shared_navigation_on_every_page(self):
        for path in ('/', '/map', '/home-assistant', '/queries', '/voices', '/cameras', '/models', '/training', '/bench'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('data-adam-navigation', response.text)
            self.assertIn('/assets/navigation.js', response.text)
        home = self.client.get('/').text
        self.assertNotIn('mapViewport', home)
        self.assertIn('resourceCpu', home)

    def private_layout(self):
        folder = self.app.state.auth.directory / 'maps'
        folder.mkdir(exist_ok=True)
        def chunk(kind, data):
            return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))
        png = b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 6, 0, 0, 0))
        png += chunk(b'IDAT', zlib.compress(b'\x00'+b'\x00'*8))+chunk(b'IEND', b'')
        (folder / 'plan.png').write_bytes(png)
        config = dict(name='Test plan', image='plan.png', rooms=[dict(
            id='test_room', name='Test room', points=[[0,0],[500,0],[500,100],[100,100],[100,500],[0,500]], anchor=[50,50])])
        (folder / 'layout.json').write_text(json.dumps(config), encoding='utf-8')
        return folder, config

    def test_private_background_authentication_polygon_and_layout_isolation(self):
        self.place()
        folder, config = self.private_layout()
        self.map.__init__(self.app.state.auth, self.service)
        snapshot = self.view()
        self.assertEqual(snapshot['name'], 'Test plan')
        self.assertEqual(snapshot['aspect_ratio'], 2)
        self.assertEqual(snapshot['background_url'], '/api/map/background')
        self.assertIsNone(next(d for d in snapshot['devices'] if d['entity_id']=='light.lounge')['room_id'])
        response = self.client.get('/api/map/background')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['content-type'], 'image/png')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.content, (folder/'plan.png').read_bytes())
        version = snapshot['version']
        body = dict(room_id='test_room', version=version, x=200, y=200)
        self.assertEqual(self.client.put('/api/map/lights/light.lounge', json=body).status_code, 422)
        self.assertEqual(self.client.put('/api/map/lights/light.lounge', json={**body, 'x': 50}).status_code, 200)
        self.assertEqual(self.place(room='test_room').status_code, 200)
        light = next(d for d in self.view()['devices'] if d['entity_id']=='light.lounge')
        self.assertEqual((light['x'], light['y']), (50, 50))
        (folder/'layout.json').unlink()
        self.map.__init__(self.app.state.auth, self.service)
        self.assertEqual(self.room()['assigned_count'], 1)
        self.assertEqual(self.client.get('/api/map/background').status_code, 404)
        (folder/'layout.json').write_text(json.dumps(config), encoding='utf-8')
        self.map.__init__(self.app.state.auth, self.service)
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/map/background').status_code, 401)
        self.assertEqual(self.actions, [])

    def test_outdoor_placements_preserve_interiors_and_reject_image_overlap(self):
        self.private_layout()
        self.map.__init__(self.app.state.auth, self.service)
        self.assertEqual(self.place(room='test_room').status_code, 200)
        layout = self.view()['layout']
        for room, x, y in (('outdoor_back', 950, -40), ('outdoor_front', 50, 740),
                           ('outdoor_back', -30, 200), ('outdoor_front', 1030, 600)):
            body = dict(room_id=room, x=x, y=y, version=self.view()['version'])
            result = self.client.put('/api/map/lights/light.bedroom', json=body)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(self.client.put('/api/map/lights/light.bedroom', json={**body, 'x':500, 'y':350}).status_code, 422)
        self.map.__init__(self.app.state.auth, self.service)
        snapshot = self.view()
        self.assertEqual(snapshot['layout'], layout)
        self.assertEqual(snapshot['viewport'], dict(x=-60, y=-75, width=1120, height=850))
        inside = next(d for d in snapshot['devices'] if d['entity_id']=='light.lounge')
        self.assertEqual((inside['room_id'], inside['x'], inside['y']), ('test_room', 50, 50))
        outside = next(d for d in snapshot['devices'] if d['entity_id']=='light.bedroom')
        self.assertEqual((outside['room_id'], outside['x'], outside['y']), ('outdoor_front', 1030, 600))
        self.assertEqual(self.actions, [])

    def test_invalid_private_layout_does_not_break_headend_or_expose_paths(self):
        folder, config = self.private_layout()
        for patch in ({'image': '../fleet.sqlite3'}, {'rooms': []}, {'image': 'missing.png'}):
            (folder/'layout.json').write_text(json.dumps({**config, **patch}), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_private_layout(self.app.state.auth.directory)
            self.map.__init__(self.app.state.auth, self.service)
            snapshot = self.view()
            self.assertEqual(snapshot['layout'], LAYOUT)
            self.assertIn('Invalid private map layout', snapshot['layout_error'])
            self.assertNotIn(str(folder), json.dumps(snapshot))


if __name__ == '__main__':
    unittest.main()
