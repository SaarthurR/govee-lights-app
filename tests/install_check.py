"""Runs the real installer from this checkout in dry mode (fake lights, local archive), then
checks the server, desktop entry, single instance, reinstall and uninstall.
macOS uses a scratch HOME and test label; Linux and Windows use the real profile, so CI only."""
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler
from socketserver import ThreadingTCPServer
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SHA = '0' * 40  # installed version == "latest", so auto-update stays a no-op
MAC, WIN = sys.platform == 'darwin', os.name == 'nt'
if not MAC and not os.environ.get('CI'):
    sys.exit('This installs into your real profile; run it in CI only.')
home = Path(tempfile.mkdtemp(prefix='lights-home-')) if MAC else Path.home()
label = 'com.govee-lights.citest' if MAC else 'Govee Lights'
app = home / 'govee-lights'
covered = []

files = subprocess.run(['git', 'ls-files', '-co', '--exclude-standard'], cwd=ROOT, check=True,
                       capture_output=True, text=True).stdout.split()
tar_bytes, zip_bytes = io.BytesIO(), io.BytesIO()
with tarfile.open(fileobj=tar_bytes, mode='w:gz') as tar, zipfile.ZipFile(zip_bytes, 'w') as z:
    for name in files:
        if (ROOT / name).is_file():
            tar.add(ROOT / name, 'govee-lights-app-ci/' + name)
            z.write(ROOT / name, 'govee-lights-app-ci/' + name)
served = {'/app.tar.gz': tar_bytes.getvalue(), '/app.zip': zip_bytes.getvalue(), '/commit.json': json.dumps({'sha': SHA}).encode()}


class Files(BaseHTTPRequestHandler):
    def do_GET(self):
        body = served.get(self.path)
        self.send_response(200 if body else 404); self.end_headers(); self.wfile.write(body or b'')
    def log_message(self, *args): pass


files_server = ThreadingTCPServer(('127.0.0.1', 0), Files)  # not HTTPServer: its reverse-DNS lookup stalls on macOS runners
threading.Thread(target=files_server.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{files_server.server_address[1]}'


def answers(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1):
            return True
    except OSError:
        return False


first = next(p for p in range(8771, 8800) if not any(answers(q) for q in range(p, p + 6)))
env = dict(os.environ, GOVEE_DRY='1', GOVEE_FAKE_LIGHTS='2', PORT=str(first), GOVEE_VERSION=SHA, GOVEE_PYTHON=sys.executable,
           GOVEE_TARBALL=base + '/app.tar.gz', GOVEE_ZIP=base + '/app.zip', GOVEE_UPDATE_URL=base + '/commit.json')
if MAC:
    env.update(HOME=str(home), GOVEE_LABEL=label)


def install(shell, *args):
    if WIN:
        command = [shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT / 'install.ps1'), *args]
    else:
        command = ['sh', str(ROOT / 'install.sh'), *args]
    print('$', ' '.join(command), flush=True)
    subprocess.run(command, env=env, cwd=tempfile.gettempdir(), check=True, timeout=600)


def entries():
    if MAC:
        return [home / 'Applications/Govee Lights.app/Contents/MacOS/Govee Lights']
    if WIN:
        script = "[Environment]::GetFolderPath('Desktop'); [Environment]::GetFolderPath('Programs')"
        folders = subprocess.run(['powershell', '-NoProfile', '-Command', script], capture_output=True, text=True, check=True).stdout.split('\n')
        return [Path(f.strip()) / 'Govee Lights.lnk' for f in folders if f.strip()]
    return [home / '.local/share/applications/govee-lights.desktop']


def registered(service):
    command = {'launchd': ['launchctl', 'print', f'gui/{os.getuid() if MAC else 0}/{label}'],
               'systemd': ['systemctl', '--user', 'is-enabled', 'govee-lights.service'],
               'task': ['schtasks', '/Query', '/TN', label]}.get(service)
    return command and subprocess.run(command, capture_output=True).returncode == 0


def wait_up(port):
    for _ in range(120):
        try:
            with urlopen(f'http://127.0.0.1:{port}/api/state', timeout=2) as response:
                return json.load(response)
        except OSError:
            time.sleep(.5)
    print((app / 'lights.log').read_text(errors='replace') if (app / 'lights.log').exists() else 'no lights.log')
    raise AssertionError(f'Lights did not answer on {port}')


def starts():
    return (app / 'lights.log').read_text(errors='replace').count('Govee app: http')


def check_running(shell):
    port = int((app / '.port').read_text())
    assert port == first, f'expected port {first}, got {port} (an old server kept it?)'
    assert 'lights' in wait_up(port)
    config = json.loads((app / '.launcher.json').read_text())
    assert all(p.exists() for p in entries()), entries()
    if MAC:
        native = entries()[0].read_bytes()[:2] != b'#!'
        assert native or not os.environ.get('CI'), 'CI has Xcode, so the native app must build'
        covered.append(f'macOS: native app {"built" if native else "NOT built (fallback script)"}')
    if config['service'] != 'foreground':
        assert registered(config['service']), config['service'] + ' service not registered'
    for _ in range(2):  # what the shortcut does, without opening a window
        url = subprocess.run([sys.executable, str(app / 'launcher/launch.py'), '--url'], env=env,
                             capture_output=True, text=True, check=True, timeout=120).stdout.strip()
        assert url == f'http://localhost:{port}', url
    assert not any(answers(p) for p in range(port + 1, port + 6)), 'a second server started'
    covered.append(f'{shell}: server on {port} via {config["service"]}, entries {", ".join(p.name for p in entries())}')
    return config['service']


def check_removed(service, purge):
    for _ in range(30):
        if not answers(first) and not registered(service):
            break
        time.sleep(.5)
    else:
        if MAC:
            subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{label}'])
        raise AssertionError(f'after uninstall: server answers {answers(first)}, service registered {registered(service)}')
    assert not any(p.exists() for p in entries()), 'desktop entry left behind'
    left = sorted(p.name for p in app.iterdir()) if app.exists() else []
    assert left == [] if purge else set(left) <= {'state.json'}, left


passes = [('pwsh', 'pwsh', ()), ('powershell', 'powershell', ())] if WIN else [('sh', 'sh', ())]
if not MAC and not WIN:  # no user session bus (SSH, containers): the installer must fall back to on-demand start
    passes.append(('sh', 'sh without systemd', ('XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS')))
base_env = env
try:
    for shell, name, drop in passes:
        if not shutil.which(shell):
            covered.append(f'{name}: not on this runner, skipped')
            continue
        env = {k: v for k, v in base_env.items() if k not in drop}
        install(shell)
        service = check_running(name)
        before = starts()
        install(shell)  # run again = update
        check_running(name + ' reinstall')
        assert starts() == before + 1, f'expected one restart, log shows {starts() - before}'
        install(shell, '-Uninstall' if WIN else '--uninstall')
        check_removed(service, purge=False)
        install(shell, '-Purge' if WIN else '--purge')
        check_removed(service, purge=True)
        covered.append(f'{name}: reinstall reused the port; uninstall and purge removed everything')
finally:
    if MAC:
        subprocess.run(['sh', str(ROOT / 'install.sh'), '--purge'], env=env, capture_output=True)
        subprocess.run(['pkill', '-f', str(home)])
        shutil.rmtree(home, ignore_errors=True)
    files_server.shutdown()
print('Covered:\n  ' + '\n  '.join(covered))
