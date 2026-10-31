"""Check model selection persistence and safe service control without real services."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'deploy/model-host/adam_control.pyw'
loader=importlib.machinery.SourceFileLoader('adam_control', str(SOURCE))
spec=importlib.util.spec_from_loader(loader.name, loader)
module=importlib.util.module_from_spec(spec)
loader.exec_module(module)


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/'config').mkdir()
        self.config={'listen':'192.168.1.20:11434','preferred_model':'old:latest','model_alias':'adam:latest'}
        (self.root/'config/ollama.json').write_text(json.dumps(self.config))
        self.calls=[]
        self.scripts=[]
        self.models=[{'name':'old:latest','digest':'old'}, {'name':'new:latest','digest':'new'}, {'name':'adam:latest','digest':'old'}]
        self.offline=False
        def request(path, data=None, timeout=5):
            self.calls.append((path,data))
            if self.offline:
                raise OSError('offline')
            return {'models':self.models} if path=='/api/tags' else {}
        self.backend=module.Backend(self.root, request, self.scripts.append)

    def test_selection_updates_stable_alias_and_persists(self):
        self.backend.select_model('new:latest')
        self.assertEqual(self.backend.config()['preferred_model'],'new:latest')
        self.assertIn(('/api/copy',{'source':'new:latest','destination':'adam:latest'}),self.calls)
        self.assertEqual(self.calls[-1][1]['model'],'adam:latest')
        self.assertNotIn('adam:latest',[m['name'] for m in self.backend.installed_models()])

    def test_offline_selection_is_applied_at_next_start(self):
        self.backend.installed_models()
        self.offline=True
        self.assertIn('when you start',self.backend.select_model('new:latest'))
        self.assertEqual(self.backend.config()['preferred_model'],'new:latest')
        self.assertFalse(any(path=='/api/copy' for path,_ in self.calls))

    def test_config_write_failure_restores_previous_alias(self):
        with patch.object(self.backend,'save_config',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.backend.select_model('new:latest')
        self.assertEqual(self.calls[-1],('/api/copy',{'source':'old:latest','destination':'adam:latest'}))
        self.assertEqual(self.backend.config()['preferred_model'],'old:latest')

    def test_unknown_model_cannot_change_active_alias(self):
        for name in ('missing:latest', "x'; Stop-Computer", 'adam:latest'):
            with self.assertRaises(ValueError):
                self.backend.select_model(name)
        self.assertFalse(any(path=='/api/copy' for path,_ in self.calls))

    def test_stop_persists_disabled_task_and_scopes_processes(self):
        self.backend.stop('chat')
        script=self.scripts[-1]
        self.assertIn("Disable-ScheduledTask -TaskName 'ADAM-Ollama'",script)
        self.assertIn('ExecutablePath.StartsWith',script)
        self.assertIn('bin',script)
        self.assertNotIn('-Name ollama',script)
        self.backend.start('chat')
        self.assertIn('Enable-ScheduledTask',self.scripts[-1])
        self.backend.stop('speech')
        self.assertIn('stop adam-speech',self.scripts[-1])
        for operation in (self.backend.start,self.backend.stop):
            with self.assertRaises(ValueError):
                operation('unknown')

    def test_load_readings_and_unavailable_tools(self):
        from subprocess import CompletedProcess
        readings=[CompletedProcess([],0,'12\n',''),CompletedProcess([],0,'35, 9216, 12288\n','')]
        with patch.object(module.subprocess,'run',side_effect=readings):
            self.assertEqual(self.backend.system_load(),'CPU 12%   |   GPU 35%   |   GPU memory 9.0 / 12.0 GiB')
        with patch.object(module.subprocess,'run',side_effect=OSError('missing')):
            self.assertEqual(self.backend.system_load(),'CPU unavailable   |   GPU unavailable')


if __name__=='__main__':
    unittest.main()
