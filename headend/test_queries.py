"""Offline weather contracts and wake -> STT -> selected voice -> targeted delivery."""
from datetime import datetime, timezone, timedelta
import tempfile
import time
import unittest
from unittest.mock import patch

from .queries import OpenMeteo, QuerySettings, QueryService, Location, WeatherUnavailable, weather_intent, search_name
from .state import State
from . import test_fleet
from .stt import Transcript


def weather_fixture():
    now = datetime.now(timezone.utc)
    return dict(utc_offset_seconds=0, current=dict(time=now.isoformat(timespec='minutes').replace('+00:00',''),
        temperature_2m=52.3, apparent_temperature=50.1, weather_code=3),
        daily=dict(time=[now.date().isoformat(), (now.date()+timedelta(days=1)).isoformat()],
                   weather_code=[61,3],temperature_2m_max=[55,54],temperature_2m_min=[43,42],
                   precipitation_probability_max=[70,25]))


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.calls=[]
        def fetch(url, params):
            self.calls.append((url,params))
            if 'geocoding' in url:
                return {'results':[dict(name='Olympia',admin1='Washington',country_code='US',latitude=47.04,longitude=-122.9)]}
            return weather_fixture()
        self.provider=OpenMeteo(fetch=fetch)
        self.service=QueryService(State(self.temp.name),None,test_fleet.FakeSpeech(),lambda pcm:pcm,self.provider)

    def tearDown(self):
        self.service.close();self.temp.cleanup()

    def test_current_alias_default_units_and_cache(self):
        for question in ("Hey Jarvis, what's the weather in Shelton, WA?", "What's the temperature at home?", "What's the weather?"):
            answer=self.service.answer(question)
            self.assertEqual(answer['status'],'answered')
            self.assertIn('52 degrees Fahrenheit and overcast',answer['text'])
            self.assertEqual(answer['location'],'Shelton, Washington')
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.calls[0][1]['latitude'],47.21509)
        config=self.service.settings();config.units='celsius';self.service.save(config)
        self.assertIn('Celsius',self.service.answer('What is the weather?')['text'])
        self.assertEqual(self.calls[-1][1]['temperature_unit'],'celsius')
        self.assertEqual(len(self.calls),2)

    def test_forecasts_and_unsupported_time_or_nonquestion(self):
        self.assertIn('Tomorrow in Shelton',self.service.answer("What's tomorrow's forecast at home?")['text'])
        self.assertIn('70 percent',self.service.answer('Will it rain today in Shelton?')['text'])
        before=len(self.calls)
        self.assertIn('current weather, today, or tomorrow',self.service.answer("What's the weather next week?")['text'])
        for question in ('Turn on the lights','I like the weather','Hey Jarvis, bananas are on the counter'):
            self.assertEqual(self.service.answer(question)['status'],'ignored')
        self.assertEqual(len(self.calls),before)

    def test_network_errors_missing_old_data_and_ambiguity_never_invent_weather(self):
        for data in ({}, dict(weather_fixture(),current={'temperature_2m':None}),
                     dict(weather_fixture(),current=dict(weather_fixture()['current'],time='2000-01-01T00:00'))):
            self.provider.cache.clear();self.provider.fetch=lambda *args:data
            result=self.service.answer('What is the weather?')
            self.assertEqual(result['status'],'unavailable')
            self.assertNotIn('degrees',result['text'])
        self.provider.cache.clear()
        def down(*args):raise WeatherUnavailable('Weather service is unavailable.')
        self.provider.fetch=down
        self.assertIn('unavailable',self.service.answer('Weather?')['text'])
        with patch.object(self.provider,'search',return_value=[Location(name='Springfield, MA',latitude=42,longitude=-72),Location(name='Springfield, IL',latitude=39,longitude=-89)]):
            self.assertIn('ambiguous',self.service.answer('Weather in Springfield?')['text'])

    def test_configuration_persists_and_rejects_unsafe_or_conflicting_settings(self):
        config=self.service.settings();config.enabled=False;self.service.save(config)
        self.assertEqual(self.service.answer('Weather?')['status'],'ignored')
        self.assertEqual(len(self.calls),0)
        restored=QueryService(State(self.temp.name),None,test_fleet.FakeSpeech(),lambda pcm:pcm,self.provider)
        try:self.assertFalse(restored.settings().enabled)
        finally:restored.close()
        for change in ({'provider':'http://127.0.0.1/admin'}, {'default_location':'missing'},
                       {'locations':[dict(name='bad',latitude=float('nan'),longitude=0)]},
                       {'locations':[dict(name='a',latitude=0,longitude=0,aliases=['home']),dict(name='b',latitude=1,longitude=1,aliases=['home'])]}):
            with self.assertRaises(ValueError):QuerySettings.model_validate({**QuerySettings().model_dump(),**change})

    def test_cache_expires_and_errors_do_not_return_old_answers(self):
        self.service.answer('Weather?')
        self.provider.clock=lambda:time.time()+601
        def down(*args):raise WeatherUnavailable('Weather service is unavailable.')
        self.provider.fetch=down
        self.assertEqual(self.service.answer('Weather?')['status'],'unavailable')

    def test_spoken_state_names_forecast_word_order_and_exact_city(self):
        self.assertEqual(search_name('Seattle Washington'),'Seattle, Washington')
        self.assertEqual(search_name('Charleston West Virginia'),'Charleston, West Virginia')
        self.assertEqual(search_name('Portland OR'),'Portland, Oregon')
        self.assertEqual(weather_intent('What is the forecast for tomorrow in Olympia Washington?'),
                         dict(day='tomorrow',place='Olympia Washington'))
        airport=Location(name='Olympia Airport, Washington',latitude=47,longitude=-122)
        city=Location(name='Olympia, Washington',latitude=47,longitude=-122)
        with patch.object(self.provider,'search',return_value=[city,airport]):
            self.assertEqual(self.service.answer('Weather in Olympia Washington?')['location'],city.name)
        self.assertIn('current weather, today, or tomorrow',self.service.answer('What was the weather last night?')['text'])


class QueryIntegrationTests(unittest.TestCase):
    tearDown=test_fleet.FleetTests.tearDown
    headers=test_fleet.FleetTests.headers
    poll=test_fleet.FleetTests.poll
    stream=test_fleet.FleetTests.stream

    def setUp(self):
        test_fleet.FleetTests.setUp(self)
        self.service=self.app.state.fleet.queries
        self.service.provider=OpenMeteo(fetch=lambda *args:weather_fixture())
        self.heard=[]
        class WeatherSTT:
            def transcribe(inner, utterance):
                return Transcript(utterance.session_id,"Hey Jarvis, what's the weather in Shelton, WA?")
        self.app.state.jobs.factory=WeatherSTT
        def speech(text):
            self.heard.append(text)
            return test_fleet.FakeSpeech().synthesize(text)
        self.service.speech.synthesize=speech

    def wait_reply(self, session_id):
        for _ in range(200):
            entry=self.app.state.fleet.sessions[session_id]
            if entry.get('reply',{}).get('status') not in (None,'working'):
                return entry['reply']
            time.sleep(.01)
        self.fail('Reply did not finish')

    def test_wake_replies_without_dashboard_polling_only_to_origin_and_once(self):
        other=self.client.post('/api/units',json={'name':'Other'}).json()
        result=self.stream(**{'X-Trigger':'wake','X-Wake-Model':'hey_jarvis-dev-v2'}).json()
        reply=self.wait_reply(result['session_id'])
        self.assertEqual(reply['status'],'queued',reply)
        self.assertEqual(len(self.heard),1)
        self.assertEqual(self.poll(other).json()['type'],'idle')
        page=self.poll().json();self.assertEqual(page['id'],reply['page_id'])
        for _ in range(3):self.client.get('/api/fleet')
        self.assertEqual(len(self.heard),1)
        # Clip retranscription is diagnostic: it must not replay an answer.
        job=self.client.post('/api/clips/'+result['clip_id']+'/transcriptions',json={}).json()
        for _ in range(100):
            if self.client.get('/api/jobs/'+job['id']).json()['status']=='done':break
            time.sleep(.01)
        self.assertEqual(len(self.heard),1)

    def test_touch_and_failed_jobs_do_not_reply(self):
        result=self.stream().json()
        for _ in range(100):
            if self.client.get('/api/jobs/'+result['job_id']).json()['status']=='done':break
            time.sleep(.01)
        self.assertEqual(self.heard,[])
        entry=dict(trigger='wake',unit_id=self.unit['id'],started=time.time())
        for status in ('cancelled','error','no_speech'):
            self.service.submit(entry,dict(status=status,text='Weather?'))
        self.assertEqual(self.heard,[])

    def test_settings_auth_preview_voice_override_and_disabled(self):
        result=self.client.post('/api/queries/test',json={'text':'Weather?'}).json()
        self.assertEqual(result['status'],'answered');self.assertEqual(self.heard,[])
        config=self.client.get('/api/queries/settings').json()
        config['voice_id']='custom-test@1.0.0'
        with patch.object(self.service.voices.registry,'resolve'):
            self.assertEqual(self.client.put('/api/queries/settings',json=config).status_code,200)
        self.app.state.auth.heartbeat(self.unit['id'],'v12','hey_jarvis-dev-v2')
        with patch.object(self.service.voices,'synthesize',return_value=test_fleet.FakeSpeech().synthesize('test')) as synth:
            result=self.client.post('/api/queries/test',json={'text':'Weather?','unit_id':self.unit['id']}).json()
            self.assertEqual(result['status'],'queued',result)
            self.assertEqual(synth.call_args.args[1],'custom-test@1.0.0')
        config['voice_id']=None;config['enabled']=False
        self.client.put('/api/queries/settings',json=config)
        self.assertEqual(self.client.post('/api/queries/test',json={'text':'Weather?'}).json()['status'],'ignored')
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/queries/settings').status_code,401)
        self.assertEqual(self.client.post('/api/queries/test',json={'text':'Weather?'}).status_code,401)
        self.assertEqual(self.client.put('/api/queries/settings',json=config,headers=self.headers()).status_code,401)

    def test_reply_queue_bound_does_not_block_recording(self):
        for _ in range(4):self.assertTrue(self.service.slots.acquire(blocking=False))
        try:
            result=self.stream(**{'X-Trigger':'wake','X-Wake-Model':'hey_jarvis-dev-v2'})
            self.assertEqual(result.status_code,202)
            reply=self.wait_reply(result.json()['session_id'])
            self.assertEqual(reply['status'],'error')
            self.assertEqual(self.heard,[])
        finally:
            for _ in range(4):self.service.slots.release()


