# Silver + gold shimmer, driven from the Mac. usage: shimmer.py <name> [seconds]
import asyncio, sys, random, time
from bleak import BleakScanner
from govee_ble_local import create_device
GOLD, DEEP_GOLD, SILVER = (255, 200, 80), (255, 170, 50), (255, 255, 255)  # LEDs run warm: lower green reads orange
async def main(name, secs):
    found = await BleakScanner.discover(timeout=10, return_adv=True)
    hit = [(d, a) for d, a in found.values() if (a.local_name or d.name) == name]
    if not hit: sys.exit(f"not found: {name}")
    d, a = hit[0]; dev = create_device(d, "H805D", a)
    try:
        s = await dev.update(); n = len(s.segments) or 15
        print(name, "segments:", n, flush=True)
        await dev.set_brightness(100)
        await dev.set_segment_rgb(range(n), GOLD)
        end = time.time() + secs; lit = []
        while time.time() < end:
            if lit: await dev.set_segment_rgb(lit, random.choice((GOLD, DEEP_GOLD)))  # sparkle fades back to gold
            lit = random.sample(range(n), max(1, n // 5))
            await dev.set_segment_rgb(lit, SILVER)
            await asyncio.sleep(0.15)
        await dev.set_segment_rgb(range(n), GOLD)
        print("done", flush=True)
    finally: await dev.stop()
asyncio.run(main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 30))
