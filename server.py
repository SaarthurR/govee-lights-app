# Govee light strip control app over Wi-Fi (LAN API, per-bulb "razer" stream). Tested on H805D.
# Run: python3 server.py  → http://localhost:8765 (phone: http://<this computer's IP>:8765)
# Needs "LAN Control" on in the Govee Home app for each light.
import base64, datetime as dt, errno, ipaddress, json, math, os, random, shutil, socket, subprocess, sys, tempfile, threading, time
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from socketserver import TCPServer
from pathlib import Path
from urllib import request, error
from launcher import update

APP_VERSION = update.settings()['version']

HERE = Path(__file__).parent
SAVE = Path(os.environ.get("GOVEE_STATE", HERE / "state.json"))   # lights, last look + history per light, themes, schedule; survives restarts
N = 84    # most colors one razer packet holds (length byte = 3*N+2 <= 255)
# Streaming slots, not physical LED counts: docs/protocol.md (H805D), OpenRGB's Govee table (H619A).
MODELS = {"H805D": {"bulbs": 84, "razer": True}, "H619A": {"bulbs": 20, "razer": True},
          "H6008": {"bulbs": 1, "razer": False}}  # false = no verified streaming support in this app
BASIC = MODELS["H6008"]
FPS = 12
HISTORY = 30
EFFECTS = ("wave", "smoothwave", "shimmer", "twinkle", "comet", "fire", "aurora",
           "solid", "pattern", "chase", "march", "breathe", "rainbow", "paint", "custom")
KEYS = ("effect", "colors", "speed", "amount", "brightness", "on", "bulbs", "animation")
DEFAULT = {"effect": "wave", "colors": [[255, 200, 80], [255, 255, 255]], "speed": 1.0, "amount": 0.3,
           "brightness": 100, "on": True, "bulbs": None, "animation": None}
GOLD, SILVER = [255, 200, 80], [255, 255, 255]
PRESETS = [
    ("Silver & gold wave", {"effect": "wave", "colors": [GOLD, SILVER], "speed": 1, "amount": 0.3}),
    ("Silver & gold smooth", {"effect": "smoothwave", "colors": [GOLD, SILVER], "speed": 1, "amount": 0.3}),
    ("Silver & gold shimmer", {"effect": "shimmer", "colors": [GOLD, SILVER], "speed": 1, "amount": 0.5}),
    ("Starlight", {"effect": "twinkle", "colors": [[40, 30, 10], SILVER], "speed": 1, "amount": 0.4}),
    ("Gold comet", {"effect": "comet", "colors": [[30, 20, 5], GOLD], "speed": 1, "amount": 0.5}),
    ("Fireplace", {"effect": "fire", "colors": [[255, 60, 0], [255, 150, 20]], "speed": 1, "amount": 0.6}),
    ("Aurora", {"effect": "aurora", "colors": [[0, 255, 140], [40, 80, 255], [160, 40, 255]], "speed": 0.6, "amount": 0.5}),
    ("Warm white", {"effect": "solid", "colors": [[255, 190, 110]]}),
    ("Candy cane", {"effect": "chase", "colors": [[255, 20, 20], SILVER], "speed": 0.6, "amount": 0.3}),
    ("Ocean", {"effect": "smoothwave", "colors": [[20, 80, 255], [0, 200, 200]], "speed": 0.6, "amount": 0.5}),
    ("Rainbow", {"effect": "rainbow", "colors": [SILVER], "speed": 1, "amount": 0.5}),
    ("Rainbow march", {"effect": "march", "colors": [[255, 0, 0], [255, 120, 0], [255, 220, 0], [0, 200, 0], [0, 80, 255], [75, 0, 200], [160, 0, 255]], "speed": 1, "amount": 0.5}),
]
BUILTIN = dict(PRESETS)
MAX_BODY, MAX_THEMES, MAX_NAME = 65536, 60, 30

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
lock = threading.RLock()
scan_lock = threading.Lock()  # one LAN scan at a time: two sockets on port 4002 would split the replies
lights = {}  # saved lights, in picker order: Govee device id -> {"name", "sku", "ip"}
found = {}   # every Govee light that answered the last scan: device id -> {"sku", "ip"}
st = {}
seeds = [random.Random(i).random() * 6.283 for i in range(N)]
rates = [0.6 + random.Random(i + 99).random() * 0.8 for i in range(N)]
themes = {}  # the user's own saved looks: name -> settings
schedule = {}  # per light: {"use": False = Govee timers instead, "timers": [{"id", "time": "HH:MM" / "sunset" / "sunset-15", "action": "on"/"off", "days": [0=Mon .. 6=Sun], "enabled", "theme"?}]}
MAX_TIMERS = 10
location = None  # [lat, lon] for sunset, set from the browser or typed in
ask_lock = threading.Lock()
PORT = int(os.environ.get("PORT", 8765))
DRY = os.environ.get("GOVEE_DRY") == "1"  # test mode: nothing is sent to the lights
FAKE = int(os.environ.get("GOVEE_FAKE_LIGHTS") or 0) if DRY else 0  # test mode: the scan "finds" this many lights
FAKE_SKUS = os.environ.get("GOVEE_FAKE_SKUS", "H805D").split(",")
# macOS Local Network privacy turns every LAN send into "No route to host" until the user allows the app.
LAN_BLOCKED = ("Allow Govee Lights in System Settings > Privacy & Security > Local Network. "
               "Not listed there? Run xcode-select --install in Terminal, then run the Lights installer again.")
# macOS asks once, on the first LAN packet. Nothing goes out until the page has explained why and the user tapped Continue.
lan_asked = False
lan_seen = (-9.0, "unknown")  # (monotonic time, last probe result)
FAKE_NET = os.environ.get("GOVEE_FAKE_NET", "allowed")  # test mode: what the Local Network check reports after Continue
APP_BIN = Path.home() / "Applications/Govee Lights.app/Contents/MacOS/Govee Lights"
govee_key = ""
govee_devices = []
govee_cache = {}
govee_lock = threading.Lock()
govee_read_at = 0.0
observed = {}  # last successful LAN sample, never persisted as a fresh baseline
command_at = {}
outside = {}  # bounded, local power-change history
paused = set()  # outside changes suspend streaming until a user action or their schedule
flashing = set()  # lights blinking right now for "which one is this?"
SAMPLE = DRY and os.environ.get("GOVEE_SAMPLE") == "1"
CLAUDE = shutil.which("claude") is not None
ask_on = True  # the user's Ask Claude choice; saves from before the choice count as on


def short(dev):
    return dev.replace(":", "")[-4:].upper()  # the 4 characters the Govee app shows, e.g. H805D_1A2B


def capability(dev):
    return MODELS.get((lights.get(dev) or found.get(dev) or {}).get("sku"), BASIC)


def light_settings(key, p):
    cap = capability(key)
    if not cap["razer"]:
        p = {k: v for k, v in p.items() if k in ("on", "brightness", "colors")}
        p = dict(p, effect="solid", bulbs=None, animation=None)
        p["colors"] = p.get("colors", (st.get(key) or DEFAULT)["colors"])[:1]
    return clean(p, cap["bulbs"])


def add_light(dev, name, sku="", addr=""):
    lights[dev] = {"name": name, "sku": sku, "ip": addr}
    st.setdefault(dev, dict(DEFAULT, **light_settings(dev, {}), frame=[[0, 0, 0]] * capability(dev)["bulbs"], error=None, said=None, history=[]))
    schedule.setdefault(dev, {"use": True, "timers": []})


def lan_open():
    return sys.platform != "darwin" or lan_asked


def lan_gate():
    if not lan_open():
        raise OSError(errno.EHOSTUNREACH, "Local Network not asked yet")  # treated like a denied send


def lan_allowed():
    """macOS Local Network access: True, False (denied) or "unknown" (not asked yet, or no network). Probes at most every 2 s."""
    global lan_seen
    if sys.platform != "darwin":
        return True
    if not lan_open():
        return "unknown"
    if DRY:
        return {"allowed": True, "denied": False}.get(FAKE_NET, "unknown")
    if time.monotonic() - lan_seen[0] >= 2:
        # The scan group's discard port: a denied send fails with No route to host, and no light listens there.
        # Unicast is worse (a missing ARP reply gives the same error) and sends to the router are never blocked.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.sendto(b"", ("239.255.255.250", 9))
                ok = True
            except OSError as e:
                ok = False if e.errno == errno.EHOSTUNREACH else "unknown"
        if ok is True and lan_seen[1] is False:
            threading.Thread(target=wake, daemon=True).start()  # just allowed: bring the lights back
        lan_seen = (time.monotonic(), ok)
    return lan_seen[1]


def fallback_app():  # the no-swiftc build is a script, which macOS can't list under Local Network
    try:
        with APP_BIN.open("rb") as f:
            return f.read(2) == b"#!"
    except OSError:
        return False


def wake():
    """Startup scan, then each saved look again."""
    try:
        print("found:", {short(k): d["ip"] for k, d in discover().items()} or "no lights yet (turn on LAN Control in the Govee Home app)")
    except OSError as e:
        print("scan failed:", e)
    for k, s in list(st.items()):
        if s["on"] and k not in paused:
            try:
                reconnect(k)
            except OSError as e:
                s["error"] = f"reconnect: {e}"


def send(key, cmd, data):
    if cmd == "razer" and not capability(key)["razer"]:
        return
    if cmd == "turn":
        command_at[key] = time.monotonic()
    if not DRY:
        lan_gate()
        sock.sendto(json.dumps({"msg": {"cmd": cmd, "data": data}}).encode(), ((lights.get(key) or found[key])["ip"], 4003))


def razer(key, raw):
    if not capability(key)["razer"]:
        return
    x = 0
    for v in raw:
        x ^= v
    send(key, "razer", {"pt": base64.b64encode(bytes(raw + [x])).decode()})


def razer_frame(colors):
    body = [0xB0, 0x00, len(colors)] + [max(0, min(255, int(v))) for c in colors for v in c]  # 0x00 = no blend
    return [0xBB, 0x00, len(body) - 1] + body


ARM = [0xBB, 0x00, 0x01, 0xB1, 0x01]  # per-bulb mode on; re-sending it while streaming blacks the strip out


def mix(a, b, t):
    return [x + (y - x) * t for x, y in zip(a, b)]


def scale(c, k):
    return [v * k for v in c]


def hashf(i, k):  # stable 0..1 noise per bulb and time step
    return random.Random(i * 7919 + k * 104729).random()


def clean_animation(a, n=N):
    if not isinstance(a, dict) or set(a) - {"frames", "shift", "motion", "block"}:
        raise ValueError("Animation needs frames, optional shift and motion.")
    frames = a.get("frames")
    if not isinstance(frames, list) or not 1 <= len(frames) <= 16:
        raise ValueError("Animation needs 1-16 frames.")
    shift, motion = a.get("shift", 0), a.get("motion", "forward")
    block = a.get("block", 1)
    if type(block) is not int or not 1 <= block <= n:
        raise ValueError(f"Animation block must be 1-{n} bulbs.")
    if type(shift) is not int or not -n <= shift <= n or motion not in ("forward", "center", "outward"):
        raise ValueError("Invalid animation motion or shift.")
    out = []
    for f in frames:
        if not isinstance(f, dict) or set(f) - {"pattern", "duration", "fade"}:
            raise ValueError("Invalid animation frame.")
        pattern = rgb_list(f.get("pattern"), n)
        duration, fade = f.get("duration"), f.get("fade", 0)
        if type(duration) not in (int, float) or not 1 / FPS <= duration <= 60:
            raise ValueError("Frame duration must be 1/12 to 60 seconds.")
        if type(fade) not in (int, float) or not 0 <= fade <= 1:
            raise ValueError("Fade must be 0-1 of the frame duration.")
        out.append({"pattern": pattern, "duration": duration, "fade": fade})
    return {"frames": out, "shift": shift, "motion": motion, "block": block}


def rgb_list(cs, limit):
    if (not isinstance(cs, list) or not 1 <= len(cs) <= limit or
        any(not isinstance(c, list) or len(c) != 3 or
            any(type(v) is not int or not 0 <= v <= 255 for v in c) for c in cs)):
        raise ValueError(f"Use 1-{limit} RGB colors with integer channels 0-255.")
    return cs


def custom_frame(a, t, n, sp=1):
    if a is None:
        return [SILVER] * n
    fs = a["frames"]
    # every frame lasts a whole number of sent frames, so each step shows equally long (no 2-3-2 jerk)
    ticks = [max(1, round(f["duration"] * FPS / sp)) for f in fs]
    loops, at = divmod(round(t * FPS), sum(ticks))  # snap to the nearest sent frame, so a late send still lands on its tick
    j = 0
    while j < len(fs) - 1 and at >= ticks[j]:
        at -= ticks[j]
        j += 1
    f, nxt = fs[j], fs[(j + 1) % len(fs)]
    step = int(loops) * len(fs) + j
    blend = max(0, (at / ticks[j] - 1 + f["fade"]) / f["fade"]) if f["fade"] else 0
    out = []
    for i in range(n):
        x = min(i, n - 1 - i) if a["motion"] != "forward" else i
        if a["motion"] == "outward":
            x = -x
        c = f["pattern"][((x - step * a["shift"]) // a.get("block", 1)) % len(f["pattern"])]
        d = nxt["pattern"][((x - (step + 1) * a["shift"]) // a.get("block", 1)) % len(nxt["pattern"])]
        out.append(mix(c, d, blend))
    return out


def render(p, t, n=N):
    """One frame of n colors for settings p at time t (seconds)."""
    cs, sp, amt, e = p["colors"] or [SILVER], p["speed"], p["amount"], p["effect"]
    c0, c1 = cs[0], cs[1 % len(cs)]
    if e == "custom":
        return custom_frame(p.get("animation"), t, n, sp)  # speed plays the animation faster or slower
    if e == "solid":
        return [c0] * n
    if e == "pattern":
        return [cs[i % len(cs)] for i in range(n)]
    if e == "paint":
        return (p["bulbs"] or [c0] * n)[:n]
    if e == "smoothwave":  # neighbours opposite, slow even sine fade, each a bit after the last
        return [mix(c0, c1, (math.sin(t * sp * 3.77 - i * amt * 1.2 + (i % 2) * math.pi) + 1) / 2) for i in range(n)]
    if e == "wave":  # same, but holds each color then switches fast
        out = []
        for i in range(n):
            v = (math.sin(t * sp * 9.42 - i * amt * 1.2 + (i % 2) * math.pi) + 1) / 2
            out.append(mix(c0, c1, v * v * (3 - 2 * v)))
        return out
    if e == "chase":
        shift, block = int(t * sp * 6), max(1, int(1 + amt * 6))
        return [cs[((i + shift) // block) % len(cs)] for i in range(n)]
    if e == "march":  # the colors in order, one per bulb, all stepping one bulb along at a time
        step = int(t * sp * 4)
        return [cs[(i - step) % len(cs)] for i in range(n)]
    if e == "breathe":
        k = 0.15 + 0.85 * (math.sin(t * sp * 1.5) + 1) / 2
        return [scale(cs[i % len(cs)], k) for i in range(n)]
    if e == "rainbow":
        return [[255 * max(0, min(1, abs(((i / n + t * sp * 0.1) % 1 * 6 + o) % 6 - 3) - 1)) for o in (0, 4, 2)]
                for i in range(n)]
    if e == "twinkle":  # base color, random bulbs flash to the sparkle color and fade
        out, step = [], t * sp * 4
        for i in range(n):
            k = int(step + seeds[i % N])
            age = step + seeds[i % N] - k  # 0..1 inside this bulb's slot
            lit = hashf(i, k) < amt * 0.5
            out.append(mix(c0, c1, (1 - age) ** 2) if lit else c0)
        return out
    if e == "comet":  # bright heads with fading tails run down the strip
        out, gap = [], max(8, int(n * (1.1 - amt)))
        head = t * sp * 20
        for i in range(n):
            d = (head - i) % gap
            out.append(mix(c0, c1, max(0.0, 1 - d / (gap * 0.45))))
        return out
    if e == "fire":  # warm flicker
        out, k = [], int(t * sp * 10)
        for i in range(n):
            f = hashf(i, k) * 0.6 + hashf(i // 3, k + 1) * 0.4
            out.append(scale(mix(c0, c1, f), 1 - amt * 0.6 * (1 - f)))
        return out
    if e == "aurora":  # slow color bands drifting through all colors
        out = []
        for i in range(n):
            x = (math.sin(i / n * 6.283 * (1 + amt * 2) + t * sp * 0.8) + 1) / 2 * (len(cs) - 1)
            j = min(int(x), len(cs) - 2) if len(cs) > 1 else 0
            out.append(mix(cs[j], cs[min(j + 1, len(cs) - 1)], x - j))
        return out
    # shimmer: neighbours alternate colors, each bulb drifts between them and twinkles on its own clock
    out = []
    for i in range(n):
        ph = seeds[i % N] + t * sp * rates[i % N] * 2.2
        a, b = cs[i % len(cs)], cs[(i + 1) % len(cs)]
        c = mix(a, b, ((math.sin(ph * 0.35) + 1) / 2) ** 3)
        tw = ((math.sin(ph) + 1) / 2) ** 2
        c = scale(c, 1 - amt * 0.7 * (1 - tw))
        if tw > 0.93:
            c = mix(c, SILVER, (tw - 0.93) / 0.07 * amt)
        out.append(c)
    return out


def look(s):
    return {k: s.get(k) for k in KEYS}


def save():
    data = {k: {"look": look(s), "history": s["history"]} for k, s in st.items()}
    data["_lights"] = lights
    data["_themes"] = themes
    data["_schedule"] = schedule
    data["_location"] = location
    data["_govee_key"] = govee_key
    data["_outside"] = outside
    data["_paused"] = sorted(paused)
    data["_ask"] = ask_on
    data["_lan_asked"] = lan_asked
    tmp = SAVE.with_suffix(".tmp")
    try:
        tmp.touch(mode=0o600, exist_ok=True)
        tmp.chmod(0o600)
        tmp.write_text(json.dumps(data))
        tmp.replace(SAVE)  # atomic, so a crash mid-write never loses the saved look
        return True
    except OSError as e:
        print("could not save:", e)


def load():
    bad = False
    def keep_bad():
        backup = SAVE.with_name(SAVE.name + ".bad")
        if not backup.exists():
            shutil.copy2(SAVE, backup)
            backup.chmod(0o600)
    try:
        data = json.loads(SAVE.read_text())
    except OSError:
        return
    except ValueError:
        keep_bad()
        return
    if not isinstance(data, dict):
        keep_bad()
        return
    def section(name):
        nonlocal bad
        value = data.get(name, {})
        if not isinstance(value, dict):
            bad = True
            return {}
        return value
    global location, govee_key, ask_on, lan_asked
    govee_key = data.get("_govee_key", "")
    if not isinstance(govee_key, str):
        bad, govee_key = True, ""
    for name in ("_ask", "_lan_asked"):
        if name in data and type(data[name]) is not bool:
            bad = True
    ask_on = data.get("_ask") is not False
    lan_asked = data.get("_lan_asked") is True
    for dev, v in section("_lights").items():
        if (not isinstance(dev, str) or not 0 < len(dev) <= 64 or not isinstance(v, dict) or
            any(not isinstance(v.get(x, ""), str) for x in ("name", "sku", "ip"))):
            bad = True
            continue
        add_light(dev, (v.get("name") or "Light")[:MAX_NAME], v.get("sku", ""), v.get("ip", ""))
    if data.get("_location") is not None:
        try:
            location = clean_location(*data["_location"])
        except (TypeError, ValueError):
            bad = True
    for k, v in section("_schedule").items():
        if k in schedule:
            try:
                if (not isinstance(v, dict) or type(v.get("use", True)) is not bool or
                    "timers" in v and not isinstance(v["timers"], list)):
                    raise ValueError("Invalid saved schedule")
                schedule[k] = migrate(v)
                if "timers" in v and len(schedule[k]["timers"]) != len(v["timers"]):
                    bad = True
            except (ValueError, TypeError, AttributeError):
                bad = True
    for name, p in section("_themes").items():
        try:
            if not isinstance(p, dict):
                raise ValueError("Invalid saved theme")
            themes[str(name)[:40]] = clean(p)
        except (ValueError, TypeError, OverflowError):
            bad = True
    for k in st:
        if k not in data or not isinstance(data[k], dict):
            bad = True
            st[k]["on"] = False
            continue
        d = data[k]
        history = d.get("history", [])
        if not isinstance(history, list):
            bad, history = True, []
        valid = []
        for h in history[-HISTORY:]:
            try:
                if not isinstance(h, dict):
                    raise ValueError("Invalid saved history")
                valid.append(clean(dict(DEFAULT, **h)))
            except (ValueError, TypeError, OverflowError):
                bad = True
        try:
            raw = d.get("look", {})
            if not isinstance(raw, dict):
                raise ValueError("Invalid saved look")
            previous = clean(dict(DEFAULT, **raw))
            try:
                current = light_settings(k, previous)
            except ValueError:  # older saves may have treated a smaller strip as an 84-slot strip
                current = light_settings(k, dict(previous, effect="solid", animation=None))
                valid.append(previous)
            st[k].update(current)
        except (ValueError, TypeError, OverflowError):
            bad = True
            st[k]["on"] = False  # a corrupt look must not turn a light on at startup
        st[k]["history"] = valid[-HISTORY:]
    for k, events in section("_outside").items():
        if k not in st:
            continue
        if not isinstance(events, list):
            bad = True
            continue
        valid = []
        for event in events[-500:]:
            try:
                if not isinstance(event, dict) or type(event.get("on")) is not bool:
                    raise ValueError("Invalid power event")
                dt.datetime.fromisoformat(event["at"])
                valid.append({"at": event["at"], "on": event["on"]})
            except (KeyError, ValueError, TypeError):
                bad = True
        outside[k] = valid
    saved_paused = data.get("_paused", [])
    if not isinstance(saved_paused, list) or any(not isinstance(k, str) for k in saved_paused):
        bad = True
    else:
        paused.update(k for k in saved_paused if k in st)
    if bad:
        keep_bad()


def clean(p, n=N):
    """Validate settings from the UI or Claude."""
    p = {k: v for k, v in p.items() if k in KEYS}
    if "effect" in p and p["effect"] not in EFFECTS:
        raise ValueError(f"effect must be one of {EFFECTS}")
    if "animation" in p and p["animation"] is not None:
        p["animation"] = clean_animation(p["animation"], n)
    if "colors" in p:
        cs = [[max(0, min(255, int(v))) for v in c][:3] for c in p["colors"]][:6]
        if not cs or any(len(c) != 3 for c in cs):
            raise ValueError("colors must be a list of [r,g,b]")
        p["colors"] = cs
    if p.get("bulbs") is not None:
        b = rgb_list(p["bulbs"], N)[:n]
        p["bulbs"] = b + [[0, 0, 0]] * (n - len(b))
    for k in ("speed", "amount"):
        if k in p:
            p[k] = max(0.05, min(5.0, float(p[k])))
    if "brightness" in p:
        p["brightness"] = max(1, min(100, int(p["brightness"])))
    if "on" in p:
        if type(p["on"]) is not bool:
            raise ValueError("Power must be true or false.")
    return p


def push(key, p, remember=True):
    """Send settings to the light. Hardware first, so the saved look only changes if the send works."""
    p = light_settings(key, p)
    paused.discard(key)
    if not capability(key)["razer"]:
        c = (p.get("colors") or st[key]["colors"])[0]
        send(key, "colorwc", {"color": dict(zip(("r", "g", "b"), c)), "colorTemInKelvin": 0})
    if "on" in p:
        send(key, "turn", {"value": 1 if p["on"] else 0})
    if "brightness" in p:
        send(key, "brightness", {"value": p["brightness"]})
    with lock:
        s = st[key]
        before = look(s)
        s.update(p)
        s["frame"] = [[int(v) for v in c] for c in render(s, time.time(), capability(key)["bulbs"])]
        added = remember and any(s[k] != before[k] for k in KEYS if k not in ("on", "brightness"))
        if added:
            s["history"] = (s["history"] + [before])[-HISTORY:]  # only looks go in undo, not on/off taps
        s["error"] = None
        save()
    if s["on"]:
        razer(key, ARM)
    return added


def undo(key):
    with lock:
        if not st[key]["history"]:
            raise ValueError("Nothing to undo.")
        prev = st[key]["history"][-1]
        push(key, prev, remember=False)
        st[key]["history"].pop()
        save()


def streamer():
    t = time.time()
    while True:
        t = max(t + 1 / FPS, time.time() - 1)  # fixed 1/12 s ticks, so frames do not drift late; resync after a stall
        for key, s in list(st.items()):  # setup can add or forget a light mid-loop
            if not capability(key)["razer"] or not s["on"] or key in paused:
                continue
            try:
                s["frame"] = [[int(v) for v in c] for c in render(s, t, capability(key)["bulbs"])]
                razer(key, razer_frame(s["frame"]))
            except Exception as e:  # one bad frame or network blip must not kill the stream
                s["error"] = f"stream: {e}"
        time.sleep(max(0, t + 1 / FPS - time.time()))


def clean_location(lat, lon):
    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):  # also rejects NaN
        raise ValueError("Latitude goes from -90 to 90 and longitude from -180 to 180.")
    return [round(lat, 2), round(lon, 2)]  # about 1 km: plenty for sunset, no need to keep more


def sunset(day, where=None):
    """Local sunset on a date (NOAA approximation, about a minute off). None until a location is set."""
    if not (where or location):
        return None
    lat, lon = where or location
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eq = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    dec = 0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
    x = math.cos(math.radians(90.833)) / (math.cos(math.radians(lat)) * math.cos(dec)) - math.tan(math.radians(lat)) * math.tan(dec)
    ha = math.degrees(math.acos(max(-1.0, min(1.0, x))))  # clamped: near the poles the sun may not set at all
    utc_min = 720 - 4 * (lon - ha) - eq
    base = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=utc_min)
    return base.astimezone().replace(tzinfo=None)


def when(spec, day):
    """'sunset', 'sunset+30', 'sunset-15' or 'HH:MM' -> local datetime on that day (None if unset)."""
    if not spec:
        return None
    if spec.startswith("sunset"):
        s = sunset(day)
        return s and s + dt.timedelta(minutes=int(spec[6:] or 0))
    h, m = map(int, spec.split(":"))
    return dt.datetime(day.year, day.month, day.day, h, m)


def clean_time(spec, location_set=None):
    """'HH:MM', 'sunset', 'sunset+30' or 'sunset-15' (up to 120 min either way) -> normalized spec."""
    spec = str(spec).strip().lower().replace(" ", "")
    try:
        if spec.startswith("sunset"):
            m = int(spec[6:] or 0)
        else:
            h, m = map(int, spec.split(":"))
    except ValueError:
        raise ValueError('time must be "HH:MM" or "sunset", "sunset+30", "sunset-15"') from None
    if spec.startswith("sunset"):
        if not (location is not None if location_set is None else location_set):
            raise ValueError("Add your location first, so the app knows when sunset is.")
        if not -120 <= m <= 120:
            raise ValueError("Sunset can move up to 120 minutes either way.")
        return f"sunset{m:+d}" if m else "sunset"
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("time must be HH:MM")
    return f"{h:02d}:{m:02d}"


def clean_timer(t, old=None, strict=True, saved=None, location_set=None):
    """A timer from the page or Claude, patched onto old (keeps old's id). strict: its theme must exist now."""
    if not isinstance(t, dict) or set(t) - {"id", "time", "action", "days", "enabled", "theme"}:
        raise ValueError("Send a timer as {time, action, days, enabled, theme}.")
    t = dict(old or {}, **t)
    if t.get("action") not in ("on", "off"):
        raise ValueError('A timer\'s action is "on" or "off".')
    if not t.get("time"):
        raise ValueError("Pick a time.")
    if type(t.get("enabled", True)) is not bool:
        raise ValueError("enabled must be true or false.")
    theme = t.get("theme") if t["action"] == "on" else None  # an off timer has no look
    if theme is not None and (not isinstance(theme, str) or strict and theme not in (themes if saved is None else saved) and theme not in BUILTIN):
        raise ValueError("Pick one of your themes or presets, or keep the last look.")
    out = {"id": str((old or {}).get("id") or os.urandom(4).hex()), "time": clean_time(t["time"], location_set), "action": t["action"],
           "days": clean_days(t.get("days")), "enabled": t.get("enabled", True)}
    return out | ({"theme": theme} if theme else {})


def change_timers(scheds, keys, add=(), edit=(), delete=(), saved=None, location_set=None):
    """Add timers, patch them by id and delete them by id on these lights, in scheds only. Raises before a bad change."""
    if not all(isinstance(x, (list, tuple)) for x in (add, edit, delete)):
        raise ValueError("add, edit and delete must be lists.")
    hit = set()
    for k in keys:
        before = scheds[k]["timers"]
        ts = scheds[k]["timers"] = [t for t in before if t["id"] not in delete]
        hit |= {t["id"] for t in before} & set(delete)
        for patch in edit:
            for j, t in enumerate(ts):
                if isinstance(patch, dict) and t["id"] == patch.get("id"):
                    ts[j] = clean_timer(patch, t, saved=saved, location_set=location_set)
                    hit.add(t["id"])
        ts += [clean_timer(t, saved=saved, location_set=location_set) for t in add]
        if len(ts) > MAX_TIMERS:
            raise ValueError(f"{lights[k]['name']} can have up to {MAX_TIMERS} timers. Delete one first.")
    if ({p.get("id") if isinstance(p, dict) else p for p in edit} | set(delete)) - hit:
        raise ValueError("That timer is gone. Reload the page.")


def migrate(v):
    """A saved schedule -> {"use", "timers"}. Old saves had one on and one off time sharing days (none = every day)."""
    timers = v.get("timers")
    if timers is None:
        try:
            days = clean_days(v.get("days"))
        except (ValueError, TypeError):
            days = None
        timers = [{"id": a, "time": v[a], "action": a, "days": days} for a in ("on", "off") if v.get(a)]
    out = []
    for t in timers[:MAX_TIMERS]:
        try:
            out.append(clean_timer(t, t, strict=False, location_set=True))  # a theme deleted since then just means the last look
        except (ValueError, TypeError):
            pass  # skip a broken timer, keep the rest
    return {"use": v.get("use") is not False, "timers": out}  # saved before the switch: on


def clean_days(days):
    """None -> every day; else a list of weekday numbers, 0 = Monday .. 6 = Sunday (Python's weekday())."""
    if days is None:
        return list(range(7))
    if not isinstance(days, list) or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in days):
        raise ValueError("days must be weekday numbers from 0 (Monday) to 6 (Sunday)")
    if not days:
        raise ValueError("Pick at least one day.")
    return sorted(set(days))


def due(timers, t, at):
    """Does timer t count on at's day? An off before every on timer that day ends the evening that began the day before."""
    day = at.date()
    ons = [o for o in (when(x["time"], day) for x in timers if x["action"] == "on") if o]
    if t["action"] == "off" and ons and at < min(ons):
        day -= dt.timedelta(days=1)
    return day.weekday() in t["days"]


def next_run(timers, now):
    """(when, timer) for the next enabled timer within a week, or None."""
    runs = [(at, t) for t in timers if t["enabled"] for d in range(8)
            if (at := when(t["time"], now.date() + dt.timedelta(days=d))) and at > now and due(timers, t, at)]
    return min(runs, key=lambda r: r[0], default=None)


fired = set()  # (light, timer id, local date), including the repeated DST hour


def schedule_tick(last, now):
    with lock:
        fired.intersection_update({x for x in fired if x[2] >= now.date() - dt.timedelta(days=1)})
        runs = []
        day = last.date() - dt.timedelta(days=1)  # sunset offsets can spill into a neighboring date
        while day <= now.date() + dt.timedelta(days=1):
            for k, sc in schedule.items():
                if not sc["use"]:
                    continue
                for t in sc["timers"]:
                    at = t["enabled"] and when(t["time"], day)
                    occurrence = (k, t["id"], day)
                    if at and last < at <= now and due(sc["timers"], t, at) and occurrence not in fired:
                        runs.append((at, k, t, occurrence))
            day += dt.timedelta(days=1)
        for at, k, t, occurrence in sorted(runs, key=lambda r: r[0]):
            fired.add(occurrence)
            p = {"on": t["action"] == "on"}
            if t.get("theme"):
                p = dict(themes.get(t["theme"]) or BUILTIN.get(t["theme"]) or {}, **p)
            try:
                push(k, p)
            except (OSError, ValueError) as e:
                st[k]["error"] = f"schedule: {e}"


def schedule_loop():
    last = dt.datetime.now()
    while True:
        time.sleep(20)
        now = dt.datetime.now()
        schedule_tick(last, now)
        last = now


def rediscover_loop():
    while True:
        time.sleep(120)
        try:
            discover()
        except OSError:
            pass


def reconnect(key):
    """Re-send power, brightness and per-bulb mode (after a power cut or a router change). Run discover() first."""
    push(key, look(st[key]), remember=False)


def parse_scan(raw):
    """One LAN scan reply -> (device id, ip, model), or None if it is not a usable Govee reply."""
    try:
        d = json.loads(raw)["msg"]["data"]
        dev, addr, sku = str(d["device"]), str(d["ip"]), str(d.get("sku") or "Govee")[:20]
        if not 0 < len(dev) <= 64 or not ipaddress.ip_address(addr).is_private:  # never stream to the internet
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return dev, addr, sku


def discover():
    """Ask every Govee light with LAN Control on to answer; keep saved lights' IPs current (DHCP can change them)."""
    global found
    with scan_lock:
        if DRY:
            replies = [json.dumps({"msg": {"cmd": "scan", "data": {"device": f"AA:BB:CC:DD:EE:FF:{0x1A + i:02X}:{0x2B + i:02X}",
                                                                   "ip": f"192.168.0.{20 + i}", "sku": FAKE_SKUS[i % len(FAKE_SKUS)]}}}) for i in range(FAKE)]
        else:
            lan_gate()
            replies = []
            r = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):  # not on Windows
                r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            r.bind(("", 4002)); r.settimeout(0.5)
            m = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            msg = json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode()
            end = time.time() + 3
            failed = None
            try:
                while time.time() < end:
                    try:
                        m.sendto(msg, ("239.255.255.250", 4001))
                        replies.append(r.recvfrom(4096)[0])
                    except socket.timeout:
                        pass
                    except OSError as e:
                        failed = e
            finally:
                r.close(); m.close()
            if not replies and getattr(failed, "errno", None) == errno.EHOSTUNREACH:
                raise failed  # blocked, not empty: say so instead of "no lights found"
        seen = {}
        for raw in replies:
            got = parse_scan(raw)
            if got:
                seen[got[0]] = {"ip": got[1], "sku": got[2]}
        found = seen
    with lock:
        moved = [k for k in lights if k in seen and lights[k]["ip"] != seen[k]["ip"]]
        for k in moved:
            lights[k]["ip"] = seen[k]["ip"]
        if moved:
            save()
    return seen


def save_lights(items):
    """Setup and rename: [{"id", "name"}, ...]. New ids must come from the last scan."""
    if not isinstance(items, list) or not 0 < len(items) <= 50:
        raise ValueError("Pick at least one light.")
    named = []
    for it in items:
        if not isinstance(it, dict):
            raise ValueError("send each light as {id, name}")
        dev, name = str(it.get("id", "")), " ".join(str(it.get("name", "")).split())[:MAX_NAME]
        if dev not in lights and dev not in found:
            raise ValueError("That light is no longer on the network. Tap Scan again.")
        if not name:
            raise ValueError("Give each light a name.")
        named.append((dev, name))
    new = []
    with lock:
        for dev, name in named:
            if dev in lights:
                lights[dev]["name"] = name
            else:
                add_light(dev, name, found[dev]["sku"], found[dev]["ip"])
                new.append(dev)
        save()
    for dev in new:  # turn it on in per-bulb mode, so the strip shows the stream right away
        push(dev, {"on": True, "brightness": st[dev]["brightness"]})


def forget_light(dev):
    with lock:
        if dev not in lights:
            raise ValueError("unknown light")
        for d in (lights, st, schedule, outside, observed, command_at):
            d.pop(dev, None)
        paused.discard(dev)
        save()


def govee_result(raw):
    if not isinstance(raw, dict):
        raise ValueError("Govee sent an unreadable response. Try refreshing again.")
    code = raw.get("code")
    if code in (401, 403):
        raise ValueError("Govee rejected the API key. Check it in the Govee Home app and try again.")
    if code == 429:
        raise ValueError("Govee's request limit was reached. Wait a minute, then refresh.")
    if code != 200:
        raise ValueError("Govee could not read this device. Try refreshing again later.")
    return raw


def govee_read(key, endpoint, device=None):
    # An explicit allowlist keeps this client read-only, including its POST queries.
    if endpoint not in ("user/devices", "device/scenes", "device/diy-scenes"):
        raise ValueError("Only Govee read queries are allowed.")
    if DRY:
        if not SAMPLE:
            raise ValueError("Dry mode does not contact Govee. Use saved samples to test imports.")
        raw = json.loads((HERE / "samples/govee.json").read_text())
        return govee_result(raw[endpoint.split("/")[-1]])
    body = None if device is None else json.dumps({"requestId": str(time.time_ns()),
             "payload": {"sku": device["sku"], "device": device["device"]}}).encode()
    req = request.Request("https://openapi.api.govee.com/router/api/v1/" + endpoint, data=body,
                          headers={"Govee-API-Key": key, "Content-Type": "application/json"})
    # Do not forward the secret to redirect destinations, even if Govee redirects.
    class NoRedirect(request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with request.build_opener(NoRedirect).open(req, timeout=8) as response:
            return govee_result(json.loads(response.read(2_000_001)))
    except error.HTTPError as e:
        govee_result({"code": e.code})
    except (OSError, ValueError) as e:
        if isinstance(e, ValueError) and str(e).startswith("Govee"):
            raise
        raise ValueError("Cannot reach Govee. Check this computer's internet connection and refresh again.") from None


def parse_devices(raw):
    data = govee_result(raw).get("data")
    if not isinstance(data, list) or any(not isinstance(d, dict) or not isinstance(d.get("device"), str)
          or not isinstance(d.get("sku"), str) or not isinstance(d.get("capabilities", []), list) for d in data):
        raise ValueError("Govee sent an unreadable device list. Try refreshing again.")
    return [d for d in data if d.get("type", "devices.types.light") == "devices.types.light"]


def parse_scenes(capabilities):
    scenes = []
    if not isinstance(capabilities, list):
        raise ValueError("Govee sent an unreadable scene list. Try refreshing again.")
    for cap in capabilities:
        if not isinstance(cap, dict) or cap.get("instance") not in ("lightScene", "diyScene", "snapshot"):
            continue
        params = cap.get("parameters") or {}
        options = params.get("options") if isinstance(params, dict) else None
        if options is None:
            continue
        if not isinstance(options, list):
            raise ValueError("Govee sent an unreadable scene list. Try refreshing again.")
        for option in options:
            if isinstance(option, dict) and isinstance(option.get("name"), str) and "value" in option:
                row = {"name": option["name"][:120], "instance": cap["instance"], "value": option["value"]}
                if row not in scenes:
                    scenes.append(row)
    return scenes


def import_devices(key):
    global govee_key, govee_devices, govee_read_at
    if not isinstance(key, str) or not key.strip() or len(key) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in key.strip()):
        raise ValueError("Paste the API key from Govee Home → Profile → Settings → Apply for API key.")
    with govee_lock:
        if time.monotonic() - govee_read_at < 60:
            raise ValueError("Wait a minute before refreshing Govee again.")
        govee_read_at = time.monotonic()
        devices = parse_devices(govee_read(key.strip(), "user/devices"))
        with lock:
            previous = govee_key
            govee_key = key.strip()
            if not save():
                govee_key = previous
                raise ValueError("Cannot save the API key. Check that state.json is writable and try again.")
        govee_devices = devices
        govee_cache.clear()
    return {"ok": True, "count": len(devices)}


def imported_scenes(dev):
    with govee_lock:
        if not govee_key:
            return {"scenes": [], "note": "Connect your Govee account to read scene names."}
        if dev not in lights:
            raise ValueError("Pick one saved light to read its Govee scenes.")
        cached = govee_cache.get(dev)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        device = next((d for d in govee_devices if d["device"].upper() == dev.upper()), None)
        if device is None:
            return {"scenes": [], "note": "Open Govee account at the bottom of the page and tap Refresh Govee to load this light's scenes."}
        scenes, notes = parse_scenes(device.get("capabilities", [])), []
        for endpoint in ("device/scenes", "device/diy-scenes"):
            try:
                raw = govee_read(govee_key, endpoint, device)
                payload = raw.get("payload")
                more = parse_scenes(payload.get("capabilities") if isinstance(payload, dict) else None)
                scenes.extend(row for row in more if row not in scenes)
            except ValueError as e:
                notes.append(str(e))
        result = {"scenes": scenes, "note": " ".join(dict.fromkeys(notes))}
        govee_cache[dev] = (time.monotonic(), result)
        return result


def parse_status(raw):
    try:
        msg = json.loads(raw)["msg"]
        value = msg["data"]["onOff"]
        return bool(value) if msg["cmd"] == "devStatus" and type(value) is int and value in (0, 1) else None
    except (ValueError, KeyError, TypeError):
        return None


def repeated_changes(events):
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


def observe_power(dev, power, now, stamp):
    with lock:
        if dev not in st:
            return
        previous = observed.get(dev)
        observed[dev] = (power, stamp)
        if previous is None or stamp - previous[1] > 30:
            if st[dev]["on"] != power:
                paused.add(dev)
                st[dev]["on"] = power
                save()
            return  # first reading or a long gap gives no reliable change time
        if stamp - command_at.get(dev, -1000) < 20:
            return
        if power == previous[0]:
            if st[dev]["on"] != power:
                paused.add(dev)
                st[dev]["on"] = power
                save()
            return
        if previous[1] <= command_at.get(dev, -1000):
            return
        events = outside.setdefault(dev, [])
        cutoff = (now - dt.timedelta(days=35)).isoformat()
        events[:] = [e for e in events if e["at"] >= cutoff][-499:]
        events.append({"at": now.isoformat(timespec="seconds"), "on": power})
        paused.add(dev)
        st[dev]["on"] = power
        st[dev]["frame"] = [[0, 0, 0]] * N
        save()


def statuses(addrs):
    """Ask each address for devStatus; yield (ip, on) as replies come in, for up to 2 seconds."""
    lan_gate()
    left = set(addrs)
    with scan_lock:  # scans and devStatus share the reply port
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as r:
            r.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            r.bind(("", 4002)); r.settimeout(0.5)
            for addr in left:
                r.sendto(b'{"msg":{"cmd":"devStatus","data":{}}}', (addr, 4003))
            end = time.monotonic() + 2
            while left and time.monotonic() < end:
                try:
                    raw, addr = r.recvfrom(4096)
                    power = parse_status(raw)
                    if addr[0] in left and power is not None:
                        left.discard(addr[0])
                        yield addr[0], power
                except socket.timeout:
                    pass


def status_loop():
    while True:
        if not DRY:
            try:
                targets = {d["ip"]: dev for dev, d in list(lights.items())}
                for addr, power in statuses(targets):
                    observe_power(targets[addr], power, dt.datetime.now(), time.monotonic())
            except OSError:
                pass  # missing samples cannot establish a timed power change
        time.sleep(5)


def flash(dev, step=0.35):
    """Blink a found or saved light 3 times (about 2 s) so the user can see which one it is, then put it back."""
    if dev not in lights and dev not in found:
        raise ValueError("That light is no longer on the network. Tap Scan again.")
    with lock:
        if dev in flashing:
            return False  # already blinking: a second tap does nothing
        flashing.add(dev)
    try:
        if dev in lights:
            was_on = st[dev]["on"]
        else:  # not saved yet, so only the light knows if it is on
            addr = found[dev]["ip"]
            was_on = True if DRY else dict(statuses([addr])).get(addr)
            if was_on is None:
                raise ValueError("That light didn't answer. Check it has power, then try again.")
        for i in range(6):  # off, on, off, on, off, on (or the reverse), ending as it started
            send(dev, "turn", {"value": int(was_on == (i % 2 == 1))})
            time.sleep(step)
        if was_on and dev in lights and dev not in paused:
            razer(dev, ARM)  # back into per-bulb mode after power on, so the stream shows again
        return True
    finally:
        flashing.discard(dev)


def clip(p):
    """Small looping preview, rendered by the same code that drives the lights."""
    p = dict(DEFAULT, **p)
    return [[[int(v) for v in c] for c in render(p, f / 12, 24)] for f in range(36)]


def previews():
    return {"groups": [["Presets", [[n, p, clip(p)] for n, p in PRESETS]]],
            "effects": {e: clip({"effect": e}) for e in EFFECTS if e not in ("paint", "custom")}}


def theme_list():
    return [[n, p, clip(p)] for n, p in themes.items()]


def save_theme(name, p):
    name = " ".join(str(name).split())[:40]
    if not name:
        raise ValueError("Give the theme a name.")
    if name not in themes and len(themes) >= MAX_THEMES:
        raise ValueError(f"You can keep up to {MAX_THEMES} themes. Delete one first.")
    themes[name] = {k: v for k, v in clean(dict(p)).items() if k != "on"}
    with lock:
        save()


def disconnect_govee():
    global govee_key, govee_devices
    with govee_lock:
        with lock:
            previous = govee_key
            govee_key = ""
            if not save():
                govee_key = previous
                raise ValueError("Cannot remove the key. Check the saved-state file is writable and try again.")
        govee_devices = []
        govee_cache.clear()


def ask_keys(selected):
    if selected == "all":
        return list(lights)
    return [selected] if isinstance(selected, str) and selected in lights else []


ASK = """Govee controller. JSON only: {"say":"short reply or one clarification question","actions":[]}, max 16 actions. Unclear: ask, no actions. Only requested changes; no code. No lights: questions, themes and location still work; explain when a light is needed. target=light id|selected|all (default selected); never guess a missing selection.
Actions:
{"type":"set","settings":{}} optional fields: on bool, brightness 1-100, effect, colors 1-6 RGB integer triples 0-255, speed .05-5, amount .05-1, bulbs exactly light's slot count RGBs or null, animation or null. Effects: wave, smoothwave (slow), shimmer, twinkle, comet, fire, aurora, solid, pattern, chase, march (colors in order, one per bulb, stepping along), breathe, rainbow, paint, custom. razer=false supports only power, brightness, one solid color and timers.
custom animation={"frames":[{"pattern":[RGB,...],"duration":seconds,"fade":0..1}],"shift":integer,"motion":"forward|center|outward","block":integer}. 1-16 frames, 1..bulbs colors/pattern repeated, duration 1/12..60s, fade at end toward next shifted frame; shift -bulbs..bulbs each frame; block 1..bulbs per color. Loop; speed multiplies playback, so send speed 1 with a new custom animation. Prefer short custom patterns for new effects. Slow police: red/blue pattern, block half bulbs, shift half bulbs, duration 2. center moves inward.
{"type":"paint","indices":[0,1],"color":[r,g,b]} zero based, only listed bulbs.
{"type":"save_theme","name":"name","settings":{}} creates from DEFAULT + settings without a light; omit settings to save target's resulting look (one look required). {"type":"apply_theme","name":"theme or preset"} keeps brightness unless asked. {"type":"rename_theme","name":"old","new":"new"}, {"type":"delete_theme","name":"name"}.
{"type":"schedule","schedule":{"add":[{"time":"HH:MM|sunset|sunset-20","action":"on|off","days":[5,6],"theme":"name","enabled":true}],"edit":[{"id":"id","enabled":false}],"delete":["id"],"use":true}} optional keys; edit=id+changes. Mon=0..Sun=6, default daily; sunset offset +/-120min, requires location to run. theme/preset on timers only; omit=last look. Max 10 timers/light. use=master switch, keep unless asked. Never changes lights now. Midnight off ends the preceding on-night; use same days as on.
{"type":"location","lat":number,"lon":number} only user-provided coordinates; ask if unknown. {"type":"rename_light","name":"name"}, {"type":"undo"}, {"type":"flash"}, {"type":"reconnect"}, {"type":"forget_light"}, {"type":"scan"}, {"type":"save_lights","lights":[{"id":"discovered id","name":"name"}]}. Setup/network/account actions must be alone; scan then let user choose discovered lights.
{"type":"govee_refresh"}, {"type":"govee_disconnect"}, {"type":"govee_scenes"}, {"type":"ask_use","use":false}, {"type":"clear_chat"}. Enter account keys in page; browser location/QR/preview/help are page controls. Keep brightness unless asked. Names max 30 light/40 theme. Chat expires in 5 min."""
CHAT_TTL, CHAT_TURNS = 1200, 6
chat = {"at": 0.0, "turns": []}  # short memory for follow-ups; forgotten after 20 quiet minutes so each ask stays cheap


def chat_view():
    if time.time() - chat["at"] > CHAT_TTL:
        chat["turns"] = []
    return {"turns": chat["turns"], "expires_in": max(0, int(chat["at"] + CHAT_TTL - time.time()))}


def ask_prompt(keys, text):
    bulbs = any(w in text.lower() for w in ("bulb", "paint", "pixel"))
    def small(p):
        return {k: v for k, v in p.items() if (k != "bulbs" or bulbs) and v is not None}
    data = {"selected": keys, "lights": {k: {"name": lights[k]["name"], "look": small(look(v)),
            "capabilities": capability(k), "schedule": schedule[k]} for k, v in st.items()}, "presets": BUILTIN,
            "themes": {k: small(v) for k, v in themes.items()},
            "history": {k: [small(h) for h in st[k]["history"][-3:]] for k in keys},
            "location_set": location is not None, "found": [{"id": k, "sku": d["sku"]} for k, d in found.items() if k not in lights],
            "chat": chat_view()["turns"], "request": text[:500]}
    return json.dumps(data, separators=(",", ":"))


def ask_claude(keys, text):
    if not ask_lock.acquire(blocking=False):
        raise ValueError("Claude is still working on the last ask. Wait a second.")
    try:
        out = subprocess.run(["claude", "-p", "--model", "haiku", "--tools", "", "--strict-mcp-config",
                              "--no-session-persistence", "--effort", "low", "--output-format", "text", "--system-prompt", ASK,
                              "--disable-slash-commands", "--setting-sources", "",
                              "--exclude-dynamic-system-prompt-sections"],
                             input=ask_prompt(keys, text), capture_output=True, text=True, timeout=60, cwd=HERE)
        if out.returncode or len(out.stdout) > MAX_BODY:
            raise ValueError("Claude could not answer. Try again.")
        try:
            return json.loads(out.stdout)
        except ValueError:
            raise ValueError("Claude returned invalid JSON. Try again.")
    except FileNotFoundError:
        raise ValueError("The claude command was not found on this computer.")
    finally:
        ask_lock.release()


def apply_answer(answer, keys):
    actions = answer.get("actions") if isinstance(answer, dict) else None
    # Network actions take govee_lock before lock; keep them outside the batch lock.
    if (isinstance(actions, list) and len(actions) == 1 and isinstance(actions[0], dict) and
        actions[0].get("type") in ("flash", "reconnect", "forget_light", "scan", "save_lights", "govee_refresh", "govee_disconnect", "govee_scenes", "ask_use", "clear_chat")):
        return _apply_answer(answer, keys)
    with lock:
        return _apply_answer(answer, keys)


def _apply_answer(answer, keys):
    global location, ask_on
    # Validate the whole batch against copies before any device or saved-state change.
    if not isinstance(answer, dict) or set(answer) - {"say", "actions"} or not isinstance(answer.get("say"), str):
        raise ValueError("Claude returned an invalid answer.")
    actions = answer.get("actions")
    if not isinstance(actions, list) or len(actions) > 16:
        raise ValueError("Claude must return up to 16 actions.")
    looks, saved = {k: look(v) for k, v in st.items()}, dict(themes)
    histories = {k: list(v["history"]) for k, v in st.items()}
    before_histories = {k: list(h) for k, h in histories.items()}
    scheds = {k: dict(v, timers=list(v["timers"])) for k, v in schedule.items()}
    changed_schedules = set()
    location_set = location is not None
    plan = []
    for a in actions:
        if not isinstance(a, dict):
            raise ValueError("Invalid action.")
        kind = a.get("type")
        fields = {"set": {"settings"}, "paint": {"indices", "color"}, "schedule": {"schedule"},
                  "save_theme": {"name", "settings"}, "apply_theme": {"name"},
                  "rename_theme": {"name", "new"}, "delete_theme": {"name"},
                  "rename_light": {"name"}, "undo": set(), "location": {"lat", "lon"},
                  "flash": set(), "reconnect": set(), "forget_light": set(), "scan": set(),
                  "save_lights": {"lights"}, "govee_refresh": set(), "govee_disconnect": set(),
                  "govee_scenes": set(), "ask_use": {"use"}, "clear_chat": set()}
        if kind not in fields or set(a) - (fields[kind] | {"type", "target"}):
            raise ValueError("Unknown action or field.")
        target = a.get("target", "selected")
        if not isinstance(target, str):
            raise ValueError("Invalid light target.")
        targets = list(st) if target == "all" else list(keys) if target == "selected" else [target]
        needs_light = kind in ("set", "apply_theme", "paint", "schedule", "rename_light", "undo", "reconnect", "forget_light", "govee_scenes") or kind == "save_theme" and "settings" not in a
        if needs_light and (not targets or any(k not in st for k in targets)):
            raise ValueError("Add a light in Manage lights first." if not st else "Pick a saved light, then try again. The selected light may have been forgotten.")
        value = None
        if kind in ("flash", "reconnect", "forget_light", "scan", "save_lights", "govee_refresh", "govee_disconnect", "govee_scenes", "ask_use", "clear_chat"):
            # Network/setup operations cannot be simulated as part of an atomic look batch.
            if len(actions) != 1:
                raise ValueError("Ask for setup, network or chat changes separately.")
            if kind == "flash":
                if not targets or any(k not in st and k not in found for k in targets):
                    raise ValueError("Pick a saved or discovered light to flash. Scan if needed.")
                for k in targets:
                    flash(k)
            elif kind == "reconnect":
                seen = discover()
                if any(k not in seen for k in targets):
                    raise ValueError("Could not find the light. Check power and LAN Control, then scan again.")
                for k in targets:
                    reconnect(k)
            elif kind == "forget_light":
                for k in targets:
                    forget_light(k)
            elif kind == "scan":
                discover()
                return f"Found {len(found)} lights. Choose lights in Manage lights to add them.", False
            elif kind == "save_lights":
                save_lights(a.get("lights"))
            elif kind == "govee_refresh":
                import_devices(govee_key)
            elif kind == "govee_disconnect":
                disconnect_govee()
            elif kind == "govee_scenes":
                if len(targets) != 1:
                    raise ValueError("Pick one light to read its Govee scenes.")
                return json.dumps(imported_scenes(targets[0]))[:300], False
            elif kind == "ask_use":
                if type(a.get("use")) is not bool:
                    raise ValueError("Ask use must be true or false.")
                with lock:
                    ask_on = a["use"]
                    save()
            else:
                chat.update(at=0, turns=[])
            return answer["say"][:300], False
        if kind == "location":
            value = clean_location(a.get("lat"), a.get("lon"))
            location_set = True
        elif kind in ("set", "apply_theme") or kind == "save_theme" and "settings" in a:
            value = a.get("settings") if kind != "apply_theme" else saved.get(a.get("name"), BUILTIN.get(a.get("name")))
            if not isinstance(value, dict) or set(value) - set(KEYS):
                raise ValueError("Invalid settings.")
            value = dict(value)
            if kind == "apply_theme":
                value.pop("brightness", None)
                value["on"] = True
            for field, low, high in (("speed", .05, 5), ("amount", .05, 1), ("brightness", 1, 100)):
                if field in value and (type(value[field]) not in (int, float) or not low <= value[field] <= high):
                    raise ValueError("Invalid " + field)
            if "on" in value and type(value["on"]) is not bool:
                raise ValueError("Power must be true or false.")
            for field, limit in (("colors", 6), ("bulbs", N)):
                if field in value and not (field == "bulbs" and value[field] is None):
                    rgb_list(value[field], limit)
            value = clean(value)
            if kind == "save_theme":
                value = dict(DEFAULT, **value)
                if value["effect"] == "custom" and value["animation"] is None:
                    raise ValueError("Custom effect needs an animation.")
            for k in targets if kind != "save_theme" else []:
                if kind == "set" and value.get("bulbs") is not None and len(a["settings"]["bulbs"]) != capability(k)["bulbs"]:
                    raise ValueError("Supply exactly this light's bulb count.")
                if set(value) - {"on", "brightness"} and dict(looks[k], **value) != looks[k]:
                    histories[k] = (histories[k] + [looks[k]])[-HISTORY:]
                looks[k] = dict(looks[k], **light_settings(k, value))
                if looks[k]["effect"] == "custom" and looks[k].get("animation") is None:
                    raise ValueError("Custom effect needs an animation.")
        elif kind == "paint":
            indices = a.get("indices")
            rgb_list([a.get("color")], 1)
            if not isinstance(indices, list) or not 1 <= len(indices) <= N or any(type(i) is not int or not 0 <= i < N for i in indices):
                raise ValueError("Invalid bulb indices.")
            value = {}
            for k in targets:
                if not capability(k)["razer"] or any(i >= capability(k)["bulbs"] for i in indices):
                    raise ValueError("Per-bulb paint needs a supported light and valid bulb indices.")
                b = [[int(v) for v in c] for c in render(looks[k], time.time(), capability(k)["bulbs"])]
                for i in indices:
                    b[i] = a["color"]
                value[k] = {"effect": "paint", "bulbs": b}
                histories[k] = (histories[k] + [looks[k]])[-HISTORY:]
                looks[k] = dict(looks[k], **value[k])
        elif kind == "schedule":
            value = a.get("schedule")
            if not isinstance(value, dict) or set(value) - {"add", "edit", "delete", "use"}:
                raise ValueError("Invalid schedule.")
            if "use" in value and type(value["use"]) is not bool:
                raise ValueError("Schedule use must be true or false.")
            change_timers(scheds, targets, value.get("add", []), value.get("edit", []), value.get("delete", []), saved=saved, location_set=location_set)
            for k in targets:
                scheds[k]["use"] = value.get("use", scheds[k]["use"])
                changed_schedules.add(k)
        elif kind == "undo":
            if any(not histories[k] for k in targets):
                raise ValueError("Nothing to undo.")
            for k in targets:
                previous = histories[k].pop()
                light_settings(k, previous)
                looks[k] = previous
        if kind in ("save_theme", "rename_theme", "delete_theme", "rename_light"):
            name = a.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > (30 if kind == "rename_light" else 40):
                raise ValueError("Invalid name.")
            name = " ".join(name.split())
            settings = value
            value = name
            if kind == "save_theme":
                if "settings" not in a and any(looks[k] != looks[targets[0]] for k in targets):
                    raise ValueError("Pick one light to save its look.")
                saved[name] = {k: v for k, v in (settings if "settings" in a else looks[targets[0]]).items() if k != "on"}
                value = (name, saved[name])
            elif kind in ("rename_theme", "delete_theme"):
                if name not in saved:
                    raise ValueError("That theme does not exist.")
                if kind == "rename_theme":
                    new = a.get("new")
                    if not isinstance(new, str) or not new.strip() or len(new) > 40:
                        raise ValueError("Invalid theme name.")
                    new = " ".join(new.split())
                    if new != name and new in saved:
                        raise ValueError("That theme name already exists.")
                    saved[new] = saved.pop(name)
                    for k, sc in scheds.items():
                        if any(t.get("theme") == name for t in sc["timers"]):
                            sc["timers"] = [dict(t, theme=new) if t.get("theme") == name else t for t in sc["timers"]]
                            changed_schedules.add(k)
                    value = (name, new)
                else:
                    del saved[name]
            elif len(targets) != 1:
                raise ValueError("Pick one light to rename.")
            if len(saved) > MAX_THEMES:
                raise ValueError("Delete a theme first.")
        plan.append((kind, targets, value))
    changed = False
    for kind, targets, value in plan:
        if kind in ("set", "apply_theme", "paint", "undo"):
            for k in targets:
                if kind == "undo":
                    undo(k)
                else:
                    changed = push(k, dict(value[k] if kind == "paint" else value)) or changed
        elif kind == "save_theme":
            save_theme(*value)
        elif kind == "rename_theme":
            themes[value[1]] = themes.pop(value[0])
        elif kind == "delete_theme":
            del themes[value]
        elif kind == "rename_light":
            lights[targets[0]]["name"] = value
        elif kind == "location":
            location = value
    if plan:
        with lock:
            schedule.update({k: scheds[k] for k in changed_schedules})
            save()
    return answer["say"][:300], changed and any(st[k]["history"] != h for k, h in before_histories.items())


PREVIEWS = None
PHONE = f"http://localhost:{PORT}"
PHONE_IP = PHONE
VERSION = dt.datetime.fromtimestamp((HERE / "server.py").stat().st_mtime, dt.timezone.utc).strftime("%Y-%m-%d")

def phone_address(ip):
    name = socket.gethostname().split(".")[0] + ".local"
    try:
        socket.gethostbyname(name)
    except OSError:
        return f"http://{ip}:{PORT}"
    return f"http://{name}:{PORT}"


class Server(ThreadingHTTPServer):
    server_bind = TCPServer.server_bind  # skips HTTPServer's reverse-DNS lookup, which can stall startup ~30 s


class H(BaseHTTPRequestHandler):
    def reply(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(data)

    def allowed(self):
        try:
            a = ipaddress.ip_address(self.client_address[0])
        except ValueError:
            return False
        return a.is_private or a.is_loopback  # home Wi-Fi and this computer only

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {"error": "home Wi-Fi only"})
        if self.path == "/api/govee":
            return self.reply(200, {"connected": bool(govee_key), "devices": [
                {"id": d["device"], "name": str(d.get("deviceName") or "")[:MAX_NAME]} for d in govee_devices]})
        if self.path == "/api/schedule":
            now = dt.datetime.now()
            ss = sunset(now.date())
            out = {}
            for k, sc in schedule.items():
                n = next_run(sc["timers"], now)
                out[k] = dict(sc, next=n and {"at": n[0].isoformat(timespec="minutes"), "id": n[1]["id"]})
            return self.reply(200, out | {"sunset": ss.strftime("%H:%M") if ss else "", "location": location,
                "outside": {k: {"events": outside.get(k, []), "warnings": repeated_changes(outside.get(k, []))} for k in lights}})
        if self.path == "/api/chat":
            return self.reply(200, chat_view())
        if self.path == "/api/themes":
            return self.reply(200, theme_list())
        if self.path == "/api/state":
            saved = list(lights.items())
            allowed = lan_allowed()
            return self.reply(200, {"lights": {k: {x: v for x, v in st[k].items() if x != "history"} | {"undo": len(st[k]["history"]), "short": short(k), "paused": k in paused} | d | {"capabilities": capability(k)}
                                               for k, d in saved if k in st},
                                    "found": [{"id": k, "sku": d["sku"], "short": short(k)} for k, d in found.items() if k not in lights],
                                    "n": N, "phone": PHONE, "phone_ip": PHONE_IP, "version": VERSION, "claude": CLAUDE, "ask": ask_on, "update": dict(update.settings(), version=APP_VERSION),
                                    "lan": {"asked": lan_open(), "allowed": allowed, "fallback": allowed is False and fallback_app()}})
        if self.path == "/api/previews":
            return self.reply(200, PREVIEWS)
        if self.path.split("?")[0] in ("/", "/index.html"):
            return self.reply(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if self.path in ("/manifest.json", "/docs/icon.svg"):
            return self.reply(200, (HERE / self.path[1:]).read_bytes(),
                              "application/manifest+json" if self.path.endswith(".json") else "image/svg+xml")
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        global govee_key, govee_devices, ask_on, lan_asked
        if not self.allowed():
            return self.reply(403, {"error": "home Wi-Fi only"})
        # JSON-only blocks other websites from posting here through the user's browser (no CORS preflight answer)
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self.reply(415, {"error": "send JSON"})
        try:
            size = int(self.headers.get("Content-Length", 0))
        except ValueError:
            return self.reply(400, {"error": "bad length"})
        if not 0 <= size <= MAX_BODY:
            return self.reply(413, {"error": "too big"})
        try:
            body = json.loads(self.rfile.read(size) or "{}")
            if not isinstance(body, dict):
                raise ValueError("send a JSON object")
            if self.path == "/api/govee/refresh":
                return self.reply(200, import_devices(body.get("key", govee_key)))
            if self.path == "/api/govee/disconnect":
                disconnect_govee()
                return self.reply(200, {"ok": True})
            if self.path == "/api/update":
                update.set_enabled(body.get("enabled"))
                return self.reply(200, {"ok": True})
            if self.path == "/api/govee/scenes":
                return self.reply(200, imported_scenes(body.get("light")))
            if self.path == "/api/effect-previews":  # effect clips in the colors the user picked
                p = clean({k: v for k, v in body.items() if k in ("colors", "speed", "amount")})
                return self.reply(200, {e: clip(dict(p, effect=e)) for e in EFFECTS if e not in ("paint", "custom")})
            if self.path == "/api/network":  # the user read why and tapped Continue, so macOS may ask now
                if not lan_asked:
                    lan_asked = True
                    with lock:
                        save()
                    threading.Thread(target=wake, daemon=True).start()  # its scan is the first LAN packet: the prompt
                return self.reply(200, {"ok": True})
            if self.path == "/api/network/settings":
                if sys.platform == "darwin":
                    subprocess.run(["open", "x-apple.systempreferences:com.apple.preference.security?Privacy_LocalNetwork"], check=False)
                return self.reply(200, {"ok": True})
            if self.path == "/api/scan":
                discover()
                return self.reply(200, {"ok": True, "found": len(found)})
            if self.path == "/api/lights":
                save_lights(body.get("lights"))
                return self.reply(200, {"ok": True})
            if self.path == "/api/light/flash":
                return self.reply(200, {"ok": True, "flashed": flash(str(body.get("id", "")))})
            if self.path == "/api/light/forget":
                forget_light(str(body.get("id", "")))
                return self.reply(200, {"ok": True})
            if self.path == "/api/location":
                global location
                location = clean_location(body.get("lat"), body.get("lon"))
                with lock:
                    save()
                return self.reply(200, {"ok": True})
            if self.path == "/api/theme":
                with lock:
                    selected = body.get("light")
                    light = selected if selected in st else next(iter(st), None)
                    if not body.get("settings") and not light:
                        raise ValueError("Set up a light first.")
                    if selected == "all" and not body.get("settings") and any(look(s) != look(st[light]) for s in st.values()):
                        raise ValueError("The lights have different looks. Pick one light to save its theme.")
                    save_theme(body.get("name", ""), body.get("settings") or look(st[light]))
                return self.reply(200, {"ok": True})
            if self.path == "/api/theme/delete":
                themes.pop(str(body.get("name", "")), None)
                with lock:
                    save()
                return self.reply(200, {"ok": True})
            if self.path == "/api/chat/clear":
                chat["turns"] = []
                return self.reply(200, {"ok": True})
            if self.path == "/api/ask/use":
                ask_on = body.get("use") is True
                with lock:
                    save()
                return self.reply(200, {"ok": True})
            if self.path == "/api/ask":
                if not ask_on:
                    raise ValueError("Ask Claude is off. Turn it on at the bottom of the page.")
                keys = ask_keys(body.get("light"))
                text = str(body.get("text", ""))
                answer = ask_claude(keys, text)
                say, changed = apply_answer(answer, keys)
                for k in keys:
                    if k in st:
                        st[k]["said"] = say
                if not any(a.get("type") == "clear_chat" for a in answer["actions"]):
                    chat["turns"] = (chat["turns"] + [[text[:500], say, changed]])[-CHAT_TURNS:]
                    chat["at"] = time.time()
                return self.reply(200, {"say": say, "changed": changed} | chat_view())
            keys = list(lights) if body.get("light") == "all" else [body.get("light")]
            if not keys or any(k not in st for k in keys):
                return self.reply(400, {"error": "unknown light" if keys else "Set up a light first."})
            if self.path == "/api/set":
                settings = dict(body.get("settings", {}))
                changes = {k: light_settings(k, settings) for k in keys}
                for k, p in changes.items():
                    push(k, p)
                return self.reply(200, {"ok": True})
            if self.path == "/api/undo":
                with lock:
                    if any(not st[k]["history"] for k in keys):
                        raise ValueError("Nothing to undo.")
                    for k in keys:
                        light_settings(k, st[k]["history"][-1])
                    for k in keys:
                        undo(k)
                return self.reply(200, {"ok": True})
            if self.path == "/api/schedule":  # {"light", "add": [timer], "edit": [{"id", ...changes}], "delete": [id]}
                with lock:
                    scheds = {k: dict(schedule[k], timers=list(schedule[k]["timers"])) for k in keys}
                    change_timers(scheds, keys, body.get("add", []), body.get("edit", []), body.get("delete", []))
                    schedule.update(scheds)
                    save()
                return self.reply(200, {"ok": True})
            if self.path == "/api/schedule/use":
                with lock:
                    for k in keys:
                        schedule[k]["use"] = body.get("use") is True
                    save()
                return self.reply(200, {"ok": True})
            if self.path == "/api/reconnect":
                seen = discover()
                for k in keys:
                    reconnect(k)
                lost = [lights[k]["name"] for k in keys if k not in seen]
                if lost:
                    raise ValueError(f"Could not find {' and '.join(lost)}. Check each light has power and LAN Control is on in the Govee Home app.")
                return self.reply(200, {"ok": True})
            self.reply(404, {"error": "unknown endpoint"})
        except subprocess.TimeoutExpired:
            self.reply(504, {"error": "Claude took too long. Try again."})
        except (ValueError, TypeError, KeyError) as e:
            self.reply(400, {"error": str(e)})
        except Exception as e:  # never leave the phone hanging without an answer
            blocked = sys.platform == "darwin" and getattr(e, "errno", None) == errno.EHOSTUNREACH
            self.reply(500, {"error": LAN_BLOCKED if blocked else f"Server error: {e}"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    f = razer_frame([[1, 2, 3]])
    assert f == [0xBB, 0, 5, 0xB0, 0, 1, 1, 2, 3], f
    assert 3 * N + 2 <= 255
    for e in EFFECTS:
        for t in (0.0, 1.7, 123.4):
            fr = render(dict(DEFAULT, effect=e, colors=[GOLD, SILVER, [9, 9, 9]]), t)
            assert len(fr) == N and all(len(c) == 3 and all(-0.001 <= v <= 255.001 for v in c) for c in fr), e
    assert clean({"brightness": 500, "speed": -1, "junk": 1}) == {"brightness": 100, "speed": 0.05}
    utc = lambda d: d.astimezone(dt.timezone.utc)  # naive local -> UTC, so the check passes in any time zone
    london = [51.5, -0.13]
    ss, ws = utc(sunset(dt.date(2026, 6, 21), london)), utc(sunset(dt.date(2026, 12, 21), london))
    assert (ss.hour, ws.hour) == (20, 15), (ss, ws)  # London: ~9:21 PM BST in June, ~3:53 PM in December
    assert sunset(dt.date(2026, 6, 21), [89.0, 0.0])  # midnight sun: clamped, no crash
    assert sunset(dt.date.today()) is None and when("sunset", dt.date.today()) is None  # no location yet
    try:
        clean_time("sunset"); assert False, "sunset needs a location"
    except ValueError:
        pass
    location = london
    assert clean_time("Sunset + 30") == "sunset+30" and clean_time("sunset-0") == "sunset" and clean_time("7:05") == "07:05"
    for bad in ("sunset+121", "sunset-x", "25:00", "", "noon"):
        try:
            clean_time(bad); assert False, bad
        except ValueError:
            pass
    sat_sun = [{"id": "on", "time": "sunset-15", "action": "on", "days": [5, 6], "enabled": True},
               {"id": "off", "time": "00:00", "action": "off", "days": [5, 6], "enabled": True}]
    assert migrate({"on": "sunset-15", "off": "00:00", "days": [5, 6]}) == {"use": True, "timers": sat_sun}
    assert migrate({"use": False, "timers": sat_sun + [{"time": "junk", "action": "on"}]}) == {"use": False, "timers": sat_sun}
    assert clean_location("37.123456", -122.98765) == [37.12, -122.99]
    for bad in ((91, 0), (0, 181), ("nan", 0), ("x", 0)):
        try:
            clean_location(*bad); assert False, bad
        except ValueError:
            pass
    location = None
    assert clean_days(None) == list(range(7)) and clean_days([4, 0, 0]) == [0, 4]
    for bad in ([], [7], [-1], ["1"], [True], "0,1", 3):
        try:
            clean_days(bad); assert False, bad
        except (ValueError, TypeError):
            pass
    fri = dt.datetime(2026, 11, 6)
    assert fri.weekday() == 4
    at = lambda d, hm: fri + dt.timedelta(days=d, hours=int(hm[:2]), minutes=int(hm[3:]))
    on, off, late = ({"id": a + h, "time": h, "action": a, "days": [0, 1, 2, 3, 4], "enabled": True}
                     for h, a in (("18:00", "on"), ("00:00", "off"), ("23:00", "off")))
    wk = [on, off]
    assert due(wk, on, at(0, "18:00")) and not due(wk, on, at(1, "18:00"))  # Friday yes, Saturday no
    assert due(wk, off, at(1, "00:00"))  # Saturday 12 AM ends Friday's evening, so it still turns off
    assert not due(wk, off, at(3, "00:00")) and due(wk, off, at(4, "00:00"))  # Mon 12 AM ends Sunday (no); Tue ends Mon
    assert due(wk + [late], late, at(0, "23:00")) and not due(wk + [late], late, at(1, "23:00"))  # after the on: its own day
    assert not due([off], off, at(1, "00:00"))  # no on timer at all: the off is just a time on its days
    assert due([dict(on, enabled=False), off], off, at(1, "00:00"))  # pausing the on timer doesn't move the off's nights
    assert next_run(wk, at(0, "12:00")) == (at(0, "18:00"), on)
    assert next_run(wk, at(0, "18:00")) == (at(1, "00:00"), off)  # tonight's off, Saturday 12 AM
    assert next_run(wk, at(1, "00:00")) == (at(3, "18:00"), on)  # weekend skipped: Monday 6 PM
    assert next_run([dict(on, enabled=False)], fri) is None
    for n, p in BUILTIN.items():
        assert len(render(dict(DEFAULT, **p), 5.0)) == N, n
    walk = dict(DEFAULT, effect="custom", speed=0.85, animation={"frames": [{"pattern": [[255, 0, 0], GOLD, SILVER], "duration": 0.2, "fade": 0}], "shift": 1, "motion": "forward", "block": 1})
    steps = [custom_frame(walk["animation"], k / FPS + (k % 3) * 0.01, 3, 0.85)[0] for k in range(30)]  # late sends jitter
    runs = [len(list(g)) for _, g in __import__("itertools").groupby(steps)][1:-1]
    assert len(set(runs)) == 1, runs  # every step of an animation lasts equally long
    chat["turns"] = [["hi", "hello", False]]  # chat["at"] is 0, so it counts as long expired
    assert chat_view()["turns"] == []
    # discovery parsing and setup, against a throwaway state file so the real one is never touched
    ok = json.dumps({"msg": {"cmd": "scan", "data": {"ip": "192.168.1.9", "device": "12:34:56:78:9A:BC:DE:F0", "sku": "H805D"}}})
    assert parse_scan(ok) == ("12:34:56:78:9A:BC:DE:F0", "192.168.1.9", "H805D") and short("12:34:56:78:9A:BC:DE:F0") == "DEF0"
    for bad in ("junk", "[]", '{"msg": {"data": {"ip": "8.8.8.8", "device": "x"}}}', '{"msg": {"data": {"device": "x"}}}'):
        assert parse_scan(bad) is None, bad
    real, SAVE, was_dry, DRY = SAVE, Path(tempfile.mkdtemp()) / "state.json", DRY, True  # setup turns a new light on
    found = {"12:34:56:78:9A:BC:DE:F0": {"ip": "192.168.1.9", "sku": "H805D"}}
    for bad in ([], [{"id": "nope", "name": "X"}], [{"id": "12:34:56:78:9A:BC:DE:F0", "name": "  "}], "x"):
        try:
            save_lights(bad); assert False, bad
        except ValueError:
            pass
    save_lights([{"id": "12:34:56:78:9A:BC:DE:F0", "name": "  Porch   rail "}])
    assert lights["12:34:56:78:9A:BC:DE:F0"] == {"name": "Porch rail", "sku": "H805D", "ip": "192.168.1.9"} and st and schedule
    try:
        flash("nope"); assert False, "flash must refuse a light the scan did not find"
    except ValueError:
        pass
    blink = threading.Thread(target=flash, args=("12:34:56:78:9A:BC:DE:F0", 0.1)); blink.start(); time.sleep(0.05)
    assert flash("12:34:56:78:9A:BC:DE:F0") is False  # a second tap while it blinks does nothing
    blink.join(); assert not flashing and flash("12:34:56:78:9A:BC:DE:F0", 0) is True
    data = json.loads(SAVE.read_text())
    P = "12:34:56:78:9A:BC:DE:F0"
    data["_schedule"] = {P: {"on": "18:00", "off": None}}  # saved before days, the switch and timers existed
    SAVE.write_text(json.dumps(data))
    lights.clear(); st.clear(); schedule.clear(); load()
    assert lights[P]["name"] == "Porch rail" and st[P]["on"]
    assert schedule[P] == {"use": True, "timers": [{"id": "on", "time": "18:00", "action": "on", "days": list(range(7)), "enabled": True}]}
    s = {P: dict(schedule[P])}
    change_timers(s, [P], add=[{"time": "23:00", "action": "off", "days": [4], "theme": "Fireplace"}], edit=[{"id": "on", "enabled": False}])
    assert [t["enabled"] for t in s[P]["timers"]] == [False, True] and "theme" not in s[P]["timers"][1]  # an off timer has no look
    assert schedule[P]["timers"][0]["enabled"]  # only the copy changed
    change_timers(s, [P], delete=["on"])
    assert [t["time"] for t in s[P]["timers"]] == ["23:00"]
    for bad in (dict(add=[{"time": "18:00", "action": "on"}] * 10), dict(edit=[{"id": "nope"}]), dict(delete=["nope"]), dict(add="x"),
                dict(add=[{"time": "18:00", "action": "on", "theme": "No such theme"}]), dict(add=[{"time": "18:00", "action": "dim"}]),
                dict(add=[{"time": "18:00", "action": "on", "enabled": "yes"}]), dict(add=[{"time": "18:00", "action": "on", "code": 1}])):
        try:
            change_timers({P: dict(s[P])}, [P], **bad); assert False, bad
        except ValueError:
            pass
    assert len(s[P]["timers"]) == 1
    sent, real_send = [], send
    send = lambda k, cmd, data: sent.append((k, cmd, data))
    st[P]["on"] = False
    six = dt.datetime.combine(dt.date.today(), dt.time(18))
    schedule[P]["use"] = False
    schedule_tick(six - dt.timedelta(minutes=1), six)
    assert sent == [] and not st[P]["on"]  # switched off: nothing sent at the on time
    schedule[P]["use"] = True
    schedule_tick(six - dt.timedelta(minutes=1), six)
    assert (P, "turn", {"value": 1}) in sent and st[P]["on"]
    schedule[P]["timers"] = [clean_timer({"time": "18:00", "action": "on", "theme": "Fireplace", "enabled": False})]
    sent.clear(); schedule_tick(six - dt.timedelta(minutes=1), six)
    assert sent == []  # a disabled timer does nothing
    schedule[P]["timers"][0]["enabled"] = True
    schedule_tick(six - dt.timedelta(minutes=1), six)
    assert st[P]["effect"] == "fire" and st[P]["on"]  # an on timer with a theme shows that look
    send = real_send
    schedule[P]["use"] = False
    save(); schedule.clear(); lights.clear(); st.clear(); load()
    assert schedule[P]["use"] is False and schedule[P]["timers"][0]["theme"] == "Fireplace"  # off survives a restart, timers kept
    forget_light("12:34:56:78:9A:BC:DE:F0")
    assert not lights and not st and not schedule
    # Public-schema samples only: these checks never call a network transport.
    sample = json.loads((HERE / "samples/govee.json").read_text())
    devices = parse_devices(sample["devices"])
    assert devices[0]["deviceName"] == "Reading lights" and len(devices) == 2
    catalog = parse_scenes(devices[0]["capabilities"])
    assert catalog[1]["instance"] == "snapshot" and catalog[1]["value"] == 0
    assert parse_scenes(sample["scenes"]["payload"]["capabilities"])[0]["value"] == {"id": 3867, "paramId": 4294}
    assert parse_scenes(sample["diy-scenes"]["payload"]["capabilities"])[0]["instance"] == "diyScene"
    assert parse_scenes([]) == []
    for bad in ({"code": 401}, {"code": 403}, {"code": 429}, {"code": 500}, [], {"code": 200, "data": {}}):
        try:
            parse_devices(bad); assert False, bad
        except ValueError as e:
            assert "Govee" in str(e)
    for bad in (None, {}, [{"instance": "snapshot", "parameters": {"options": "bad"}}]):
        try:
            parse_scenes(bad); assert False, bad
        except ValueError:
            pass
    try:
        govee_read("sample-not-a-key", "device/control"); assert False, "mutation must be rejected before transport"
    except ValueError:
        pass
    assert parse_status(json.dumps(sample["status"])) is True
    for bad in ("junk", "[]", '{"msg":{"cmd":"scan","data":{"onOff":1}}}', '{"msg":{"cmd":"devStatus","data":{"onOff":"1"}}}'):
        assert parse_status(bad) is None
    events = sample["outside"]
    assert repeated_changes(events) == [{"on": True, "minute": 1080, "days": 2}]
    assert not repeated_changes(events[:1] * 2)  # one date never counts twice
    assert not repeated_changes([events[0], dict(events[1], on=False)])
    assert not repeated_changes([events[0], dict(events[1], at="2026-11-06T18:04:00")])
    assert len(repeated_changes(events + [dict(e, on=False) for e in events])) == 2
    assert repeated_changes([dict(events[0], at="2026-11-05T23:59:00"), dict(events[1], at="2026-11-06T00:01:00")])
    dev = devices[0]["device"]
    add_light(dev, "Sample light", "H805D", "192.168.0.20")
    start = dt.datetime(2026, 11, 5, 17, 59, 55)
    observe_power(dev, False, start, 100)
    assert not outside.get(dev) and dev in paused  # baseline respects power, but invents no event
    observe_power(dev, True, start + dt.timedelta(seconds=5), 105)
    assert len(outside[dev]) == 1 and st[dev]["on"] and dev in paused
    command_at[dev] = 106
    observe_power(dev, False, start + dt.timedelta(seconds=10), 110)
    assert len(outside[dev]) == 1  # app commands do not count
    observe_power(dev, True, start + dt.timedelta(minutes=2), 225)
    assert len(outside[dev]) == 1  # long observation gap has no exact time
    govee_key = "sample-not-a-key"
    save()
    assert os.name == "nt" or SAVE.stat().st_mode & 0o777 == 0o600  # Windows has no POSIX modes; the profile folder is per-user
    outside.clear(); paused.clear(); govee_key = ""; load()
    assert govee_key == "sample-not-a-key" and len(outside[dev]) == 1 and dev in paused
    ask_on = False; save(); ask_on = True; load()
    assert ask_on is False  # "Not now" survives a restart
    data = json.loads(SAVE.read_text()); del data["_ask"]; SAVE.write_text(json.dumps(data)); load()
    assert ask_on is True  # a save from before the choice keeps the chat
    reconnect(dev)  # explicit user action, still DRY inside these assertions
    assert dev not in paused and dev not in json.loads(SAVE.read_text())["_paused"]
    forget_light(dev)
    assert dev not in outside and dev not in paused
    govee_key = ""
    print("Self-checks passed (saved samples; no network).")
    SAVE, found, DRY = real, {}, was_dry
    PREVIEWS = previews()
    load()
    if lan_open():
        wake()
    me = "localhost"
    try:  # which address the phone should use; a UDP connect sends no packet
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.connect(("8.8.8.8", 80)); me = u.getsockname()[0]; u.close()
    except OSError:
        pass
    PHONE_IP = f"http://{me}:{PORT}"
    PHONE = phone_address(me)
    threading.Thread(target=streamer, daemon=True).start()
    threading.Thread(target=rediscover_loop, daemon=True).start()
    threading.Thread(target=schedule_loop, daemon=True).start()
    threading.Thread(target=status_loop, daemon=True).start()
    print(f"Govee app: http://localhost:{PORT}   phone (same Wi-Fi): {PHONE}" + ("   DRY RUN, lights untouched" if DRY else "") + "   Ctrl+C to stop.")
    Server(("0.0.0.0", PORT), H).serve_forever()
