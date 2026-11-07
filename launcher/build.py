"""Create/update desktop entries; invoked with the installer's chosen Python."""
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent.parent
service, label = sys.argv[1:]
launch = HERE / 'launcher/launch.py'
start = {'launchd': ['launchctl', 'kickstart', f'gui/{os.getuid()}/{label}'] if hasattr(os, 'getuid') else [],
         'systemd': ['systemctl', '--user', 'start', 'govee-lights.service'],
         'task': ['schtasks', '/Run', '/TN', label], 'foreground': []}[service]
env = {k: os.environ[k] for k in ('GOVEE_DRY', 'GOVEE_FAKE_LIGHTS', 'GOVEE_FAKE_SKUS', 'GOVEE_STATE') if k in os.environ}
(HERE / '.launcher.json').write_text(json.dumps(dict(start=start, env=env, python=sys.executable, home=str(Path.home()), label=label, service=service,
    **{key: os.environ[var] for key, var in (('update_url', 'GOVEE_UPDATE_URL'), ('update_archive', 'GOVEE_UPDATE_ARCHIVE')) if var in os.environ})))
if sys.platform == 'darwin':
    app = Path.home() / 'Applications/Govee Lights.app'
    mac = app / 'Contents/MacOS'
    resources = app / 'Contents/Resources'
    mac.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)
    executable = mac / 'Govee Lights'
    # Remove the old binary before compiling so a failed update uses the fallback.
    executable.unlink(missing_ok=True)
    compiler = shutil.which('swiftc')
    if subprocess.run(['xcode-select', '-p'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        compiler = None  # /usr/bin/swiftc can be a stub that prompts to install developer tools.
    if not compiler or subprocess.run([compiler, str(HERE / 'launcher/App.swift'), '-o', str(executable)]).returncode:
        executable.write_text('#!/bin/sh\nexec ' + shlex.join([sys.executable, str(launch)]) + '\n')
        executable.chmod(0o755)
    shutil.copyfile(HERE / 'launcher/icon.icns', resources / 'Lights.icns')
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(dict(
        CFBundleName='Govee Lights', CFBundleDisplayName='Govee Lights',
        CFBundleIdentifier=label + '.desktop', CFBundleExecutable='Govee Lights',
        CFBundlePackageType='APPL', CFBundleIconFile='Lights', CFBundleVersion='1',
        NSHighResolutionCapable=True, NSAppTransportSecurity={'NSAllowsLocalNetworking': True}, NSLocalNetworkUsageDescription='Connect to your local Lights server.',
        LightsPython=sys.executable, LightsLauncher=str(launch))))
    register = Path('/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister')
    if register.exists():
        subprocess.run([str(register), '-f', str(app)], check=False)
elif sys.platform != 'win32':
    entry = Path.home() / '.local/share/applications/govee-lights.desktop'
    entry.parent.mkdir(parents=True, exist_ok=True)
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    entry.write_text('[Desktop Entry]\nType=Application\nName=Govee Lights\n' +
                     'Exec=' + quote(sys.executable) + ' ' + quote(launch) + '\n' +
                     'Icon=' + str(HERE / 'docs/icon.svg') + '\nTerminal=false\nCategories=Utility;\n')
    entry.chmod(0o755)
