"""Installed desktop launcher and single-instance server entry point (stdlib only)."""
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser
import update

HERE = Path(__file__).resolve().parent.parent


def server_lock():
    handle = (HERE / '.server.lock').open('a+b')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            # Windows byte-range locks need a byte even on a new file.
            if not handle.read(1):
                handle.write(b'0'); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def serve(config):
    handle = server_lock()
    if handle is None:
        return
    os.chdir(HERE)
    os.environ.update(config['env'])
    first = int((HERE / '.port').read_text())
    for port in range(first, first + 6):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                continue
        except OSError:
            pass
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('0.0.0.0', port))
                probe.listen(1)
            except OSError:
                continue
        (HERE / '.port').write_text(str(port))
        os.environ['PORT'] = str(port)
        log = (HERE / 'lights.log').open('ab')
        def start_child():
            return subprocess.Popen([update.PYTHON, '-u', str(HERE / 'server.py')],
                                    stdout=log, stderr=log, **update.HIDDEN)
        child = start_child()
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
        def restart():
            nonlocal child
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()
            child = start_child()
            for _ in range(60):
                if child.poll() is not None:
                    break
                try:
                    with urlopen(f'http://127.0.0.1:{port}/api/state', timeout=1):
                        return
                except OSError:
                    time.sleep(.5)
            raise RuntimeError('Updated server could not start')
        try:
            while True:
                if (HERE / '.stop-server').exists():
                    (HERE / '.stop-server').unlink(missing_ok=True)
                    return
                marker = HERE / '.check-update'
                force = marker.exists()
                marker.unlink(missing_ok=True)
                if update.check(config, force=force, restart=restart) and config['service'] == 'launchd':
                    return  # launchd restarts the rebuilt app so Local Network access matches its new binary
                if child.poll() is not None:
                    raise RuntimeError('Lights server stopped.')
                time.sleep(1)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            log.close()
    raise RuntimeError(f'Ports {first} to {first + 5} are busy.')


def ensure_server(config):
    if update.settings()['enabled']:
        (HERE / '.check-update').touch()
    # Starting the managed service preserves login/crash recovery. Its server lock
    # also excludes a simultaneous desktop launch or repeated service start.
    command = config['start']
    started = command and subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, **update.HIDDEN).returncode == 0
    if not started:
        with (HERE / 'lights.log').open('ab') as log:
            kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
            subprocess.Popen([update.PYTHON, str(Path(__file__).resolve()), '--server'],
                             cwd=HERE, stdout=log, stderr=log, **kwargs)
    for _ in range(120):
        handle = server_lock()
        if handle is None:
            port = (HERE / '.port').read_text().strip()
            try:
                # 127.0.0.1: the server is IPv4-only and Windows takes ~2 s to refuse localhost's ::1 first.
                with urlopen(f'http://127.0.0.1:{port}/api/state', timeout=1) as response:
                    if response.status == 200:
                        return 'http://localhost:' + port
            except OSError:
                pass
        else:
            handle.close()
        time.sleep(.5)
    raise RuntimeError('Lights could not start. See lights.log in the installation folder.')


def open_page(url):
    if sys.platform == 'darwin':
        candidates = [Path(root) / (name + '.app/Contents/MacOS/' + name)
                      for root in ('/Applications', str(Path.home() / 'Applications'))
                      for name in ('Google Chrome', 'Microsoft Edge')]
    elif os.name == 'nt':
        candidates = [Path(os.environ.get(key, '')) / 'Microsoft/Edge/Application/msedge.exe'
                      for key in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA')]
    else:
        candidates = [Path(p) for name in ('microsoft-edge', 'google-chrome', 'chromium', 'chromium-browser')
                      if (p := shutil.which(name))]
    for browser in candidates:
        if browser.is_file():
            subprocess.Popen([str(browser), '--app=' + url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **update.HIDDEN)
            return
    webbrowser.open(url)


if __name__ == '__main__':
    config = json.loads((HERE / '.launcher.json').read_text())
    if '--stop' in sys.argv:
        (HERE / '.stop-server').touch()
        for _ in range(240):
            handle = server_lock()
            if handle is not None:
                handle.close()
                (HERE / '.stop-server').unlink(missing_ok=True)
                break
            time.sleep(.5)
        else:
            raise RuntimeError('Lights is still running; try uninstall again.')
    elif '--server' in sys.argv:
        serve(config)
    else:
        url = ensure_server(config)
        if '--url' in sys.argv:
            print(url)
        else:
            open_page(url)
