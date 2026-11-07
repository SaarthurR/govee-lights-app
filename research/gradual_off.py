# Turn off the light's own slow-fade ("gradual") over BLE. usage: gradual_off.py <ble name>
import asyncio, sys
from bleak import BleakScanner
from govee_ble_local import create_device
async def main(name):
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    hit = [(d, a) for d, a in found.values() if (a.local_name or d.name) == name]
    if not hit: sys.exit(f"not found: {name}")
    dev = create_device(hit[0][0], "H805D", hit[0][1])
    try:
        s = await dev.update(); print("gradual before:", getattr(s, "gradual", "?"), flush=True)
        await dev.set_gradual(False)
        s = await dev.update(); print("gradual after:", getattr(s, "gradual", "?"), flush=True)
    finally: await dev.stop()
asyncio.run(main(sys.argv[1]))
