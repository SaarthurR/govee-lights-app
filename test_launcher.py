"""Offline launcher/update checks. Only temporary HOME, fake server and ports 8771+."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('lights_update', SOURCE / 'launcher/update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lights-launch-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old_here = update.HERE
        update.HERE = self.root
        self.addCleanup(setattr, update, 'HERE', self.old_here)
        (self.root / '.version').write_text('a' * 40)
        (self.root / 'state.json').write_text('{"sentinel":true}')
        self.meta = self.root / 'github.json'
        self.meta.write_text(json.dumps({'sha': 'b' * 40}))
        self.config = dict(env={'GOVEE_DRY': '1'}, home=str(self.root), label='test', service='foreground',
                           update_url=self.meta.as_uri(), update_archive=(self.root / '{sha}').as_uri())

    def test_update_new_same_failure_off_and_daily(self):
        runs = []
        def install(command, **kwargs):
            runs.append(command)
            (self.root / '.version').write_text(kwargs['env']['GOVEE_VERSION'])
        with patch.object(update.subprocess, 'run', side_effect=install):
            self.assertTrue(update.check(self.config))
            self.assertEqual(len(runs), 1)
            self.assertFalse(update.check(self.config, force=True))  # same commit
            self.meta.write_text(json.dumps({'sha': 'c' * 40}))
            self.assertFalse(update.check(self.config))  # daily throttle
            self.assertEqual(len(runs), 1)
            def fail(command, **kwargs):
                if '--update-only' in command:
                    (self.root / '.version').write_text('broken')
                    (self.root / 'extra.py').touch()
                    raise subprocess.CalledProcessError(1, command)
            with patch.object(update.subprocess, 'run', side_effect=fail):
                self.assertFalse(update.check(self.config, force=True))
            self.assertEqual((self.root / '.version').read_text(), 'b' * 40)
            self.assertFalse((self.root / 'extra.py').exists())
            self.assertEqual((self.root / 'state.json').read_text(), '{"sentinel":true}')
            update.set_enabled(False)
            with patch.object(update, 'urlopen', side_effect=AssertionError('disabled must not check')):
                self.assertFalse(update.check(self.config, force=True))
        update.set_enabled(True)
        self.meta.unlink()
        self.assertFalse(update.check(self.config, force=True))  # offline
        self.assertEqual((self.root / '.version').read_text(), 'b' * 40)

    def test_startup_failure_rolls_back(self):
        calls = []
        def restart():
            calls.append((self.root / '.version').read_text())
            if len(calls) == 1:
                raise RuntimeError('new server failed')
        def install(command, **kwargs):
            if '--update-only' in command:
                (self.root / '.version').write_text('b' * 40)
        with patch.object(update.subprocess, 'run', side_effect=install):
            self.assertFalse(update.check(self.config, restart=restart))
        self.assertEqual(calls, ['b' * 40, 'a' * 40])

    def test_repeated_open_and_busy_port(self):
        shutil.copytree(SOURCE / 'launcher', self.root / 'launcher')
        self.config.update(start=[])
        (self.root / '.launcher.json').write_text(json.dumps(self.config))
        update.set_enabled(False)
        (self.root / 'server.py').write_text('''import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
Path('started').open('a').write('start' + chr(10))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
    def log_message(self, *args): pass
Path('child-pid').write_text(str(os.getpid()))
HTTPServer.allow_reuse_address = True
HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
''')
        for port in range(8771, 8900):
            busy = socket.socket()
            try:
                busy.bind(('127.0.0.1', port)); busy.listen()
                with socket.socket() as probe:
                    probe.bind(('127.0.0.1', port + 1))
                break
            except OSError:
                busy.close()
                continue
        else:
            self.fail('No spare test ports')
        self.addCleanup(busy.close)
        (self.root / '.port').write_text(str(port))
        command = [sys.executable, str(self.root / 'launcher/launch.py')]
        service = subprocess.Popen(command + ['--server'])
        procs = []
        try:
            for _ in range(50):
                if (self.root / 'started').exists():
                    break
                time.sleep(.1)
            procs = [subprocess.Popen(command + ['--url'], stdout=subprocess.PIPE) for _ in range(3)]
            urls = [p.communicate(timeout=15)[0].decode().strip() for p in procs]
            self.assertEqual(urls, [f'http://localhost:{port + 1}'] * 3)
            self.assertEqual((self.root / 'started').read_text(), 'start\n')
        finally:
            service.terminate()
            service.wait(timeout=15)
            subprocess.run(command + ['--stop'], check=True, timeout=15)
            for process in procs:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=5)
        with self.assertRaises(ProcessLookupError):
            os.kill(int((self.root / 'child-pid').read_text()), 0)


if __name__ == '__main__':
    unittest.main()
