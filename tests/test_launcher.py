"""Offline launcher/update checks. Only temporary HOME, fake server and ports 8771+."""
import importlib.util
from contextlib import contextmanager
import os
import signal
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

SOURCE = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('lights_update', SOURCE / 'launcher/update.py')
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)
UPDATE = {'--update-only', '-UpdateOnly'}  # install.sh / install.ps1


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lights-launch-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.old_here = update.HERE
        update.HERE = self.root
        self.addCleanup(setattr, update, 'HERE', self.old_here)
        (self.root / '.version').write_text('a' * 40)
        (self.root / 'state.json').write_text('{"sentinel":true}')
        self.meta = self.root / 'github.json'
        self.meta.write_text(json.dumps({'sha': 'b' * 40}))
        self.config = dict(env={'GOVEE_DRY': '1'}, home=str(self.root), label='test', service='foreground',
                           update_url=self.meta.as_uri(), update_archive=(self.root / '{sha}').as_uri())

    @contextmanager
    def installer(self, action):
        def start(command, **kwargs):
            action(command, **kwargs)
            return MagicMock(wait=MagicMock(return_value=0))
        with patch.object(update.subprocess, 'Popen', side_effect=start), patch.object(update.subprocess, 'run', side_effect=action):
            yield

    def test_update_new_same_failure_off_and_daily(self):
        runs = []
        def install(command, **kwargs):
            runs.append(command)
            (self.root / '.version').write_text(kwargs['env']['GOVEE_VERSION'])
        with self.installer(install):
            self.assertTrue(update.check(self.config))
            self.assertEqual(len(runs), 1)
            self.assertFalse(update.check(self.config, force=True))  # same commit
            self.meta.write_text(json.dumps({'sha': 'c' * 40}))
            self.assertFalse(update.check(self.config))  # daily throttle
            self.assertEqual(len(runs), 1)
            def fail(command, **kwargs):
                if UPDATE & set(command):
                    (self.root / '.version').write_text('broken')
                    (self.root / 'extra.py').touch()
                    raise subprocess.CalledProcessError(1, command)
            with self.installer(fail):
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
            if UPDATE & set(command):
                (self.root / '.version').write_text('b' * 40)
        with self.installer(install):
            self.assertFalse(update.check(self.config, restart=restart))
        self.assertEqual(calls, ['b' * 40, 'a' * 40])

    def test_failures_log_preserve_bad_state_and_retry_in_one_hour(self):
        bad = self.root / 'state.json.bad'
        bad.write_text('original corruption')
        self.config['env']['GOVEE_STATE'] = str(self.root / 'custom.json')
        custom_bad = self.root / 'custom.json.bad'
        custom_bad.write_text('original custom corruption')
        log = self.root / 'lights.log'
        log.write_text('older log\n')
        def fail(command, **kwargs):
            if UPDATE & set(command):
                kwargs['stdout'].write('installer stdout and stderr\n')
                self.assertEqual(kwargs['stderr'], subprocess.STDOUT)
                bad.write_text('new corruption')
                custom_bad.write_text('new custom corruption')
                raise subprocess.CalledProcessError(3, command)
        with self.installer(fail):
            self.assertFalse(update.check(self.config))
        self.assertEqual(bad.read_text(), 'new corruption')
        self.assertEqual(custom_bad.read_text(), 'new custom corruption')
        self.assertTrue(log.read_text().startswith('older log\ninstaller stdout and stderr\n'))
        self.assertIn('exit status 3', log.read_text())
        for response in ('offline', 'bad sha'):
            with self.subTest(response=response):
                if response == 'offline':
                    self.meta.unlink()
                else:
                    self.meta.write_text('{"sha":"bad"}')
                self.assertFalse(update.check(self.config, force=True))
                stamp = (self.root / '.last-update-check').stat().st_mtime
                self.assertAlmostEqual(time.time() - stamp, 86400 - 3600, delta=2)
                with patch.object(update, 'urlopen', side_effect=OSError('retry attempted')) as fetch:
                    with patch.object(update.time, 'time', return_value=stamp + 86399):
                        self.assertFalse(update.check(self.config))
                        fetch.assert_not_called()
                    with patch.object(update.time, 'time', return_value=stamp + 86401):
                        self.assertFalse(update.check(self.config))
                        fetch.assert_called_once()
                self.assertIn('retry attempted', log.read_text())

    def test_log_write_failure_does_not_prevent_rollback(self):
        real_open = Path.open
        log_opens = []
        def open_file(path, *args, **kwargs):
            if path == self.root / 'lights.log':
                log_opens.append(path)
                if len(log_opens) > 1:
                    raise OSError('log is unwritable')
            return real_open(path, *args, **kwargs)
        def fail(command, **kwargs):
            if UPDATE & set(command):
                (self.root / '.version').write_text('broken')
                raise subprocess.CalledProcessError(3, command)
        with patch.object(Path, 'open', open_file), patch.object(update.subprocess, 'Popen', side_effect=fail), \
             patch.object(update.subprocess, 'run', side_effect=fail):
            self.assertFalse(update.check(self.config))
        self.assertEqual((self.root / '.version').read_text(), 'a' * 40)
        self.assertAlmostEqual(time.time() - (self.root / '.last-update-check').stat().st_mtime, 82800, delta=2)

    @unittest.skipIf(os.name == 'nt', 'POSIX process groups only')
    def test_timeout_kills_installer_group_before_rollback(self):
        process = MagicMock(pid=12345)
        process.wait.side_effect = [subprocess.TimeoutExpired('installer', 180), 0]
        with patch.object(update.subprocess, 'Popen', return_value=process) as start, \
             patch.object(update.subprocess, 'run') as rebuild, patch.object(update.os, 'killpg') as kill:
            self.assertFalse(update.check(self.config))
        self.assertTrue(start.call_args.kwargs['start_new_session'])
        kill.assert_called_once_with(12345, signal.SIGKILL)
        self.assertEqual(process.wait.call_count, 2)
        rebuild.assert_called_once()
        self.assertIn('timed out', (self.root / 'lights.log').read_text())
        self.assertAlmostEqual(time.time() - (self.root / '.last-update-check').stat().st_mtime, 82800, delta=2)

    def test_windows_installer_uses_run_without_posix_session(self):
        windows = MagicMock(wraps=update.os)
        windows.name = 'nt'
        windows.killpg = MagicMock()
        with patch.object(update, 'os', windows), patch.object(update.subprocess, 'run') as run, \
             patch.object(update.subprocess, 'Popen') as start:
            self.assertTrue(update.check(self.config))
        start.assert_not_called()
        self.assertEqual(run.call_args.args[0][0], 'powershell.exe')
        self.assertNotIn('start_new_session', run.call_args.kwargs)
        windows.killpg.assert_not_called()

    def test_repeated_open_and_busy_port(self):
        shutil.copytree(SOURCE / 'launcher', self.root / 'launcher')
        self.config.update(start=[])
        (self.root / '.launcher.json').write_text(json.dumps(self.config))
        update.set_enabled(False)
        (self.root / 'server.py').write_text('''import os
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer
from pathlib import Path
Path('started').open('a').write('start' + chr(10))
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')
    def log_message(self, *args): pass
TCPServer.allow_reuse_address = True
TCPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()
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
            subprocess.run(command + ['--stop'], check=True, timeout=30)
            service.wait(timeout=15)
            for process in procs:
                if process.poll() is None:
                    process.terminate()
                process.communicate(timeout=5)
        with self.assertRaises(OSError):  # the server child stopped too
            socket.create_connection(('127.0.0.1', port + 1), timeout=1).close()


if __name__ == '__main__':
    unittest.main()
