# Test with govee-ble-local (does the Govee handshake). Full brightness 2s, off 2s, on.
# usage: python test_lib.py Govee_H805D_XXXX  (XXXX = the 4 characters the Govee app shows)
import asyncio, sys
from bleak import BleakScanner
from govee_ble_local import create_device
async def main(name):
    found = await BleakScanner.discover(timeout=12, return_adv=True)
    hit = [(d, a) for d, a in found.values() if (a.local_name or d.name) == name]
    if not hit: sys.exit(f"not found: {name}")
    d, a = hit[0]
    dev = create_device(d, "H805D", a)
    try:
        await dev.turn_on(); await dev.set_brightness(100); print(name, "on 100%", flush=True); await asyncio.sleep(2)
        await dev.turn_off(); print("off", flush=True); await asyncio.sleep(2)
        await dev.turn_on(); print("on", flush=True)
        s = await dev.update(); print("state: on=", s.is_on, "bright=", s.brightness, "rgb=", s.rgb_color)
    finally:
        await dev.stop()
asyncio.run(main(sys.argv[1]))
