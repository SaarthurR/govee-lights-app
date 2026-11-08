"""Dry regressions for the second audit. No device, installer or model calls."""
import copy
import datetime as dt
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

os.environ["GOVEE_DRY"] = "1"
import server as app


class FixChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_save = app.SAVE
        app.SAVE = Path(self.tmp.name) / "state.json"
        for d in (app.lights, app.st, app.schedule, app.themes, app.found, app.outside, app.observed, app.command_at):
            d.clear()
        app.paused.clear()
        app.location = None
        app.add_light("a", "First", "H805D"); app.add_light("b", "Second", "H805D")


    def tearDown(self):
        app.SAVE = self.old_save
        self.tmp.cleanup()


    def post(self, path, body):
        handler = object.__new__(app.H)
        raw = json.dumps(body).encode()
        handler.path, handler.client_address = path, ("127.0.0.1", 0)
        handler.headers = {"Content-Type": "application/json", "Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        result = []
        handler.reply = lambda code, body: result.append((code, body))
        handler.do_POST()
        return result[0]


    def timer(self, time, action="on", id=None):
        return app.clean_timer(dict(time=time, action=action), dict(id=id or time))


    def test_bad_state_sections_keep_original_and_good_sections(self):
        for bad in ("not json", "[]"):
            app.SAVE.write_text(bad)
            app.load()
            backup = app.SAVE.with_name("state.json.bad")
            self.assertEqual(backup.read_text(), bad)
            backup.unlink()
        app.save()
        data = json.loads(app.SAVE.read_text())
        data["_lights"]["bad"] = []
        data["_schedule"]["a"] = []
        data["_schedule"]["b"]["timers"] = [self.timer("18:00")]
        data["a"] = dict(look=dict(colors=[None]), history=[None])
        data["b"]["look"]["colors"] = [[1, 2, 3]]
        data["_themes"] = dict(Bad=[], Good=dict(effect="solid", colors=[[4, 5, 6]]))
        data["_outside"] = dict(a=[dict(at="bad", on=True)], b=[dict(at="2026-11-07T18:00:00", on=True)])
        data["_paused"] = [dict(bad=True)]
        app.SAVE.write_text(json.dumps(data))
        app.load()
        app.save()  # a later good save must not overwrite the bad-file evidence
        self.assertEqual(json.loads(app.SAVE.with_name("state.json.bad").read_text()), data)
        self.assertEqual(app.st["a"]["colors"], app.DEFAULT["colors"])
        self.assertEqual(app.st["a"]["history"], [])
        self.assertEqual(app.st["b"]["colors"], [[1, 2, 3]])
        self.assertEqual(len(app.schedule["b"]["timers"]), 1)
        self.assertEqual(set(app.themes), {"Good"})
        self.assertEqual(len(app.outside["b"]), 1)
        for state in app.st.values():
            app.render(state, 0)


    def test_settings_reject_bad_bulbs_and_non_boolean_power(self):
        before = copy.deepcopy(app.st)
        for settings in (dict(effect="paint", bulbs=[[1]]), dict(bulbs=[[1, 2, 3, 4]]), dict(bulbs=[[True, 0, 0]]), dict(on="false"), dict(on=0)):
            self.assertEqual(self.post("/api/set", dict(light="all", settings=settings))[0], 400)
            self.assertEqual(app.st, before)
        app.push("a", dict(effect="paint", bulbs=[[1, 2, 3]], on=False))
        self.assertEqual(len(app.st["a"]["frame"]), 84)
        self.assertTrue(all(len(c) == 3 for c in app.st["a"]["frame"]))


