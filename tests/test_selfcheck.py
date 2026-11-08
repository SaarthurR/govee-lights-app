"""Former startup self-checks, isolated from saved state and real hardware."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ["GOVEE_DRY"] = "1"
import server as app


class SelfChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        state = patch.multiple(app, SAVE=Path(temp.name) / "state.json", DRY=True,
                               lights={}, st={}, schedule={}, themes={}, found={}, outside={},
                               observed={}, command_at={}, paused=set(), fired=set(), flashing=set(),
                               location=None, chat={"at": 0.0, "turns": []}, govee_key="", ask_on=True, send=app.send)
        state.start()
        self.addCleanup(state.stop)

    def test_saved_sample_self_checks(self):
        f = app.razer_frame([[1, 2, 3]])
        assert f == [0xBB, 0, 5, 0xB0, 0, 1, 1, 2, 3], f
        assert app.razer_frame([[0, 0, 0]] * 132)[:6] == [0xBB, 0x01, 0x8E, 0xB0, 0, 132]  # bytes the H805D showed one per bulb (Nov 7)
        for e in app.EFFECTS:
            for t in (0.0, 1.7, 123.4):
                fr = app.render(dict(app.DEFAULT, effect=e, colors=[app.GOLD, app.SILVER, [9, 9, 9]]), t)
                assert len(fr) == app.N and all(len(c) == 3 and all(-0.001 <= v <= 255.001 for v in c) for c in fr), e
        assert app.clean({"brightness": 500, "speed": -1, "junk": 1}) == {"brightness": 100, "speed": 0.05}
        utc = lambda d: d.astimezone(app.dt.timezone.utc)  # naive local -> UTC, so the check passes in any time zone
        london = [51.5, -0.13]
        ss, ws = utc(app.sunset(app.dt.date(2026, 6, 21), london)), utc(app.sunset(app.dt.date(2026, 12, 21), london))
        assert (ss.hour, ws.hour) == (20, 15), (ss, ws)  # London: ~9:21 PM BST in June, ~3:53 PM in December
        assert app.sunset(app.dt.date(2026, 6, 21), [89.0, 0.0])  # midnight sun: clamped, no crash
        assert app.sunset(app.dt.date.today()) is None and app.when("sunset", app.dt.date.today()) is None  # no location yet
        try:
            app.clean_time("sunset"); assert False, "sunset needs a location"
        except ValueError:
            pass
        app.location = london
        assert app.clean_time("Sunset + 30") == "sunset+30" and app.clean_time("sunset-0") == "sunset" and app.clean_time("7:05") == "07:05"
        for bad in ("sunset+121", "sunset-x", "25:00", "", "noon"):
            try:
                app.clean_time(bad); assert False, bad
            except ValueError:
                pass
        sat_sun = [{"id": "on", "time": "sunset-15", "action": "on", "days": [5, 6], "enabled": True},
                   {"id": "off", "time": "00:00", "action": "off", "days": [5, 6], "enabled": True}]
        assert app.migrate({"on": "sunset-15", "off": "00:00", "days": [5, 6]}) == {"use": True, "timers": sat_sun}
        assert app.migrate({"use": False, "timers": sat_sun + [{"time": "junk", "action": "on"}]}) == {"use": False, "timers": sat_sun}
        assert app.clean_location("37.123456", -122.98765) == [37.12, -122.99]
        for bad in ((91, 0), (0, 181), ("nan", 0), ("x", 0)):
            try:
                app.clean_location(*bad); assert False, bad
            except ValueError:
                pass
        app.location = None
        assert app.clean_days(None) == list(range(7)) and app.clean_days([4, 0, 0]) == [0, 4]
        for bad in ([], [7], [-1], ["1"], [True], "0,1", 3):
            try:
                app.clean_days(bad); assert False, bad
            except (ValueError, TypeError):
                pass
        fri = app.dt.datetime(2026, 11, 6)
        assert fri.weekday() == 4
        at = lambda d, hm: fri + app.dt.timedelta(days=d, hours=int(hm[:2]), minutes=int(hm[3:]))
        on, off, late = ({"id": a + h, "time": h, "action": a, "days": [0, 1, 2, 3, 4], "enabled": True}
                         for h, a in (("18:00", "on"), ("00:00", "off"), ("23:00", "off")))
        wk = [on, off]
        assert app.due(wk, on, at(0, "18:00")) and not app.due(wk, on, at(1, "18:00"))  # Friday yes, Saturday no
        assert app.due(wk, off, at(1, "00:00"))  # Saturday 12 AM ends Friday's evening, so it still turns off
        assert not app.due(wk, off, at(3, "00:00")) and app.due(wk, off, at(4, "00:00"))  # Mon 12 AM ends Sunday (no); Tue ends Mon
        assert app.due(wk + [late], late, at(0, "23:00")) and not app.due(wk + [late], late, at(1, "23:00"))  # after the on: its own day
        assert not app.due([off], off, at(1, "00:00"))  # no on timer at all: the off is just a time on its days
        assert app.due([dict(on, enabled=False), off], off, at(1, "00:00"))  # pausing the on timer doesn't move the off's nights
        assert app.next_run(wk, at(0, "12:00")) == (at(0, "18:00"), on)
        assert app.next_run(wk, at(0, "18:00")) == (at(1, "00:00"), off)  # tonight's off, Saturday 12 AM
        assert app.next_run(wk, at(1, "00:00")) == (at(3, "18:00"), on)  # weekend skipped: Monday 6 PM
        assert app.next_run([dict(on, enabled=False)], fri) is None
        for n, p in app.BUILTIN.items():
            assert len(app.render(dict(app.DEFAULT, **p), 5.0)) == app.N, n
        walk = dict(app.DEFAULT, effect="custom", speed=0.85, animation={"frames": [{"pattern": [[255, 0, 0], app.GOLD, app.SILVER], "duration": 0.2, "fade": 0}], "shift": 1, "motion": "forward", "block": 1})
        steps = [app.custom_frame(walk["animation"], k / app.FPS + (k % 3) * 0.01, 3, 0.85)[0] for k in range(30)]  # late sends jitter
        runs = [len(list(g)) for _, g in __import__("itertools").groupby(steps)][1:-1]
        assert len(set(runs)) == 1, runs  # every step of an animation lasts equally long
        app.chat["turns"] = [["hi", "hello", False]]  # chat["at"] is 0, so it counts as long expired
        assert app.chat_view()["turns"] == []
        # discovery parsing and setup, against a throwaway state file so the real one is never touched
        ok = app.json.dumps({"msg": {"cmd": "scan", "data": {"ip": "192.168.1.9", "device": "12:34:56:78:9A:BC:DE:F0", "sku": "H805D"}}})
        assert app.parse_scan(ok) == ("12:34:56:78:9A:BC:DE:F0", "192.168.1.9", "H805D") and app.short("12:34:56:78:9A:BC:DE:F0") == "DEF0"
        for bad in ("junk", "[]", '{"msg": {"data": {"ip": "8.8.8.8", "device": "x"}}}', '{"msg": {"data": {"device": "x"}}}'):
            assert app.parse_scan(bad) is None, bad
        app.found = {"12:34:56:78:9A:BC:DE:F0": {"ip": "192.168.1.9", "sku": "H805D"}}
        for bad in ([], [{"id": "nope", "name": "X"}], [{"id": "12:34:56:78:9A:BC:DE:F0", "name": "  "}], "x"):
            try:
                app.save_lights(bad); assert False, bad
            except ValueError:
                pass
        app.save_lights([{"id": "12:34:56:78:9A:BC:DE:F0", "name": "  Porch   rail "}])
        assert app.lights["12:34:56:78:9A:BC:DE:F0"] == {"name": "Porch rail", "sku": "H805D", "ip": "192.168.1.9"} and app.st and app.schedule
        try:
            app.flash("nope"); assert False, "flash must refuse a light the scan did not find"
        except ValueError:
            pass
        app.flashing.add("12:34:56:78:9A:BC:DE:F0")
        assert app.flash("12:34:56:78:9A:BC:DE:F0") is False  # a second tap while it blinks does nothing
        app.flashing.clear()
        with patch.object(app.time, "sleep"):
            assert app.flash("12:34:56:78:9A:BC:DE:F0", 0.1) is True
            assert not app.flashing and app.flash("12:34:56:78:9A:BC:DE:F0", 0) is True
        data = app.json.loads(app.SAVE.read_text())
        P = "12:34:56:78:9A:BC:DE:F0"
        data["_schedule"] = {P: {"on": "18:00", "off": None}}  # saved before days, the switch and timers existed
        app.SAVE.write_text(app.json.dumps(data))
        app.lights.clear(); app.st.clear(); app.schedule.clear(); app.load()
        assert app.lights[P]["name"] == "Porch rail" and app.st[P]["on"]
        assert app.schedule[P] == {"use": True, "timers": [{"id": "on", "time": "18:00", "action": "on", "days": list(range(7)), "enabled": True}]}
        s = {P: dict(app.schedule[P])}
        app.change_timers(s, [P], add=[{"time": "23:00", "action": "off", "days": [4], "theme": "Fireplace"}], edit=[{"id": "on", "enabled": False}])
        assert [t["enabled"] for t in s[P]["timers"]] == [False, True] and "theme" not in s[P]["timers"][1]  # an off timer has no look
        assert app.schedule[P]["timers"][0]["enabled"]  # only the copy changed
        app.change_timers(s, [P], delete=["on"])
        assert [t["time"] for t in s[P]["timers"]] == ["23:00"]
        for bad in (dict(add=[{"time": "18:00", "action": "on"}] * 10), dict(edit=[{"id": "nope"}]), dict(delete=["nope"]), dict(add="x"),
                    dict(add=[{"time": "18:00", "action": "on", "theme": "No such theme"}]), dict(add=[{"time": "18:00", "action": "dim"}]),
                    dict(add=[{"time": "18:00", "action": "on", "enabled": "yes"}]), dict(add=[{"time": "18:00", "action": "on", "code": 1}])):
            try:
                app.change_timers({P: dict(s[P])}, [P], **bad); assert False, bad
            except ValueError:
                pass
        assert len(s[P]["timers"]) == 1
        sent, real_send = [], app.send
        app.send = lambda k, cmd, data: sent.append((k, cmd, data))
        app.st[P]["on"] = False
        six = app.dt.datetime.combine(app.dt.date.today(), app.dt.time(18))
        app.schedule[P]["use"] = False
        app.schedule_tick(six - app.dt.timedelta(minutes=1), six)
        assert sent == [] and not app.st[P]["on"]  # switched off: nothing sent at the on time
        app.schedule[P]["use"] = True
        app.schedule_tick(six - app.dt.timedelta(minutes=1), six)
        assert (P, "turn", {"value": 1}) in sent and app.st[P]["on"]
        app.schedule[P]["timers"] = [app.clean_timer({"time": "18:00", "action": "on", "theme": "Fireplace", "enabled": False})]
        sent.clear(); app.schedule_tick(six - app.dt.timedelta(minutes=1), six)
        assert sent == []  # a disabled timer does nothing
        app.schedule[P]["timers"][0]["enabled"] = True
        app.schedule_tick(six - app.dt.timedelta(minutes=1), six)
        assert app.st[P]["effect"] == "fire" and app.st[P]["on"]  # an on timer with a theme shows that look
        app.send = real_send
        app.schedule[P]["use"] = False
        app.save(); app.schedule.clear(); app.lights.clear(); app.st.clear(); app.load()
        assert app.schedule[P]["use"] is False and app.schedule[P]["timers"][0]["theme"] == "Fireplace"  # off survives a restart, timers kept
        app.forget_light("12:34:56:78:9A:BC:DE:F0")
        assert not app.lights and not app.st and not app.schedule
        # Public-schema samples only: these checks never call a network transport.
        sample = app.json.loads((app.HERE / "samples/govee.json").read_text())
        devices = app.parse_devices(sample["devices"])
        assert devices[0]["deviceName"] == "Reading lights" and len(devices) == 2
        catalog = app.parse_scenes(devices[0]["capabilities"])
        assert catalog[1]["instance"] == "snapshot" and catalog[1]["value"] == 0
        assert app.parse_scenes(sample["scenes"]["payload"]["capabilities"])[0]["value"] == {"id": 3867, "paramId": 4294}
        assert app.parse_scenes(sample["diy-scenes"]["payload"]["capabilities"])[0]["instance"] == "diyScene"
        assert app.parse_scenes([]) == []
        for bad in ({"code": 401}, {"code": 403}, {"code": 429}, {"code": 500}, [], {"code": 200, "data": {}}):
            try:
                app.parse_devices(bad); assert False, bad
            except ValueError as e:
                assert "Govee" in str(e)
        for bad in (None, {}, [{"instance": "snapshot", "parameters": {"options": "bad"}}]):
            try:
                app.parse_scenes(bad); assert False, bad
            except ValueError:
                pass
        try:
            app.govee_read("sample-not-a-key", "device/control"); assert False, "mutation must be rejected before transport"
        except ValueError:
            pass
        assert app.parse_status(app.json.dumps(sample["status"])) is True
        for bad in ("junk", "[]", '{"msg":{"cmd":"scan","data":{"onOff":1}}}', '{"msg":{"cmd":"devStatus","data":{"onOff":"1"}}}'):
            assert app.parse_status(bad) is None
        events = sample["outside"]
        assert app.repeated_changes(events) == [{"on": True, "minute": 1080, "days": 2}]
        assert not app.repeated_changes(events[:1] * 2)  # one date never counts twice
        assert not app.repeated_changes([events[0], dict(events[1], on=False)])
        assert not app.repeated_changes([events[0], dict(events[1], at="2026-11-06T18:04:00")])
        assert len(app.repeated_changes(events + [dict(e, on=False) for e in events])) == 2
        assert app.repeated_changes([dict(events[0], at="2026-11-05T23:59:00"), dict(events[1], at="2026-11-06T00:01:00")])
        dev = devices[0]["device"]
        app.add_light(dev, "Sample light", "H805D", "192.168.0.20")
        start = app.dt.datetime(2026, 11, 5, 17, 59, 55)
        app.observe_power(dev, False, start, 100)
        assert not app.outside.get(dev) and dev in app.paused  # baseline respects power, but invents no event
        app.observe_power(dev, True, start + app.dt.timedelta(seconds=5), 105)
        assert len(app.outside[dev]) == 1 and app.st[dev]["on"] and dev in app.paused
        app.command_at[dev] = 106
        app.observe_power(dev, False, start + app.dt.timedelta(seconds=10), 110)
        assert len(app.outside[dev]) == 1  # app commands do not count
        app.observe_power(dev, True, start + app.dt.timedelta(minutes=2), 225)
        assert len(app.outside[dev]) == 1  # long observation gap has no exact time
        app.govee_key = "sample-not-a-key"
        app.save()
        assert app.os.name == "nt" or app.SAVE.stat().st_mode & 0o777 == 0o600  # Windows has no POSIX modes; the profile folder is per-user
        app.outside.clear(); app.paused.clear(); app.govee_key = ""; app.load()
        assert app.govee_key == "sample-not-a-key" and len(app.outside[dev]) == 1 and dev in app.paused
        app.ask_on = False; app.save(); app.ask_on = True; app.load()
        assert app.ask_on is False  # "Not now" survives a restart
        data = app.json.loads(app.SAVE.read_text()); del data["_ask"]; app.SAVE.write_text(app.json.dumps(data)); app.load()
        assert app.ask_on is True  # a save from before the choice keeps the chat
        app.reconnect(dev)  # explicit user action, still DRY inside these assertions
        assert dev not in app.paused and dev not in app.json.loads(app.SAVE.read_text())["_paused"]
        app.forget_light(dev)
        assert dev not in app.outside and dev not in app.paused
        app.govee_key = ""
