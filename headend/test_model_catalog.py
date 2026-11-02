"""Model discovery is authenticated, read-only, bounded, and credential scoped."""
import asyncio
import json
import unittest

import httpx

from . import test_fleet
from .model_services import ServiceSettings
from .model_catalog import catalog_json


class ModelCatalogTests(unittest.TestCase):
    setUp = test_fleet.FleetTests.setUp
    tearDown = test_fleet.FleetTests.tearDown

    def setup_catalog(self):
        self.calls = []
        self.reply = httpx.Response(200, json={'data':[
            {'id':'whisper-local','task':'automatic-speech-recognition'},
            {'id':'piper-local','task':'text-to-speech'},
            {'id':'whisper-local','task':'automatic-speech-recognition'}]})
        def handle(request):
            self.calls.append(request)
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        self.service = self.app.state.model_services
        self.service.transport = httpx.MockTransport(handle)
        self.app.state.fleet.conversation.transport = self.service.transport
        self.service.save(ServiceSettings(stt={'url':'http://gpu:8000/v1','api_key':'private-saved-key'}))

    def test_speech_filtering_and_saved_key_without_routing_changes(self):
        self.setup_catalog()
        before = self.service.public()
        response = self.client.post('/api/models/stt/catalog',json={'url':'http://gpu:8000/v1'})
        self.assertEqual(response.status_code,200)
        self.assertEqual([m['id'] for m in response.json()['models']],['whisper-local'])
        self.assertEqual(self.calls[0].method,'GET')
        self.assertEqual(self.calls[0].headers['authorization'],'Bearer private-saved-key')
        self.assertEqual(self.service.public(),before)
        self.assertNotIn('private-saved-key',response.text)
        response = self.client.post('/api/models/tts/catalog',json={'url':'http://gpu:8000/v1'})
        self.assertEqual([m['id'] for m in response.json()['models']],['piper-local'])
        self.assertNotIn('authorization',self.calls[-1].headers)

    def test_keys_do_not_follow_changed_address_or_clear_request(self):
        self.setup_catalog()
        for payload in ({'url':'http://other:8000/v1'}, {'url':'http://gpu:8000/v1','clear_key':True}):
            self.client.post('/api/models/stt/catalog',json=payload)
            self.assertNotIn('authorization',self.calls[-1].headers)
        self.client.post('/api/models/stt/catalog',json={'url':'http://other:8000/v1','api_key':'new-key'})
        self.assertEqual(self.calls[-1].headers['authorization'],'Bearer new-key')
        self.assertEqual(self.service.settings.stt.api_key,'private-saved-key')

    def test_ollama_alias_and_installed_chat_models(self):
        self.setup_catalog()
        self.reply = httpx.Response(200,json={'models':[
            {'name':'qwen3.5:9b','capabilities':['completion']},
            {'name':'adam:latest','capabilities':['completion']},
            {'name':'embed-only','capabilities':['embedding']}]})
        response = self.client.post('/api/conversation/models',json={'remote_url':'http://gpu:11434'})
        self.assertEqual(response.status_code,200)
        self.assertEqual([m['id'] for m in response.json()['models']],['adam:latest','qwen3.5:9b'])
        self.assertIn('Follow GPU Control',response.json()['models'][0]['label'])
        self.assertEqual(str(self.calls[-1].url),'http://gpu:11434/api/tags')
        self.assertNotIn('authorization',self.calls[-1].headers)

    def test_offline_redirect_auth_bad_payload_and_size_are_sanitized(self):
        self.setup_catalog()
        before = self.service.public()
        for reply in (httpx.ConnectError('private-saved-key'),httpx.Response(401,text='private-saved-key'),
                      httpx.Response(302,headers={'location':'http://evil'}),
                      httpx.Response(200,json={'models':[]}),httpx.Response(200,content=b'x'*524289)):
            self.reply = reply
            response = self.client.post('/api/models/stt/catalog',json={'url':'http://gpu:8000/v1'})
            self.assertEqual(response.status_code,502)
            self.assertNotIn('private-saved-key',response.text)
        self.assertEqual(self.service.public(),before)

    def test_catalog_auth_and_secret_validation(self):
        self.setup_catalog()
        for path in ('/api/models/stt/catalog','/api/conversation/models'):
            result = self.client.post(path,json={'url':'http://user:secret@host','remote_url':'bad','api_key':'secret'})
            self.assertEqual(result.status_code,400)
            self.assertNotIn('secret',result.text)
            self.assertEqual(self.client.post(path,content=b'x'*8193).status_code,400)
        self.assertEqual(self.calls,[])
        self.client.post('/api/auth/logout')
        for path in ('/api/models/stt/catalog','/api/conversation/models'):
            self.assertEqual(self.client.post(path,json={}).status_code,401)

    def test_speech_settings_error_identifies_the_section_without_key(self):
        response = self.client.put('/api/models/settings',json={'tts':{
            'mode':'prefer_remote','url':'http://gpu:11434','api_key':'secret'}})
        self.assertEqual(response.status_code,400)
        self.assertIn('Speaking voice',response.text)
        self.assertNotIn('secret',response.text)
