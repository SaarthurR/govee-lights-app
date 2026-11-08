"""Check installer ordering and the isolated shell rollback helper, never an installer."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parent.parent


class InstallFixChecks(unittest.TestCase):
    def test_download_and_validation_precede_manual_stop_on_both_platforms(self):
        shell = (SOURCE / "install.sh").read_text()
        body = shell[shell.index('  version=${GOVEE_VERSION:-}'):]
        self.assertLess(body.index('curl -fsSL'), body.index('-m py_compile'))
        self.assertLess(body.index('-m py_compile'), body.index('    stop_lights'))
        self.assertLess(body.index('    stop_lights'), body.index('cp -R "$tmp/app/."'))
        windows = (SOURCE / "install.ps1").read_text()
        body = windows[windows.index('    $version = $env:GOVEE_VERSION'):]
        self.assertLess(body.index('Invoke-WebRequest'), body.index('-m py_compile'))
        self.assertLess(body.index('-m py_compile'), body.index('      Stop-Lights'))
        self.assertLess(windows.index("missing pythonw.exe"), windows.index('    $version = $env:GOVEE_VERSION'))
        self.assertLess(body.index('      Stop-Lights'), body.index('Get-ChildItem $source -Force'))
        recovery = body[body.index('    $failure = $_'):]
        self.assertIn('Register-ScheduledTask -TaskName $taskName -Xml $taskXml -Force', recovery)
        self.assertIn('Start-ScheduledTask -TaskName $taskName', recovery)
        self.assertLess(recovery.index('Copy-Item -Destination $dir'), recovery.index('Start-ScheduledTask'))
        self.assertIn('throw $failure', recovery)
        self.assertIn('$keepBackup = $true', recovery)
        self.assertIn('if (-not $keepBackup)', recovery)

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell unavailable")
    def test_shell_failure_restores_code_and_service_but_keeps_current_state(self):
        source = (SOURCE / "install.sh").read_text()
        helper = source[source.index('  finish() {'):source.index('  trap finish EXIT')]
        # Only the recovery function runs; every service action is a stub, all files are scratch.
        helper = helper.replace('$HOME', '$test_home')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            live, staged = root / "live", root / "staged"
            live.mkdir(); (staged / "old").mkdir(parents=True)
            (live / "server.py").write_text("broken")
            (live / "extra.py").write_text("new file")
            (live / "state.json").write_text("current state")
            (live / "state.json.bad").write_text("corrupt evidence")
            (live / ".server.lock").write_text("lock")
            (staged / "old/server.py").write_text("old working code")
            (staged / "agent").write_text("old service config")
            (root / "agent").write_text("broken service config")
            script = ("root=$1\ndir=\"$root/live\"; tmp=\"$root/staged\"; test_home=\"$root/home\"\n"
                      "stopped=1; service=launchd; label=test; agent=\"$root/agent\"; unit=\"$root/unit\"\n"
                      "stop_lights() { echo stopped >> \"$root/events\"; }\n"
                      "launchctl() { echo \"$*\" >> \"$root/events\"; }\n" + helper + "\nfalse\nfinish\n")
            result = subprocess.run(["sh", "-c", script, "rollback-test", folder], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual((live / "server.py").read_text(), "old working code")
            self.assertFalse((live / "extra.py").exists())
            self.assertEqual((live / "state.json").read_text(), "current state")
            self.assertEqual((live / "state.json.bad").read_text(), "corrupt evidence")
            self.assertEqual((live / ".server.lock").read_text(), "lock")
            self.assertEqual((root / "agent").read_text(), "old service config")
            self.assertIn("bootstrap gui/", (root / "events").read_text())
            self.assertFalse(staged.exists())

    @unittest.skipUnless(shutil.which("sh"), "POSIX shell unavailable")
    def test_failed_rollback_keeps_backup_and_does_not_start_broken_code(self):
        source = (SOURCE / "install.sh").read_text()
        helper = source[source.index('  finish() {'):source.index('  trap finish EXIT')].replace('$HOME', '$test_home')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "live").mkdir()
            (root / "staged/old").mkdir(parents=True)
            (root / "live/state.json").write_text("current state")
            (root / "staged/old/server.py").write_text("old working code")
            script = ("root=$1\ndir=\"$root/live\"; tmp=\"$root/staged\"; test_home=\"$root/home\"\n"
                      "stopped=1; service=launchd; label=test; agent=\"$root/agent\"; unit=\"$root/unit\"\n"
                      "stop_lights() { :; }\ncp() { return 1; }\nlaunchctl() { exit 99; }\n" + helper + "\nfalse\nfinish\n")
            result = subprocess.run(["sh", "-c", script, "rollback-test", folder], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual((root / "staged/old/server.py").read_text(), "old working code")
            self.assertEqual((root / "live/state.json").read_text(), "current state")
            self.assertIn("Previous files are kept", result.stderr)
