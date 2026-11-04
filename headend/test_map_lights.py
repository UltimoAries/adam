"""Map placement persistence, origin isolation, and fresh read-only status."""
import unittest
from unittest.mock import patch

from . import test_ha_inventory
from .map_lights import MapLights
from .home_assistant import HomeAssistantSettings, HomeAssistantUnavailable


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


if __name__ == '__main__':
    unittest.main()
