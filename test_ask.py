"""Offline Ask contract checks. No network/device or paid Claude calls."""
import copy
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

os.environ.update(GOVEE_DRY="1", PORT="8771", GOVEE_FAKE_LIGHTS="2")
import server as app


class AskChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        app.SAVE = Path(self.tmp.name) / "state.json"
        app.st.clear(); app.themes.clear(); app.schedule.clear(); app.found.clear()
        app.location = None; app.ask_on = True
        if hasattr(app, "lights"):
            app.lights.clear()
            app.add_light("a", "First", "H805D"); app.add_light("b", "Second", "H805D")
        else:
            for k in app.LIGHTS:
                app.st[k] = dict(app.DEFAULT, history=[], frame=[[0, 0, 0]] * app.N)
                app.schedule[k] = dict(use=True, timers=[])
        self.keys = list(app.st)
        app.chat.update(at=0, turns=[])

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, actions, selected=None):
        raw = json.dumps(dict(say="Done", actions=actions))
        with patch.object(app.subprocess, "run", return_value=SimpleNamespace(stdout=raw, returncode=0)) as run:
            answer = app.ask_claude(self.keys if selected is None else selected, "test")
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("--model") + 1], "haiku")
        self.assertEqual(args[args.index("--tools") + 1], "")
        return app.apply_answer(answer, self.keys if selected is None else selected)

    def test_actions_and_persistence(self):
        a, b = self.keys
        spec = {"frames": [{"pattern": [[255, 0, 0], [0, 0, 255]], "duration": 2, "fade": .5}], "shift": 1, "motion": "center"}
        self.ask([dict(type="set", target="all", settings=dict(on=False, brightness=30, effect="custom", animation=spec)),
                  dict(type="schedule", target="all", schedule=dict(use=False, add=[dict(time="18:00", action="on", days=[0, 1, 2, 3, 4]),
                                                                                     dict(time="00:00", action="off", days=[0, 1, 2, 3, 4])])),
                  dict(type="save_theme", target=a, name="Ocean night"),
                  dict(type="rename_light", target=b, name="Porch")])
        self.assertTrue(all(s["brightness"] == 30 and not s["on"] for s in app.st.values()))
        self.assertTrue(all(not s["use"] and [t["days"] for t in s["timers"]] == [[0, 1, 2, 3, 4]] * 2 for s in app.schedule.values()))
        self.assertIn("animation", app.themes["Ocean night"])
        frame = app.render(app.st[a], .3)
        self.assertEqual(frame, frame[::-1])
        self.assertNotEqual(frame, app.render(app.st[a], 2.3))
        self.assertEqual(len(app.clip(app.themes["Ocean night"])), 36)
        self.ask([dict(type="rename_theme", name="Ocean night", new="Ocean"), dict(type="delete_theme", name="Ocean")])
        self.assertFalse(app.themes)
        self.ask([dict(type="paint", target=a, indices=[0, 83], color=[1, 2, 3]), dict(type="save_theme", target=a, name="Paint")])
        self.assertEqual(app.st[a]["bulbs"][83], [1, 2, 3])
        self.assertEqual(app.themes["Paint"]["bulbs"][0], [1, 2, 3])
        self.ask([dict(type="undo", target=a)])
        self.assertEqual(app.st[a]["effect"], "custom")
        self.ask([dict(type="schedule", target=a, schedule=dict(use=True))])
        self.assertEqual(app.schedule[a]["timers"][1]["time"], "00:00")
        app.save(); app.themes.clear(); app.load()
        self.assertEqual(app.themes["Paint"]["bulbs"][83], [1, 2, 3])
        self.assertEqual(app.st[a]["animation"]["shift"], 1)
        self.assertEqual(len(app.schedule[a]["timers"]), 2)

    def test_timers(self):
        a, b = self.keys
        self.ask([dict(type="schedule", target=a, schedule=dict(add=[dict(time="18:30", action="on", theme="Ocean"), dict(time="23:00", action="off")]))])
        on, off = app.schedule[a]["timers"]
        self.assertEqual((on["theme"], on["days"], off["enabled"]), ("Ocean", list(range(7)), True))
        self.assertEqual(app.schedule[b]["timers"], [])
        self.ask([dict(type="schedule", target="all", schedule=dict(edit=[dict(id=off["id"], enabled=False, time="23:30")]))])
        self.assertEqual((app.schedule[a]["timers"][1]["time"], app.schedule[a]["timers"][1]["enabled"]), ("23:30", False))
        self.assertEqual(app.schedule[a]["timers"][1]["id"], off["id"])
        before = copy.deepcopy(app.schedule)
        for bad in (dict(delete=["missing"]), dict(edit=[dict(id=on["id"], action="dim")]), dict(add=[dict(time="25:00", action="on")]),
                    dict(add=[dict(time="18:00", action="on")] * 9), dict(on="18:00")):
            with self.assertRaises(ValueError):
                self.ask([dict(type="set", settings=dict(brightness=10)), dict(type="schedule", target=a, schedule=bad)])
            self.assertEqual(before, app.schedule)
        self.ask([dict(type="schedule", target=a, schedule=dict(delete=[on["id"], off["id"]]))])
        self.assertEqual(app.schedule[a]["timers"], [])

    def test_animation_timing(self):
        spec = app.clean_animation({"frames": [{"pattern": [[255, 0, 0]], "duration": 1, "fade": 1},
                                                 {"pattern": [[0, 0, 255]], "duration": 1}], "block": 42})
        self.assertEqual(app.custom_frame(spec, .5, 84)[0], [127.5, 0, 127.5])
        self.assertEqual(app.custom_frame(spec, 1, 84)[0], [0, 0, 255])
        self.assertEqual(app.custom_frame(spec, 2, 84)[0], [255, 0, 0])

    def test_bad_specs_and_batches_change_nothing(self):
        valid = {"frames": [{"pattern": [[255, 0, 0]], "duration": 1}]}
        bad = [None, {}, {"frames": []}, {"frames": valid["frames"] * 17}, dict(valid, shift=85), dict(valid, block=0), dict(valid, block=True), dict(valid, motion="exec"), dict(valid, code="x")]
        for field, value in (("pattern", [[256, 0, 0]]), ("pattern", [[True, 0, 0]]), ("pattern", [[1, 2]]), ("pattern", [[0, 0, 0]] * 85), ("duration", .01), ("duration", float("nan")), ("duration", 61), ("fade", 2)):
            f = dict(valid["frames"][0]); f[field] = value; bad.append({"frames": [f]})
        for spec in bad:
            before = copy.deepcopy((app.st, app.schedule, app.themes))
            with self.assertRaises((ValueError, TypeError)):
                self.ask([dict(type="set", settings=dict(brightness=30)), dict(type="set", settings=dict(effect="custom", animation=spec))])
            self.assertEqual(before, (app.st, app.schedule, app.themes))
        for action in (dict(type="shell", code="x"), dict(type="set", settings=dict(on="false")), dict(type="set", settings=dict(speed=float("inf"))), dict(type="rename_light", target="missing", name="X"), dict(type="schedule", schedule=dict(use="yes"))):
            with self.assertRaises(ValueError):
                self.ask([action])
        with self.assertRaises(ValueError):
            self.ask([dict(type="set", settings=dict(effect="solid")), dict(type="undo"), dict(type="undo")])

    def test_clarification_json_and_context(self):
        before = copy.deepcopy(app.st)
        self.assertFalse(self.ask([])[1])
        self.assertEqual(before, app.st)
        for raw in ('prose {"say":"Hi","actions":[]}', '```json\n{}\n```'):
            with patch.object(app.subprocess, "run", return_value=SimpleNamespace(stdout=raw, returncode=0)):
                with self.assertRaises(ValueError): app.ask_claude(self.keys, "test")
        app.st[self.keys[0]]["bulbs"] = [[1, 2, 3]] * 84
        self.assertNotIn("bulbs", json.loads(app.ask_prompt(self.keys, "dim it"))["lights"][self.keys[0]]["look"])
        self.assertIn("bulbs", json.loads(app.ask_prompt(self.keys, "paint bulb 1"))["lights"][self.keys[0]]["look"])
        app.chat.update(at=0, turns=[["old", "old", False]])
        self.assertEqual(app.chat_view()["turns"], [])

    def test_no_light_themes_location_and_questions(self):
        app.lights.clear(); app.st.clear(); app.schedule.clear()
        with patch.object(app, "FAKE", 0):
            self.assertEqual(app.discover(), {})
        self.assertEqual(app.ask_keys("all"), [])
        self.ask([], [])
        self.ask([dict(type="save_theme", name="Ember", settings=dict(effect="fire", colors=[[255, 60, 0]])),
                  dict(type="rename_theme", name="Ember", new="Glow"), dict(type="location", lat=0, lon=0)], [])
        self.assertEqual(app.themes["Glow"]["effect"], "fire")
        self.assertEqual(app.location, [0, 0])
        self.ask([dict(type="delete_theme", name="Glow")], [])
        for kind in ("set", "paint", "schedule", "apply_theme", "rename_light", "undo", "save_theme"):
            with self.assertRaisesRegex(ValueError, "Add a light"):
                self.ask([dict(type=kind)], [])
        self.assertFalse(app.themes)

    def test_theme_dependencies_and_full_schedule(self):
        self.ask([dict(type="location", lat=0, lon=0),
                  dict(type="save_theme", name="Ember", settings=dict(effect="fire", brightness=50)),
                  dict(type="apply_theme", target="all", name="Ember"),
                  dict(type="schedule", target="all", schedule=dict(add=[dict(time="sunset-20", action="on", days=[5, 6], theme="Ember"),
                                                                          dict(time="00:00", action="off", days=[5, 6])]))])
        self.assertTrue(all(s["effect"] == "fire" and s["brightness"] == 100 and s["on"] for s in app.st.values()))
        self.assertTrue(all(s["timers"][0]["theme"] == "Ember" for s in app.schedule.values()))
        self.ask([dict(type="rename_theme", name="Ember", new="Glow")])
        self.assertTrue(all(s["timers"][0]["theme"] == "Glow" for s in app.schedule.values()))
        self.ask([dict(type="schedule", target="all", schedule=dict(add=[dict(time=f"{h:02}:00", action="on") for h in range(8)]))])
        self.assertTrue(all(len(s["timers"]) == 10 for s in app.schedule.values()))
        self.ask([dict(type="set", settings=dict(effect="solid", bulbs=None, animation=None, colors=[[1, 2, 3]], speed=.05, amount=1))])
        before = copy.deepcopy((app.st, app.themes, app.schedule, app.location))
        for bad in (dict(type="location", lat=float("nan"), lon=0),
                    dict(type="save_theme", name="Bad", settings=dict(on="yes")),
                    dict(type="apply_theme", name="missing"),
                    dict(type="set", settings=dict(bulbs=[[1, 2, 3]]))):
            with self.assertRaises((ValueError, TypeError)):
                self.ask([dict(type="location", lat=1, lon=1), bad])
            self.assertEqual(before, (app.st, app.themes, app.schedule, app.location))
        with self.assertRaisesRegex(ValueError, "16 actions"):
            self.ask([dict(type="undo")] * 17)

    def test_setup_network_and_chat_actions(self):
        with patch.object(app, "flash") as flash:
            self.ask([dict(type="flash", target="all")])
            self.assertEqual(flash.call_count, 2)
        with patch.object(app, "discover", return_value=dict.fromkeys(self.keys)), patch.object(app, "reconnect") as reconnect:
            self.ask([dict(type="reconnect", target="all")])
            self.assertEqual(reconnect.call_count, 2)
            self.ask([dict(type="scan")], [])
        with patch.object(app, "import_devices") as refresh, patch.object(app, "disconnect_govee") as disconnect:
            self.ask([dict(type="govee_refresh")], [])
            self.ask([dict(type="govee_disconnect")], [])
            refresh.assert_called_once(); disconnect.assert_called_once()
        with patch.object(app, "imported_scenes", return_value=dict(scenes=[])) as scenes:
            self.ask([dict(type="govee_scenes", target=self.keys[0])])
            scenes.assert_called_once_with(self.keys[0])
        self.ask([dict(type="ask_use", use=False)])
        self.assertFalse(app.ask_on)
        app.chat.update(at=1, turns=[["old", "reply", False]])
        self.ask([dict(type="clear_chat")])
        self.assertEqual(app.chat["turns"], [])
        with patch.object(app, "FAKE", 2):
            app.discover()
        discovered = next(iter(app.found))
        self.ask([dict(type="save_lights", lights=[dict(id=discovered, name="Third")])], [])
        self.assertIn(discovered, app.st)
        self.ask([dict(type="forget_light", target=discovered)])
        self.assertNotIn(discovered, app.st)
        with patch.object(app, "flash") as flash:
            for batch in ([dict(type="flash"), dict(type="set", settings=dict(on=False))],
                          [dict(type="set", settings=dict(on=False)), dict(type="flash")]):
                with self.assertRaises(ValueError):
                    self.ask(batch)
            flash.assert_not_called()

    def test_http_selection_lifecycle(self):
        # Exercise the real handler; only the paid model call is stubbed.
        http = app.ThreadingHTTPServer(("localhost", 8771), app.H)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        answer = dict(say="Ready", actions=[])
        def post(light, text="question"):
            req = app.request.Request("http://localhost:8771/api/ask", data=json.dumps(dict(light=light, text=text)).encode(),
                                      headers={"Content-Type": "application/json"})
            try:
                with app.request.urlopen(req) as r:
                    return r.status, json.load(r)
            except app.error.HTTPError as e:
                with e:
                    return e.code, json.load(e)
        try:
            with patch.object(app, "ask_claude", side_effect=lambda keys, text: (json.loads(app.ask_prompt(keys, text)), answer)[1]) as model:
                for selected, expected in (("all", self.keys), ("", []), (None, []), ("gone", []), (self.keys[0], [self.keys[0]])):
                    self.assertEqual(post(selected)[0], 200)
                    self.assertEqual(model.call_args.args[0], expected)
                app.apply_answer(dict(say="Renamed", actions=[dict(type="rename_light", target=self.keys[0], name="Renamed")]), self.keys)
                self.assertEqual(app.ask_keys(self.keys[0]), [self.keys[0]])
                app.forget_light(self.keys[0])
                self.assertEqual(post(self.keys[0])[0], 200)
                self.assertEqual(model.call_args.args[0], [])
                answer = dict(say="Done", actions=[dict(type="set", settings=dict(on=True))])
                self.assertIn("Pick a saved light", post(self.keys[0])[1]["error"])
                app.forget_light(self.keys[1])
                self.assertIn("Add a light", post("all")[1]["error"])
                answer = dict(say="Created", actions=[dict(type="save_theme", name="Ember", settings=dict(effect="fire"))])
                self.assertEqual(post("all")[0], 200)
                self.assertIn("Ember", app.themes)
                answer = dict(say="Cleared", actions=[dict(type="clear_chat")])
                self.assertEqual(post("")[1]["turns"], [])
        finally:
            http.shutdown(); http.server_close(); thread.join()


if __name__ == "__main__":
    unittest.main()
