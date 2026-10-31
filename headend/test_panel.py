"""Panel weather is bounded, fresh, and independent of the audio/paging path."""
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from .panel import PanelService
from .queries import QueryService, OpenMeteo, Location
from datetime import datetime, timezone
from .state import State
from .test_queries import weather_fixture
from . import test_fleet
from .test_fleet import FakeSpeech


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.calls=[]
        def fetch(*args):
            self.calls.append(args)
            return weather_fixture()
        self.queries=QueryService(State(self.temp.name),None,FakeSpeech(),lambda pcm:pcm,OpenMeteo(fetch=fetch))
        self.panel=PanelService(self.queries)

    def tearDown(self):
        self.panel.close();self.queries.close();self.temp.cleanup()

    def ready(self):
        for _ in range(100):
            value=self.panel.snapshot()
            if value['weather_state']!='loading':return value
            time.sleep(.01)
        self.fail('Panel weather did not finish')

    def test_default_weather_clock_units_and_cached_reads(self):
        value=self.ready()
        self.assertEqual(value['weather_state'],'ready')
        self.assertEqual((value['location'],value['temperature'],value['units']),('Seattle',52,'F'))
        self.assertLess(abs(value['unix_time']-time.time()),2)
        for _ in range(5):self.panel.snapshot()
        self.assertEqual(len(self.calls),1)
        config=self.queries.settings();config.units='celsius';self.queries.save(config)
        self.assertEqual(self.ready()['units'],'C')
        self.assertEqual(len(self.calls),2)

    def test_disabled_and_invalid_weather_keep_clock_without_inventing_temperature(self):
        config=self.queries.settings();config.enabled=False;self.queries.save(config)
        value=self.panel.snapshot()
        self.assertEqual(value['weather_state'],'disabled');self.assertFalse(self.calls)
        self.assertIn('unix_time',value);self.assertNotIn('temperature',value)
        config.enabled=True;self.queries.save(config)
        data=weather_fixture();data['current']['time']='2000-01-01T00:00'
        with patch.object(self.queries.provider,'forecast',return_value=(data,time.time())):
            value=self.ready()
        self.assertEqual(value['weather_state'],'unavailable')
        self.assertNotIn('temperature',value)

    def test_slow_lookup_does_not_block_snapshot_or_keep_old_location(self):
        entered=threading.Event();release=threading.Event()
        def fetch(*args):
            entered.set();release.wait(3)
            return weather_fixture(),time.time()
        with patch.object(self.queries.provider,'forecast',side_effect=fetch):
            try:
                self.assertEqual(self.panel.snapshot()['weather_state'],'loading')
                self.assertTrue(entered.wait(1))
                started=time.monotonic()
                value=self.panel.snapshot()
                self.assertLess(time.monotonic()-started,.2)
                config=self.queries.settings();config.locations[0].name='New home';config.default_location='New home'
                self.queries.save(config)
                self.assertEqual(self.panel.snapshot()['location'],'New home')
            finally:release.set()
        self.assertEqual(self.ready()['location'],'New home')

    def test_old_cached_conditions_are_not_shown_as_current(self):
        self.ready()
        self.panel.clock=lambda:time.time()+7300
        self.panel.enabled=False
        value=self.panel.snapshot()
        self.assertEqual(value['weather_state'],'stale')
        self.assertNotIn('temperature',value)

    def test_device_locations_keep_separate_caches_and_names(self):
        config=self.queries.settings()
        config.locations.append(Location(name='New York',latitude=40.7,longitude=-74,timezone='America/New_York'))
        config.device_locations['a'*16]='New York'
        self.queries.save(config)
        def forecast(place,units):
            self.calls.append(place.name)
            data=weather_fixture();data['current']['temperature_2m']=61 if place.name=='New York' else 52
            return data,time.time()
        with patch.object(self.queries.provider,'forecast',side_effect=forecast):
            for _ in range(100):
                home=self.panel.snapshot('Kitchen','b'*16)
                other=self.panel.snapshot('Cabin','a'*16)
                if home['weather_state']==other['weather_state']=='ready':break
                time.sleep(.01)
            self.assertEqual((home['temperature'],other['temperature']),(52,61))
            for _ in range(5):
                self.panel.snapshot('Kitchen','b'*16);self.panel.snapshot('Cabin','a'*16)
            self.assertEqual(len(self.calls),2)
            self.assertEqual((home['device_name'],other['device_name']),('Kitchen','Cabin'))
        config.panel_show_device_name=False;self.queries.save(config)
        self.assertEqual(self.panel.snapshot('Kitchen')['device_name'],'')

    def test_local_time_tracks_dst_without_weather_or_computer_timezone(self):
        config=self.queries.settings();config.enabled=False;self.queries.save(config)
        self.panel.clock=lambda:datetime(2026,11,1,8,30,tzinfo=timezone.utc).timestamp()
        before=self.panel.snapshot()
        self.panel.clock=lambda:datetime(2026,11,1,9,30,tzinfo=timezone.utc).timestamp()
        after=self.panel.snapshot()
        self.assertEqual((before['utc_offset'],after['utc_offset']),(-25200,-28800))
        self.assertFalse(self.calls)


class PanelPollTests(unittest.TestCase):
    setUp=test_fleet.FleetTests.setUp
    tearDown=test_fleet.FleetTests.tearDown
    poll=test_fleet.FleetTests.poll
    headers=test_fleet.FleetTests.headers

    def test_metadata_accompanies_idle_busy_and_page_without_exposing_it_publicly(self):
        result=self.poll()
        self.assertEqual(result.json()['type'],'idle')
        self.assertEqual(result.json()['panel']['location'],'Seattle')
        self.assertEqual(result.json()['panel']['device_name'],'Kitchen')
        self.assertLess(len(result.content),1400)

    def test_device_rename_and_panel_toggle_apply_on_next_poll(self):
        self.app.state.auth.rename(self.unit['id'],'Living room panel')
        self.assertEqual(self.poll().json()['panel']['device_name'],'Living room panel')
        config=self.client.get('/api/queries/settings').json()
        config['panel_show_device_name']=False
        self.assertEqual(self.client.put('/api/queries/settings',json=config).status_code,200)
        self.assertEqual(self.poll().json()['panel']['device_name'],'')
        path='/api/units/'+self.unit['id']+'/poll'
        self.assertEqual(self.client.get(path).status_code,401)
        self.client.post('/api/pages/text',json={'units':[self.unit['id']],'text':'Hello.'})
        self.assertEqual(self.client.get(path+'?busy=true',headers=self.headers()).json()['type'],'idle')
        result=self.poll()
        self.assertEqual(result.json()['type'],'page')
        self.assertIn('panel',result.json())
        self.assertLess(len(result.content),1400)


if __name__=='__main__':unittest.main()
