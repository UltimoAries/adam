import json
import unittest
import httpx
import numpy as np
from .clips import wav_bytes
from .cue import decode_cue,generate_elevenlabs


class CueTests(unittest.TestCase):
    def sample(self):
        return wav_bytes(24000,(2000*np.sin(np.arange(24000)*2*np.pi*220/24000)).astype('<i2'))

    def test_local_conversion_and_silent_rejection(self):
        pcm,wav,metadata=decode_cue(self.sample())
        self.assertEqual(metadata['rate'],16000)
        self.assertEqual(metadata['channels'],1)
        self.assertTrue(.9<=metadata['seconds']<=1.1)
        self.assertGreater(len(wav),len(pcm))
        with self.assertRaises(ValueError):decode_cue(wav_bytes(16000,np.zeros(1600,dtype='<i2')))

    def test_generation_sends_only_description_and_does_not_save_key(self):
        requests=[]
        def handler(request):
            requests.append(request)
            return httpx.Response(200,content=self.sample())
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            _,_,metadata=generate_elevenlabs('fixture-key',client=client)
        self.assertEqual(len(requests),1)
        request=requests[0]
        self.assertEqual(str(request.url).split('?')[0],'https://api.elevenlabs.io/v1/sound-generation')
        self.assertEqual(request.headers['xi-api-key'],'fixture-key')
        self.assertEqual(json.loads(request.content)['duration_seconds'],1)
        self.assertNotIn('fixture-key',json.dumps(metadata))

    def test_service_errors_do_not_echo_response_or_credentials(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(401,text='fixture-secret'))) as client:
            with self.assertRaises(ValueError) as error:generate_elevenlabs('fixture-secret',client=client)
        self.assertIn('401',str(error.exception))
        self.assertNotIn('fixture-secret',str(error.exception))


if __name__=='__main__':unittest.main()
