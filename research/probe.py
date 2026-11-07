# Debug probe: read state, set white 100%, read state. usage: probe.py <name>
import asyncio, sys, logging
logging.basicConfig(level=logging.DEBUG, format="%(name)s %(message)s")
for n in ("bleak",): logging.getLogger(n).setLevel(logging.WARNING)
from bleak import BleakScanner
from govee_ble_local import create_device
async def main(name):
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    hit = [(d, a) for d, a in found.values() if (a.local_name or d.name) == name]
    if not hit: sys.exit(f"not found: {name}")
    d, a = hit[0]; dev = create_device(d, "H805D", a)
    try:
        s = await dev.update(); print("BEFORE", s, flush=True)
        await dev.set_brightness(100); await dev.set_rgb((255, 255, 255)); print("SENT white 100", flush=True)
        await asyncio.sleep(2); s = await dev.update(); print("AFTER", s, flush=True)
    finally: await dev.stop()
asyncio.run(main(sys.argv[1]))
