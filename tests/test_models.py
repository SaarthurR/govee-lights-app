"""Capability checks with fake lights only; no network or Claude calls."""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.update(GOVEE_DRY="1", PORT="8771", GOVEE_FAKE_LIGHTS="2")
import server as app


class ModelChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_save = app.SAVE
        app.SAVE = Path(self.tmp.name) / "state.json"
        for d in (app.lights, app.st, app.schedule, app.themes, app.found):
            d.clear()
        for sku in ("UNKNOWN", "H6008", "H619A", "H805D"):
            app.add_light(sku, sku, sku, "192.168.0.20")

    def tearDown(self):
        app.SAVE = self.old_save
        self.tmp.cleanup()

    def test_basic_never_streams_and_keeps_schedules(self):
        for sku in ("UNKNOWN", "H6008"):
            self.assertEqual(app.capability(sku), dict(bulbs=1, razer=False))
            with patch.object(app, "send") as send:
                app.push(sku, dict(on=True, brightness=45, effect="rainbow", colors=[[1, 2, 3], [4, 5, 6]]))
                app.razer(sku, app.ARM)
                app.reconnect(sku)
                app.flash(sku, step=0)
                app.schedule[sku]["timers"] = [app.clean_timer(dict(time="18:00", action="on", theme="Fireplace"))]
                now = dt.datetime.combine(dt.date.today(), dt.time(18))
                app.schedule_tick(now - dt.timedelta(seconds=1), now)
                cmds = [c.args[1] for c in send.call_args_list]
                self.assertNotIn("razer", cmds)
                self.assertTrue({"turn", "brightness", "colorwc"} <= set(cmds))
                self.assertIn(unittest.mock.call(sku, "colorwc", {"color": {"r": 1, "g": 2, "b": 3}, "colorTemInKelvin": 0}), send.call_args_list)
            history = len(app.st[sku]["history"])
            app.push(sku, dict(brightness=62, on=False))
            self.assertEqual(len(app.st[sku]["history"]), history)
            self.assertEqual(app.st[sku]["effect"], "solid")
            self.assertEqual(len(app.st[sku]["colors"]), 1)
            self.assertEqual(len(app.st[sku]["frame"]), 1)
        app.save(); app.load()
        self.assertTrue(all(app.st[k]["effect"] == "solid" for k in ("UNKNOWN", "H6008")))

    def test_custom_animation_colors_feed_effects(self):
        pat = [[160, 0, 255], [0, 80, 255], [255, 220, 0], [160, 0, 255]]
        app.push("H805D", dict(effect="custom", colors=[[255, 200, 80], [255, 255, 255]], animation=dict(frames=[dict(pattern=pat, duration=1)])))
        self.assertEqual(app.st["H805D"]["colors"], pat[:3])
        app.push("H805D", dict(effect="wave"))
        self.assertEqual(app.st["H805D"]["colors"], pat[:3])

    def test_commands_to_one_light_are_spaced_but_frames_are_not(self):
        clock, naps = [100.0], []
        def nap(seconds):
            naps.append(round(seconds, 3)); clock[0] += seconds
        app.sent_at.clear()
        with patch.object(app, "DRY", False), patch.object(app, "lan_gate"), patch.object(app, "sock") as sock, \
             patch.object(app.time, "monotonic", lambda: clock[0]), patch.object(app.time, "sleep", nap):
            app.send("H805D", "turn", {"value": 1})
            app.send("H805D", "brightness", {"value": 50})   # right behind the first: waits out the gap
            app.razer("H805D", app.ARM)                      # per-bulb mode on is a command too
            app.send("H619A", "turn", {"value": 1})          # another light has its own gap
            before = len(naps)
            app.razer("H805D", app.razer_frame([[1, 2, 3]] * 132))  # a stream frame never waits
            self.assertEqual(len(naps), before)
            clock[0] += 5
            app.send("H805D", "brightness", {"value": 60})   # long after the last command: no wait
        self.assertEqual(naps, [0, app.GAP, app.GAP, 0, 0])
        self.assertEqual(sock.sendto.call_count, 6)

    def test_user_bulb_count_is_saved_and_streamed(self):
        app.push("H805D", dict(effect="paint", bulbs=[[9, 9, 9]] * 132))
        app.save_lights([dict(id="H805D", name="Porch", bulbs=96)])
        self.assertEqual(app.capability("H805D"), dict(bulbs=96, razer=True))
        self.assertEqual(len(app.st["H805D"]["bulbs"]), 96)
        app.save_lights([dict(id="H6008", name="Lamp", bulbs=50)])  # basic lights ignore a count
        self.assertEqual(app.capability("H6008")["bulbs"], 1)
        for bad in (0, 256, 1.5, True, "96"):
            with self.assertRaises(ValueError):
                app.save_lights([dict(id="H805D", name="Porch", bulbs=bad)])
        app.save(); app.lights.clear(); app.load()
        self.assertEqual(app.capability("H805D")["bulbs"], 96)
        self.assertEqual(len(app.razer_frame(app.render(app.st["H805D"], 1, 96))), 3 + 3 + 3 * 96)

    def test_stream_lengths_and_h805d(self):
        self.assertEqual(app.capability("H805D"), dict(bulbs=132, razer=True))
        self.assertEqual(app.capability("H619A"), dict(bulbs=20, razer=True))
        for sku, n in (("H805D", 132), ("H619A", 20)):
            for effect in app.EFFECTS:
                settings = dict(app.DEFAULT, effect=effect)
                frame = app.render(settings, 1.7, n)
                self.assertEqual(len(frame), n)
                self.assertEqual(app.razer_frame(frame)[5], n)
        with patch.object(app, "send") as send, patch.object(app.time, "sleep", side_effect=InterruptedError):
            with self.assertRaises(InterruptedError):
                app.streamer()
            self.assertEqual({c.args[0] for c in send.call_args_list}, {"H805D", "H619A"})
            for call in send.call_args_list:
                raw = base64.b64decode(call.args[2]["pt"])
                n = app.capability(call.args[0])["bulbs"]
                self.assertEqual(raw[5], n)
                self.assertEqual(len(raw), 7 + 3 * n)

    def test_paint_animation_and_prompt_use_count(self):
        answer = dict(say="Done", actions=[dict(type="paint", target="H619A", indices=[19], color=[1, 2, 3])])
        app.apply_answer(answer, ["H619A"])
        self.assertEqual(len(app.st["H619A"]["bulbs"]), 20)
        self.assertEqual(app.st["H619A"]["bulbs"][19], [1, 2, 3])
        for sku, index in (("H619A", 20), ("H6008", 0)):
            answer["actions"][0].update(target=sku, indices=[index])
            with self.assertRaises(ValueError):
                app.apply_answer(answer, [sku])
        spec = dict(frames=[dict(pattern=[[255, 0, 0], [0, 0, 255]], duration=2, fade=0)], block=10, shift=10, motion="center")
        app.push("H619A", dict(effect="custom", animation=spec))
        f = app.render(app.st["H619A"], .5, 20)
        self.assertEqual(f, f[::-1])
        self.assertEqual(len(f), 20)
        with self.assertRaises(ValueError):
            app.push("H619A", dict(animation=dict(spec, block=21)))
        before = json.dumps(app.st, sort_keys=True)
        with self.assertRaises(ValueError):
            app.apply_answer(dict(say="Done", actions=[dict(type="set", target="all", settings=dict(animation=dict(spec, block=21)))]), ["H805D"])
        self.assertEqual(json.dumps(app.st, sort_keys=True), before)  # validate all counts before changing any light
        context = json.loads(app.ask_prompt(["H619A", "H6008"], "paint"))
        self.assertEqual(context["lights"]["H619A"]["capabilities"], dict(bulbs=20, razer=True))
        self.assertFalse(context["lights"]["H6008"]["capabilities"]["razer"])

    def test_older_84_slot_animation_and_shared_theme(self):
        spec = dict(frames=[dict(pattern=[[255, 0, 0]], duration=2, fade=0)], block=42, shift=42, motion="forward")
        app.st["H619A"].update(effect="custom", animation=spec)
        app.save(); app.load()
        self.assertEqual(app.st["H619A"]["effect"], "solid")
        self.assertEqual(app.st["H619A"]["history"][-1]["animation"], spec)
        app.reconnect("H619A")
        app.themes["Large"] = dict(effect="custom", animation=spec)
        for sku in ("H619A", "H6008"):
            app.schedule[sku]["timers"] = [app.clean_timer(dict(time="18:00", action="on", theme="Large"))]
        now = dt.datetime.combine(dt.date.today(), dt.time(18))
        app.schedule_tick(now - dt.timedelta(seconds=1), now)
        self.assertIn("schedule:", app.st["H619A"]["error"])
        self.assertEqual(app.st["H6008"]["effect"], "solid")

    def test_fake_scan_skus(self):
        with patch.object(app, "FAKE", 3), patch.object(app, "FAKE_SKUS", ["H805D", "H6008", "UNKNOWN"]):
            self.assertEqual([d["sku"] for d in app.discover().values()], ["H805D", "H6008", "UNKNOWN"])


if __name__ == "__main__":
    unittest.main()
