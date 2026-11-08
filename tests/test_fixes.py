"""Dry regressions for the second audit. No device, installer or model calls."""
import copy
import datetime as dt
import io
import json
import os
import random
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

os.environ["GOVEE_DRY"] = "1"
import server as app


class FixChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_save = app.SAVE
        app.SAVE = Path(self.tmp.name) / "state.json"
        for d in (app.lights, app.st, app.schedule, app.themes, app.found, app.outside, app.observed, app.command_at):
            d.clear()
        app.paused.clear(); app.fired.clear()
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

    def answer(self, actions):
        return app.apply_answer(dict(say="Done", actions=actions), ["a"])

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
        data["_schedule"]["b"]["timers"] = [self.timer("18:00"), dict(self.timer("19:00"), time="sunset")]
        data["_location"] = ["bad", 0]
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
        self.assertFalse(app.st["a"]["on"])
        self.assertEqual(app.st["b"]["colors"], [[1, 2, 3]])
        self.assertEqual(len(app.schedule["b"]["timers"]), 2)
        self.assertEqual(set(app.themes), {"Good"})
        self.assertEqual(len(app.outside["b"]), 1)
        for state in app.st.values():
            app.render(state, 0)

    def test_timer_crossing_midnight(self):
        app.schedule["a"]["timers"] = [self.timer("23:59", "off")]
        with patch.object(app, "push") as push:
            app.schedule_tick(dt.datetime(2026, 11, 6, 23, 58, 50), dt.datetime(2026, 11, 7, 0, 0, 10))
        push.assert_called_once_with("a", dict(on=False))
        app.fired.clear()
        app.schedule["a"]["timers"][0]["time"] = "sunset+120"
        with patch.object(app, "when", side_effect=lambda spec, day: dt.datetime.combine(day, dt.time()) + dt.timedelta(hours=25)), patch.object(app, "push") as push:
            app.schedule_tick(dt.datetime(2026, 11, 7, 0, 59), dt.datetime(2026, 11, 7, 1, 1))
        push.assert_called_once_with("a", dict(on=False))

    def test_timer_crossing_order_and_multiple_dates(self):
        app.schedule["a"]["timers"] = [self.timer("18:01", "off"), self.timer("18:00")]
        with patch.object(app, "push") as push:
            app.schedule_tick(dt.datetime(2026, 11, 6, 17, 59), dt.datetime(2026, 11, 7, 18, 2))
        self.assertEqual([c.args[1]["on"] for c in push.call_args_list], [True, False, True, False])

    def test_timer_fall_back_occurrence_runs_once(self):
        app.schedule["a"]["timers"] = [self.timer("01:30")]
        with patch.object(app, "push") as push:
            for start, end in (("01:29:50", "01:30:10"), ("01:59:50", "01:00:10"), ("01:29:50", "01:30:10")):
                app.schedule_tick(dt.datetime.fromisoformat("2026-11-01T" + start), dt.datetime.fromisoformat("2026-11-01T" + end))
            self.assertEqual(push.call_count, 1)
            app.schedule_tick(dt.datetime(2026, 11, 2, 1, 29), dt.datetime(2026, 11, 2, 1, 31))
            self.assertEqual(push.call_count, 2)

    def test_schedule_switch_mutates_only_inside_lock(self):
        attempted = threading.Event()
        underlying = threading.RLock()
        class Gate:
            def __enter__(self):
                if threading.current_thread().name == "switch":
                    attempted.set()
                return underlying.__enter__()
            def __exit__(self, *args):
                return underlying.__exit__(*args)
        result = []
        with patch.object(app, "lock", Gate()):
            with app.lock:
                worker = threading.Thread(name="switch", target=lambda: result.append(self.post("/api/schedule/use", dict(light="a", use=False))))
                worker.start()
                self.assertTrue(attempted.wait(2))
                self.assertTrue(app.schedule["a"]["use"])
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0][0], 200)
        self.assertFalse(app.schedule["a"]["use"])

    def test_ask_schedule_commit_excludes_concurrent_edit_and_untouched_lights(self):
        validating, proceed, finished = threading.Event(), threading.Event(), threading.Event()
        original = app.change_timers
        results = []
        def hold(*args, **kwargs):
            if threading.current_thread().name == "ask":
                validating.set()
                if not proceed.wait(2):
                    raise RuntimeError("Test did not release validation")
            return original(*args, **kwargs)
        def direct():
            results.append(self.post("/api/schedule", dict(light="a", add=[dict(time="19:00", action="off")])))
            finished.set()
        with patch.object(app, "change_timers", side_effect=hold):
            ask = threading.Thread(name="ask", target=lambda: results.append(self.answer([dict(type="schedule", schedule=dict(add=[dict(time="18:00", action="on")]))])))
            ask.start()
            self.assertTrue(validating.wait(2))
            edit = threading.Thread(target=direct); edit.start()
            self.assertFalse(finished.wait(.05))  # validation and commit own the state lock
            proceed.set()
            ask.join(2); edit.join(2)
        self.assertFalse(ask.is_alive() or edit.is_alive())
        self.assertEqual({t["time"] for t in app.schedule["a"]["timers"]}, {"18:00", "19:00"})
        untouched = app.schedule["b"]
        self.answer([dict(type="set", settings=dict(brightness=30))])
        self.assertIs(app.schedule["b"], untouched)

    def test_power_mismatch_reconciles_after_command_grace(self):
        app.observed["a"] = (True, 100)
        app.command_at["a"] = 101
        for stamp in (105, 110, 120):
            app.observe_power("a", False, dt.datetime(2026, 11, 7, 12), stamp)
            self.assertTrue(app.st["a"]["on"])
        app.observe_power("a", False, dt.datetime(2026, 11, 7, 12), 125)
        self.assertFalse(app.st["a"]["on"])
        self.assertIn("a", app.paused)
        self.assertFalse(app.outside.get("a"))  # ambiguous timing is not a warning event

    def test_settings_reject_bad_bulbs_and_non_boolean_power(self):
        before = copy.deepcopy(app.st)
        for settings in (dict(effect="paint", bulbs=[[1]]), dict(bulbs=[[1, 2, 3, 4]]), dict(bulbs=[[True, 0, 0]]), dict(on="false"), dict(on=0)):
            self.assertEqual(self.post("/api/set", dict(light="all", settings=settings))[0], 400)
            self.assertEqual(app.st, before)
        app.push("a", dict(effect="paint", bulbs=[[1, 2, 3]], on=False))
        self.assertEqual(len(app.st["a"]["frame"]), 132)
        self.assertTrue(all(len(c) == 3 for c in app.st["a"]["frame"]))

    def test_all_undo_preflights_and_failed_send_keeps_history(self):
        app.push("a", dict(effect="solid"))
        before = copy.deepcopy(app.st)
        self.assertEqual(self.post("/api/undo", dict(light="all"))[0], 400)
        self.assertEqual(app.st, before)
        app.lights["b"]["sku"] = "H619A"
        app.st["b"]["history"] = [dict(app.DEFAULT, effect="custom", animation=dict(frames=[dict(pattern=[[1, 2, 3]], duration=1)], block=42))]
        incompatible = copy.deepcopy(app.st)
        self.assertEqual(self.post("/api/undo", dict(light="all"))[0], 400)
        self.assertEqual(app.st, incompatible)
        with self.assertRaises(ValueError):
            self.answer([dict(type="undo", target="all")])
        self.assertEqual(app.st, incompatible)
        with patch.object(app, "send", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                app.undo("a")
        self.assertEqual(app.st["a"]["history"], before["a"]["history"])
        app.undo("a")
        self.assertEqual(app.st["a"]["effect"], app.DEFAULT["effect"])
        self.assertEqual(app.st["a"]["history"], [])
        self.assertEqual(json.loads(app.SAVE.read_text())["a"]["history"], [])

    def test_all_theme_save_requires_one_look(self):
        app.push("a", dict(colors=[[255, 0, 0]]))
        self.assertEqual(self.post("/api/theme", dict(light="all", name="Mixed"))[0], 400)
        self.assertNotIn("Mixed", app.themes)
        app.push("b", dict(colors=[[255, 0, 0]]))
        self.assertEqual(self.post("/api/theme", dict(light="all", name="Same"))[0], 200)
        self.assertEqual(app.themes["Same"]["colors"], [[255, 0, 0]])

    def test_ask_changed_means_new_undoable_look(self):
        for settings in (dict(brightness=30), dict(on=False), dict(effect=app.DEFAULT["effect"])):
            self.assertFalse(self.answer([dict(type="set", settings=settings)])[1])
        self.assertEqual(app.st["a"]["history"], [])
        self.assertTrue(self.answer([dict(type="set", settings=dict(effect="solid"))])[1])
        self.assertFalse(self.answer([dict(type="undo")])[1])
        self.assertFalse(self.answer([])[1])
        self.assertFalse(self.answer([dict(type="set", settings=dict(effect="solid")), dict(type="undo")])[1])
        self.assertEqual(app.st["a"]["history"], [])


    def test_undo_preserves_current_power_and_brightness(self):
        app.push("a", dict(effect="solid"))
        app.push("a", dict(on=False, brightness=23))
        app.undo("a")
        self.assertFalse(app.st["a"]["on"])
        self.assertEqual(app.st["a"]["brightness"], 23)
        self.assertEqual(app.st["a"]["effect"], app.DEFAULT["effect"])
        # Old saved entries and a later theme save in the same answer use current power.
        app.push("a", dict(effect="solid"))
        app.st["a"]["history"][-1].update(on=True, brightness=100)
        self.answer([dict(type="undo"), dict(type="save_theme", name="After undo")])
        self.assertFalse(app.st["a"]["on"])
        self.assertEqual(app.themes["After undo"]["brightness"], 23)

    def test_undo_does_not_wake_an_off_basic_light_with_a_color_command(self):
        app.add_light("c", "Basic", "H6008")
        app.push("c", dict(colors=[[255, 0, 0]]))
        app.push("c", dict(on=False, brightness=23))
        with patch.object(app, "send") as send:
            app.undo("c")
        send.assert_not_called()
        self.assertFalse(app.st["c"]["on"])
        self.assertEqual(app.st["c"]["brightness"], 23)
        self.assertEqual(app.st["c"]["colors"], [app.DEFAULT["colors"][0]])

    def test_colors_require_exactly_three_integer_channels(self):
        before = copy.deepcopy(app.st)
        for colors in ([[255, 0, 0, 9]], [[255, 0]], [[True, 0, 0]], [["255", 0, 0]], [[255, 0, None]], "red"):
            with self.subTest(colors=colors):
                self.assertEqual(self.post("/api/set", dict(light="a", settings=dict(colors=colors)))[0], 400)
                self.assertEqual(app.st, before)
        self.assertEqual(app.clean(dict(colors=[[255, 0, 0]]))["colors"], [[255, 0, 0]])

    def test_theme_snapshot_and_prompt_use_the_state_lock(self):
        case = self
        class Gate:
            held = False
            def __enter__(self): self.held = True
            def __exit__(self, *args): self.held = False
        gate = Gate()
        class Guarded(dict):
            def items(self):
                case.assertTrue(gate.held)
                return super().items()
            def __setitem__(self, key, value):
                case.assertTrue(gate.held)
                super().__setitem__(key, value)
        saved = Guarded(One=dict(effect="solid"))
        def preview(p):
            self.assertFalse(gate.held)
            dict.__setitem__(saved, "Two", dict(effect="wave"))
            return []
        with patch.object(app, "themes", saved), patch.object(app, "st", Guarded(app.st)), patch.object(app, "lock", gate):
            with patch.object(app, "clip", side_effect=preview):
                self.assertEqual([row[0] for row in app.theme_list()], ["One"])
            context = json.loads(app.ask_prompt(["a"], "dim"))
            self.assertEqual(set(context["themes"]), {"One", "Two"})
            with patch.object(app, "save"):
                app.save_theme("Three", dict(effect="solid"))

    def test_batch_saves_once_including_undo_and_theme(self):
        with patch.object(app, "save", wraps=app.save) as save:
            self.answer([dict(type="set", target="all", settings=dict(effect="solid")),
                         dict(type="save_theme", target="all", name="Solid"),
                         dict(type="undo", target="all")])
            save.assert_called_once()
        saved = json.loads(app.SAVE.read_text())
        self.assertEqual(saved["a"]["history"], [])
        self.assertIn("Solid", saved["_themes"])
        with patch.object(app, "save") as save:
            app.push("a", dict(brightness=50))
            save.assert_called_once()

    def test_batch_saves_successful_sends_if_a_later_send_fails(self):
        original = app.send
        def fail_second(key, *args):
            if key == "b":
                raise OSError("second light failed")
            return original(key, *args)
        with patch.object(app, "send", side_effect=fail_second), patch.object(app, "save", wraps=app.save) as save:
            with self.assertRaises(OSError):
                self.answer([dict(type="set", target="all", settings=dict(on=False))])
            save.assert_called_once()
        saved = json.loads(app.SAVE.read_text())
        self.assertFalse(saved["a"]["look"]["on"])
        self.assertTrue(saved["b"]["look"]["on"])

    def test_reconnect_only_pushes_to_lights_seen_in_scan(self):
        app.st["b"]["error"] = "not found"
        with patch.object(app, "discover", return_value={"a": app.lights["a"]}), patch.object(app, "reconnect", wraps=app.reconnect) as reconnect:
            code, body = self.post("/api/reconnect", dict(light="all"))
        self.assertEqual(code, 400)
        self.assertIn("Second", body["error"])
        reconnect.assert_called_once_with("a")
        self.assertEqual(app.st["b"]["error"], "not found")

    def test_discovery_closes_receiver_if_bind_fails(self):
        receiver = MagicMock()
        receiver.bind.side_effect = OSError("address in use")
        with patch.object(app, "DRY", False), patch.object(app, "lan_gate"), patch.object(app.socket, "socket", return_value=receiver):
            with self.assertRaisesRegex(OSError, "address in use"):
                app.discover()
        receiver.close.assert_called_once()

    def test_repeated_changes_matches_original_on_random_events(self):
        def original(events):
            """One warning per power/time cluster, across distinct local dates (within three minutes)."""
            warnings = []
            samples = [(dt.datetime.fromisoformat(e["at"]), e["on"]) for e in events]
            samples = [(at.date(), at.hour * 60 + at.minute, power) for at, power in samples]
            distance = lambda a, b: min(abs(a - b), 1440 - abs(a - b))
            for day, minute, power in samples:
                days = {date for date, other, on in samples if on == power and distance(other, minute) <= 3}
                if len(days) >= 2 and not any(w["on"] == power and distance(w["minute"], minute) <= 6 for w in warnings):
                    warnings.append({"on": power, "minute": minute, "days": len(days)})
            return warnings
        rng = random.Random(20261108)
        start = dt.datetime(2026, 11, 1)
        for run in range(100):
            events = []
            for _ in range(rng.randrange(200)):
                minute = rng.choice([0, 1, 2, 3, 4, 6, 7, 1437, 1439, rng.randrange(1440)])
                at = start + dt.timedelta(days=rng.randrange(8), minutes=minute, seconds=rng.randrange(60))
                events.append(dict(at=at.isoformat(), on=bool(rng.randrange(2))))
            with self.subTest(run=run):
                self.assertEqual(app.repeated_changes(events), original(events))


class HostChecks(unittest.TestCase):
    def test_only_local_host_names_are_answered(self):  # DNS rebinding guard
        def ok(host, ip="127.0.0.1"):
            h = app.H.__new__(app.H)
            h.client_address, h.headers = (ip, 0), {"Host": host}
            return h.allowed()
        for host in ("localhost:8765", "127.0.0.1:8765", "mac.local:8765", "192.168.1.5:8765", "[::1]:8765", "mypc", ""):
            self.assertTrue(ok(host), host)
        for host in ("evil.com:8765", "8.8.8.8", "localhost.evil.com"):
            self.assertFalse(ok(host), host)
        self.assertFalse(ok("localhost", ip="8.8.8.8"))
