"""Commit checks and installer reuse; no light commands or state-file writes."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

HERE = Path(__file__).resolve().parent.parent
PYTHON = str(Path(sys.executable).with_name('python.exe')) if os.name == 'nt' else sys.executable
HIDDEN = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
API = 'https://api.github.com/repos/SaarthurR/govee-lights-app/commits/main'
ARCHIVE = 'https://github.com/SaarthurR/govee-lights-app/archive/{sha}'
# python.org's macOS Python has no CA certificates until "Install Certificates" is run.
CERTS = ssl.create_default_context(cafile='/etc/ssl/cert.pem') if sys.platform == 'darwin' else None


def settings():
    return {'enabled': not (HERE / '.auto-update-off').exists(),
            'version': (HERE / '.version').read_text().strip() if (HERE / '.version').exists() else 'development'}


def set_enabled(enabled):
    if type(enabled) is not bool:
        raise ValueError('Auto-update must be on or off.')
    if enabled:
        (HERE / '.auto-update-off').unlink(missing_ok=True)
    else:
        (HERE / '.auto-update-off').touch()


def check(config, force=False, restart=None):
    if not settings()['enabled']:
        return False
    checked = HERE / '.last-update-check'
    if not force and checked.exists() and time.time() - checked.stat().st_mtime < 86400:
        return False
    try:
        checked.touch()
        with urlopen(config.get('update_url', API), timeout=10, context=CERTS) as response:
            sha = json.load(response)['sha']
        if not re.fullmatch('[0-9a-f]{40}', sha):
            raise ValueError('Invalid update commit: ' + str(sha))
        if sha == settings()['version'] or not settings()['enabled']:
            return False
        env = dict(os.environ, **config['env'], HOME=config['home'], GOVEE_VERSION=sha, GOVEE_PYTHON=PYTHON)
        env.update({var: config[key] for key, var in (('update_url', 'GOVEE_UPDATE_URL'), ('update_archive', 'GOVEE_UPDATE_ARCHIVE')) if key in config})
        archive = config.get('update_archive', ARCHIVE).format(sha=sha)
        if os.name == 'nt':
            env['GOVEE_ZIP'] = archive + '.zip'
            command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(HERE / 'install.ps1'), '-UpdateOnly']
        else:
            env['GOVEE_TARBALL'] = archive + '.tar.gz'
            env['GOVEE_LABEL'] = config['label']
            command = ['sh', str(HERE / 'install.sh'), '--update-only']
        # The installed service stays alive until installation succeeds. Keep a
        # rollback copy of code, excluding state and files owned by running processes.
        keep = {'state.json', 'state.json.bad', '.server.lock', 'lights.log', '.auto-update-off', '.last-update-check', '.check-update', '.stop-server'}
        state = Path(env.get('GOVEE_STATE', HERE / 'state.json')).resolve()
        if state.parent == HERE:
            keep.update({state.name, state.name + '.bad'})
        with tempfile.TemporaryDirectory(prefix='lights-update-') as temp:
            backup = Path(temp) / 'old'
            shutil.copytree(HERE, backup, ignore=lambda directory, names: keep if Path(directory) == HERE else {'__pycache__'})
            restarting = False
            try:
                with (HERE / 'lights.log').open('a') as log:
                    if os.name == 'nt':
                        subprocess.run(command, env=env, cwd=temp, check=True, timeout=180,
                                       stdout=log, stderr=subprocess.STDOUT, **HIDDEN)
                    else:
                        process = subprocess.Popen(command, env=env, cwd=temp, start_new_session=True,
                                                   stdout=log, stderr=subprocess.STDOUT)
                        try:
                            code = process.wait(timeout=180)
                        except subprocess.TimeoutExpired:
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except ProcessLookupError:
                                pass
                            process.wait()
                            raise
                        if code:
                            raise subprocess.CalledProcessError(code, command)
                if not settings()['enabled']:
                    raise RuntimeError('Auto-update was turned off')
                if restart:
                    restarting = True
                    restart()
            except Exception as exc:
                try:
                    with (HERE / 'lights.log').open('a') as log:
                        log.write(f'Update failed: {exc}\n')
                except OSError:
                    pass
                for path in HERE.iterdir():
                    if path.name not in keep:
                        if path.is_dir(): shutil.rmtree(path)
                        else: path.unlink()
                shutil.copytree(backup, HERE, dirs_exist_ok=True)
                # Restore desktop entries too if the installer failed during build.
                subprocess.run([PYTHON, str(HERE / 'launcher/build.py'), config['service'], config['label']],
                               env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, **HIDDEN)
                if restart and restarting:
                    restart()
                raise
        return True
    except Exception as exc:
        try:
            with (HERE / 'lights.log').open('a') as log:
                log.write(f'Update failed: {exc}\n')
        except OSError:
            pass
        try:
            retry = time.time() - 86400 + 3600
            os.utime(checked, (retry, retry))
        except OSError:
            pass
        return False  # Offline, bad downloads and failed installs leave the running app alone.
