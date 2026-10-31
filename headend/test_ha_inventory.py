"""Inventory identity, capability checks, imports and spoken dispatch contracts."""
import json
import unittest
from unittest.mock import patch
import httpx
from . import test_fleet
from .home_assistant import HomeAssistantClient, HomeAssistantService, HomeAssistantSettings


class InventoryTests(unittest.TestCase):
    tearDown = test_fleet.FleetTests.tearDown

    def setUp(self):
        test_fleet.FleetTests.setUp(self)
        self.service = self.app.state.fleet.home_assistant
        self.rows = [
            dict(entity_id='light.lounge', state='off', attributes=dict(friendly_name='Living Room', supported_color_modes=['brightness'])),
            dict(entity_id='light.bedroom', state='off', attributes=dict(friendly_name='Bedroom', supported_color_modes=['rgb', 'color_temp'], min_color_temp_kelvin=2000, max_color_temp_kelvin=6500)),
            dict(entity_id='cover.garage', state='closed', attributes=dict(friendly_name='Garage Door', supported_features=3)),
            dict(entity_id='climate.house', state='heat_cool', attributes=dict(friendly_name='Thermostat', supported_features=3, hvac_modes=['off','heat','heat_cool'], min_temp=45, max_temp=90)),
            dict(entity_id='lock.front', state='locked', attributes=dict(friendly_name='Front Door')),
            dict(entity_id='sensor.battery', state='92', attributes=dict(friendly_name='Battery', unit_of_measurement='%')),
            dict(entity_id='switch.lounge_led', state='on', attributes=dict(friendly_name='Living Room LED')),
            dict(entity_id='fan.office', state='off', attributes=dict(friendly_name='Office Fan', supported_features=49)),
            dict(entity_id='select.preset', state='one', attributes=dict(friendly_name='Preset', options=['one', 'two'])),
        ]
        self.actions = []
        def handle(request):
            if request.url.path == '/api/states': return httpx.Response(200, json=self.rows)
            if request.url.path.startswith('/api/states/'):
                entity_id = request.url.path.rsplit('/',1)[1]
                return httpx.Response(200, json=next(r for r in self.rows if r['entity_id']==entity_id))
            if request.method == 'POST':
                body = json.loads(request.content); self.actions.append((request.url.path, body))
                row = next(r for r in self.rows if r['entity_id']==body['entity_id'])
                action = request.url.path.rsplit('/',1)[1]
                row['state'] = {'turn_on':'on', 'turn_off':'off', 'open_cover':'open', 'close_cover':'closed', 'lock':'locked', 'unlock':'unlocked'}.get(action,row['state'])
                return httpx.Response(200,json=[])
            raise AssertionError(request.url.path)
        self.service.client = HomeAssistantClient(httpx.MockTransport(handle))
        self.config = dict(url='http://ha.local', token='test-token', enabled=True, auto_import=True)
        self.assertEqual(self.client.put('/api/home-assistant/settings',json=self.config).status_code,200)
        self.refresh()

    def refresh(self):
        result = self.client.post('/api/home-assistant/inventory/refresh')
        self.assertEqual(result.status_code,200,result.text)
        return {r['entity_id']:r for r in result.json()['devices']}

    def save(self, entity_id, **values):
        response = self.client.patch('/api/home-assistant/inventory/'+entity_id,json=values)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def command(self,text):
        result = self.client.post('/api/home-assistant/commands',json=dict(text=text))
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def act(self,entity_id,action,value=None):
        return self.client.post('/api/home-assistant/inventory/'+entity_id+'/actions',json=dict(action=action,value=value))

    def test_default_names_auto_import_and_capabilities(self):
        rows = self.refresh()
        self.assertTrue(rows['light.bedroom']['imported'])
        self.assertFalse(rows['sensor.battery']['imported'])
        self.assertFalse(rows['switch.lounge_led']['imported'])
        self.assertEqual(rows['cover.garage']['capabilities'],['open','close'])
        self.assertIn('brightness',rows['light.bedroom']['capabilities'])
        self.assertEqual(self.command('Turn on Bedroom')['state'],'on')
        self.assertEqual(self.actions[-1],('/api/services/light/turn_on',dict(entity_id='light.bedroom')))

    def test_names_aliases_survive_refresh_and_restart_without_retargeting(self):
        self.save('light.lounge', name='Lounge', aliases=['Front room'])
        self.rows[0]['attributes']['friendly_name'] = 'Main ceiling'
        row = self.refresh()['light.lounge']
        self.assertEqual(row['name'],'Lounge');self.assertEqual(row['default_name'],'Main ceiling')
        self.command('Turn on Front room')
        self.assertEqual(self.actions[-1][1]['entity_id'],'light.lounge')
        restored = HomeAssistantService(self.app.state.auth,None,self.service.client)
        try:
            result = restored.answer('Turn off Lounge',restored.settings())
            self.assertEqual(result['entity_id'],'light.lounge')
        finally: restored.close()
        self.save('light.lounge',name='Lounge',use_default_name=False)
        self.assertEqual(self.command('Turn on Main ceiling')['status'],'ignored')
        self.save('light.lounge',name='')
        self.assertEqual(self.refresh()['light.lounge']['name'],'Main ceiling')

    def test_duplicates_and_negated_or_multiple_commands_do_not_act(self):
        self.save('light.bedroom',name='Living Room')
        self.assertEqual(self.command('Turn on Living Room')['status'],'ambiguous')
        self.assertTrue(self.refresh()['light.lounge']['conflicts'])
        for phrase in ('Do not turn on Bedroom','Turn on Living Room and Bedroom','Turn it on'):
            self.assertEqual(self.command(phrase)['status'],'ignored')
        self.assertEqual(self.actions,[])

    def test_polite_commands_keep_exact_targets_and_reject_negation(self):
        self.assertEqual(self.command('Hey Adam, can you unlock the front door?')['state'], 'unlocked')
        self.assertEqual(self.actions[-1], ('/api/services/lock/unlock', {'entity_id': 'lock.front'}))
        self.assertEqual(self.command('Hey, Adam, could you please turn on Bedroom?')['state'], 'on')
        self.assertEqual(self.actions[-1], ('/api/services/light/turn_on', {'entity_id': 'light.bedroom'}))
        before = len(self.actions)
        for phrase in ('Can you not unlock Front Door?', 'Could you please turn on Bedroom and Living Room?',
                       'Would you unlock a different door?'):
            self.assertEqual(self.command(phrase)['status'], 'ignored')
        self.assertEqual(len(self.actions), before)

    def test_removal_missing_entity_and_changed_origin_do_not_remap(self):
        self.save('light.bedroom',imported=False)
        self.assertFalse(self.refresh()['light.bedroom']['imported'])
        self.assertEqual(self.command('Turn on Bedroom')['status'],'ignored')
        self.rows = [r for r in self.rows if r['entity_id']!='light.lounge']
        self.rows.append(dict(entity_id='light.replacement',state='off',attributes=dict(friendly_name='Living Room',supported_color_modes=['brightness'])))
        rows = self.refresh()
        self.assertFalse(rows['light.lounge']['present'])
        self.assertEqual(rows['light.lounge']['custom_name'],'')
        self.assertEqual(self.act('light.lounge','on').status_code,409)
        self.assertEqual(self.client.put('/api/home-assistant/settings',json={**self.config,'url':'http://another.local'}).status_code,200)
        self.assertEqual(self.client.get('/api/home-assistant/inventory').json()['devices'],[])
        self.assertEqual(self.actions,[])

    def test_auto_import_can_enable_existing_discovered_entries(self):
        self.save('light.bedroom',imported=False)
        self.client.put('/api/home-assistant/settings',json={**self.config,'auto_import':False})
        self.rows.append(dict(entity_id='switch.new',state='off',attributes=dict(friendly_name='New plug')))
        self.assertFalse(self.refresh()['switch.new']['imported'])
        self.client.put('/api/home-assistant/settings',json=self.config)
        rows=self.refresh()
        self.assertTrue(rows['switch.new']['imported'])
        self.assertFalse(rows['light.bedroom']['imported'])

    def test_parameter_controls_exact_target_bounds_and_service(self):
        self.command('Set Bedroom to 40 percent')
        self.assertEqual(self.actions[-1],('/api/services/light/turn_on',dict(entity_id='light.bedroom',brightness_pct=40)))
        self.command('Set color of Bedroom to blue')
        self.assertEqual(self.actions[-1][1]['rgb_color'],[0,0,255])
        self.command('Set Office Fan to 50 percent')
        self.assertEqual(self.actions[-1][0],'/api/services/fan/set_percentage')
        self.command('Open Garage Door')
        self.assertEqual(self.actions[-1][0],'/api/services/cover/open_cover')
        before=len(self.actions)
        self.assertEqual(self.act('cover.garage','position',50).status_code,400)
        self.assertEqual(self.act('light.bedroom','brightness',101).status_code,400)
        self.assertEqual(self.act('light.bedroom','brightness',True).status_code,422)
        self.assertEqual(self.act('light.bedroom','arbitrary_service').status_code,400)
        self.rows[1]['attributes']['supported_color_modes']=['onoff']
        self.assertEqual(self.act('light.bedroom','brightness',40).status_code,400)
        self.assertEqual(len(self.actions),before)

    def test_climate_ranges_and_decimals_and_select_validation(self):
        self.assertEqual(self.command('Set Thermostat to 70 degrees')['status'],'unsupported')
        self.command('Set temperature range of Thermostat to 65.5 and 72 degrees')
        self.assertEqual(self.actions[-1][1],dict(entity_id='climate.house',target_temp_low=65.5,target_temp_high=72))
        self.command('Set mode of Thermostat to heat')
        self.assertEqual(self.actions[-1][1]['hvac_mode'],'heat')
        self.assertEqual(self.act('climate.house','temperature_range',[80,65]).status_code,400)
        self.assertEqual(self.command('Set temperature of Thermostat to -60 degrees')['status'],'unsupported')
        self.save('select.preset')
        self.command('Set option of Preset to two')
        self.assertEqual(self.actions[-1][1]['option'],'two')
        self.assertEqual(self.act('select.preset','option','three').status_code,400)

    def test_disabling_or_renaming_during_lookup_prevents_action(self):
        original=self.service.client.device_state
        def state(settings,entity_id):
            result=original(settings,entity_id)
            self.save(entity_id,name='New name',enabled=False)
            return result
        with patch.object(self.service.client,'device_state',side_effect=state):
            self.assertEqual(self.command('Turn on Bedroom')['status'],'ignored')
        self.assertFalse(self.actions)

    def test_authentication_on_inventory_and_control_routes(self):
        self.client.post('/api/auth/logout')
        for method,path,body in [('GET','',None),('POST','/refresh',{}),('PATCH','/light.lounge',{}),('POST','/light.lounge/actions',dict(action='on'))]:
            self.assertEqual(self.client.request(method,'/api/home-assistant/inventory'+path,json=body).status_code,401)
        self.assertFalse(self.actions)

    def test_curated_sensors_binary_sensors_spoken_states_and_summaries(self):
        self.rows.extend([
            dict(entity_id='sensor.nursery_temp', state='71.42', attributes=dict(
                friendly_name='Nursery Temperature', device_class='temperature', unit_of_measurement='°F')),
            dict(entity_id='sensor.nursery_humidity', state='46', attributes=dict(
                friendly_name='Nursery Humidity', device_class='humidity', unit_of_measurement='%')),
            dict(entity_id='sensor.router_rssi', state='-58', attributes=dict(
                friendly_name='Router Signal', device_class='signal_strength', unit_of_measurement='dBm')),
            dict(entity_id='binary_sensor.patio_door', state='on', attributes=dict(
                friendly_name='Patio Door', device_class='door')),
            dict(entity_id='binary_sensor.office_window', state='off', attributes=dict(
                friendly_name='Office Window', device_class='window')),
            dict(entity_id='binary_sensor.sink_leak', state='off', attributes=dict(
                friendly_name='Sink Leak', device_class='moisture')),
        ])
        rows = self.refresh()
        self.assertTrue(rows['sensor.nursery_temp']['imported'])
        self.assertEqual(rows['sensor.nursery_temp']['capabilities'], ['read'])
        self.assertTrue(rows['sensor.nursery_humidity']['imported'])
        self.assertTrue(rows['binary_sensor.patio_door']['imported'])
        self.assertTrue(rows['binary_sensor.sink_leak']['imported'])
        self.assertFalse(rows['sensor.battery']['imported'])
        self.assertFalse(rows['sensor.router_rssi']['imported'])

        # Direct and room-first sensor reads:
        self.assertEqual(self.command("What's the temperature in the nursery?")['text'],
                         'Nursery Temperature is 71.4 degrees Fahrenheit.')
        self.assertEqual(self.command('How warm is the nursery?')['text'],
                         'Nursery Temperature is 71.4 degrees Fahrenheit.')
        self.assertEqual(self.command("What's the humidity in the nursery?")['text'],
                         'Nursery Humidity is 46 percent.')
        self.assertEqual(self.command('Is Patio Door open?')['text'], 'Patio Door is open.')
        self.assertEqual(self.command('Is Sink Leak wet?')['text'], 'Sink Leak is dry.')

        # Multi-entity bedtime/departure summaries:
        openings = self.command('Are any doors or windows open?')
        self.assertEqual(openings['status'], 'answered')
        self.assertIn('Patio Door', openings['text'])

        for r in self.rows:
            if r['entity_id'] == 'binary_sensor.patio_door':
                r['state'] = 'off'
        all_closed = self.command('Are all doors and windows closed?')
        self.assertEqual(all_closed['text'], 'All 3 doors and windows are closed.')

        self.assertEqual(self.command('Are all doors locked?')['text'], 'Front Door is locked.')
        self.assertEqual(self.command('Are any lights on?')['text'], 'All 2 lights are off.')
        self.assertEqual(self.command('Are there any water leaks?')['text'], 'Sink Leak is dry.')
        self.assertEqual(self.actions, [])


if __name__=='__main__': unittest.main()
