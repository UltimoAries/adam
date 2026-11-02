from collections import namedtuple
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from .server import create_app
from .system_monitor import SystemMonitor, host_counters

Disk = namedtuple('Disk', 'total used free')


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.counters = Mock(return_value=((1000, 800), (1000, 600)))
        self.disk = Mock(return_value=Disk(2000, 500, 1500))
        self.monitor = SystemMonitor('data', self.counters, self.disk, lambda: self.now, lambda: 1234)

    def test_cpu_delta_and_cache(self):
        self.assertIsNone(self.monitor.snapshot()['cpu_percent'])
        self.assertEqual(self.counters.call_count, 1)
        self.now = 5
        self.counters.return_value = ((1200, 950), (1000, 400))
        result = self.monitor.snapshot()
        self.assertEqual(result['cpu_percent'], 25)
        self.assertEqual(result['memory']['percent'], 60)
        self.assertEqual(result['memory']['used_bytes'], 600)
        self.assertEqual(result['storage']['percent'], 25)
        self.disk.assert_called_with('data')
        self.monitor.snapshot()
        self.assertEqual(self.counters.call_count, 2)

    def test_missing_counters_and_disk_recover_without_stale_values(self):
        self.now = 5
        self.counters.side_effect = OSError('private host details')
        self.disk.side_effect = OSError('private path')
        self.assertEqual(self.monitor.snapshot(), dict(sampled_at=1234, cpu_percent=None, memory=None, storage=None))
        self.now = 10
        self.counters.side_effect = None
        self.assertIsNone(self.monitor.snapshot()['cpu_percent'])
        self.assertIsNotNone(self.monitor.snapshot()['memory'])

    def test_counter_reset_is_not_reported_as_usage(self):
        self.now = 5
        self.counters.return_value = ((100, 80), (1000, 600))
        self.assertIsNone(self.monitor.snapshot()['cpu_percent'])

    def test_linux_excludes_double_counted_guest_and_uses_available_memory(self):
        with patch('headend.system_monitor.os.name', 'posix'), patch('headend.system_monitor.Path') as path:
            path.return_value.read_text.side_effect = [
                'cpu 100 20 30 400 50 10 5 5 60 10\ncpu0 0 0 0 0\n',
                'MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 600 kB\n']
            self.assertEqual(host_counters(), ((620, 450), (1024000, 614400)))

    def test_api_requires_admin_and_omits_host_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(data_directory=directory, testing=True, auth_required=True)
            with TestClient(app) as client:
                self.assertEqual(client.get('/api/system/resources').status_code, 401)
                app.state.auth.setup('admin', 'monitor-test-password')
                self.assertEqual(client.post('/api/auth/login', json={
                    'username': 'admin', 'password': 'monitor-test-password'}).status_code, 200)
                response = client.get('/api/system/resources')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual(set(response.json()), {'sampled_at', 'cpu_percent', 'memory', 'storage'})
                self.assertNotIn(directory, response.text)


if __name__ == '__main__':
    unittest.main()
