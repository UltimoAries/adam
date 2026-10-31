import hashlib
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import sqlite3
import subprocess
import sys

from tools.migrate_headend import export_bundle, restore_bundle
from .state import State
from .reconnect_unit import reconnect
from .test_server import FakeDevice


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_accounts_unit_identity_voice_and_models_survive_relocation(self):
        data, models = self.root/'source'/'data', self.root/'source'/'models'
        state = State(data)
        state.setup('admin', 'migration-fixture-password')
        unit = state.add_unit('Desk Speaker')
        (data/'voices').mkdir()
        (data/'voices'/'active.json').write_text('{"voice_id":"Joe@1.0.0"}')
        (data/'server.pid').write_text('123')
        (data/'deployment').mkdir()
        (data/'deployment'/'pi5_ed25519').write_text('private deployment credential fixture')
        model = models/'stt'/'base.en'/'model.bin'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'model fixture')
        archive = self.root/'migration.zip'
        self.assertEqual(export_bundle(data, models, archive), 3)
        destination = self.root/'target'/'headend'
        self.assertEqual(restore_bundle(archive, destination), 3)
        restored = State(destination/'data')
        self.assertTrue(restored.login('admin', 'migration-fixture-password'))
        self.assertTrue(restored.unit_auth(unit['id'], unit['token']))
        self.assertEqual(restored.units()[0]['name'], 'Desk Speaker')
        self.assertEqual(json.loads((destination/'data'/'voices'/'active.json').read_text())['voice_id'], 'Joe@1.0.0')
        self.assertFalse((destination/'data'/'server.pid').exists())
        self.assertFalse((destination/'data'/'deployment').exists())
        with self.assertRaises(ValueError):
            restore_bundle(archive, destination)

    def test_bad_hash_and_traversal_never_install_runtime(self):
        for name, digest in [('data/../../escape', hashlib.sha256(b'bad').hexdigest()),
                             ('data/fleet.sqlite3', '0'*64)]:
            with self.subTest(name=name):
                archive = self.root/'invalid.zip'
                with zipfile.ZipFile(archive, 'w') as out:
                    out.writestr(name, b'bad')
                    out.writestr('manifest.json', json.dumps({'format':1, 'files':{name:digest}}))
                with self.assertRaises(ValueError):
                    restore_bundle(archive, self.root/'destination')
                self.assertFalse((self.root/'destination'/'data').exists())
                self.assertFalse((self.root/'escape').exists())

    def test_stopped_database_recovers_interrupted_transaction_before_backup(self):
        data, models = self.root/'source'/'data', self.root/'source'/'models'
        data.mkdir(parents=True)
        path = data/'fleet.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE fixture (value TEXT, payload BLOB)')
            db.execute("INSERT INTO fixture VALUES ('committed',zeroblob(100000))")
        # Force uncommitted pages to disk, then simulate a process crash.
        subprocess.run([sys.executable, '-c', '''
import sqlite3,os,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA cache_size=1')
db.execute("UPDATE fixture SET value='uncommitted',payload=randomblob(200000)")
os._exit(0)
''', str(path)], check=True)
        self.assertTrue(path.with_name(path.name+'-journal').is_file())
        model = models/'stt'/'base.en'/'model.bin'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'model fixture')
        bundle = self.root/'recovered.zip'
        export_bundle(data, models, bundle)
        destination = self.root/'restored'/'headend'
        restore_bundle(bundle, destination)
        with closing(sqlite3.connect(destination/'data'/'fleet.sqlite3')) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(db.execute('SELECT value FROM fixture').fetchone()[0], 'committed')

    def test_training_original_transcript_review_and_collection_tags_survive_move(self):
        import time
        import numpy as np
        from .clips import wav_bytes
        from .training_archive import InteractionArchive,CollectionTags,Review
        data,models=self.root/'source'/'data',self.root/'source'/'models'
        state=State(data)
        unit=state.add_unit('Living room ADAM')
        training=InteractionArchive(data/'training_archive')
        tags=CollectionTags(room='Living room',speaker='A',collection_session='moving-test')
        training.save_context(unit['id'],tags)
        raw=wav_bytes(16000,np.arange(1600,dtype='<i2'))
        saved=training.capture(raw,dict(id='migration-recording',unit_id=unit['id'],device_name=unit['name'],
            started=time.time(),trigger='wake',model_id='hey_adam-test',preroll_ms=20,
            collection_tags=tags.model_dump()))
        self.assertEqual(saved['status'],'ready')
        training.complete(saved['id'],dict(status='done',text='Hey Adam'))
        training.review(saved['id'],Review(revision=0,label='positive',phrase_start_ms=0,
            phrase_end_ms=80,cue_status='excluded',**tags.model_dump()))
        # A completed temporary original must also survive a move after a failed rename.
        from unittest.mock import patch
        with patch('headend.training_archive.os.replace',side_effect=OSError('fixture interrupted write')):
            pending=training.capture(raw,dict(id='pending-original',unit_id=unit['id'],device_name=unit['name'],
                started=time.time(),trigger='touch',preroll_ms=0))
        self.assertIsNotNone(pending['id'])
        model=models/'stt'/'base.en'/'model.bin'
        model.parent.mkdir(parents=True)
        model.write_bytes(b'migration model fixture')
        bundle=self.root/'training-migration.zip'
        export_bundle(data,models,bundle)
        destination=self.root/'restored'/'headend'
        restore_bundle(bundle,destination)
        restored=InteractionArchive(destination/'data'/'training_archive')
        record,original=restored.audio(saved['id'])
        self.assertEqual(original,raw)
        self.assertEqual(record['sha256'],hashlib.sha256(raw).hexdigest())
        self.assertEqual((record['transcript'],record['label'],record['phrase_end_ms']),('Hey Adam','positive',80))
        self.assertEqual(restored.context(unit['id']),tags)
        self.assertEqual(restored.audio(pending['id'])[1],raw)

    def test_reconnect_preserves_identity_and_failed_usb_keeps_old_key(self):
        state = State(self.root/'data')
        unit = state.add_unit('Study Speaker')
        bridge = FakeDevice()
        result = reconnect(state, bridge, unit['id'], 'http://192.168.1.50:8765')
        self.assertEqual(result, dict(id=unit['id'], name=unit['name']))
        self.assertEqual(len(state.units()), 1)
        new_token = bridge.calls[-1][1]['token']
        self.assertTrue(state.unit_auth(unit['id'], new_token))
        self.assertFalse(state.unit_auth(unit['id'], unit['token']))
        def fail(*args):
            raise RuntimeError('USB unavailable')
        bridge.run = fail
        with self.assertRaises(RuntimeError):
            reconnect(state, bridge, unit['id'], 'http://192.168.1.50:8765')
        self.assertTrue(state.unit_auth(unit['id'], new_token))
        with self.assertRaises(ValueError):
            reconnect(state, bridge, unit['id'], 'http://127.0.0.1:8765')
