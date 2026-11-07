# Govee BLE test: connect, ask power state, blink off 2s then on.
import asyncio, sys
from bleak import BleakClient, BleakScanner
W = "00010203-0405-0607-0809-0a0b0c0d2b11"; N = "00010203-0405-0607-0809-0a0b0c0d2b10"
def pkt(*b):
    b = list(b) + [0] * (19 - len(b)); x = 0
    for v in b: x ^= v
    return bytes(b + [x])
assert pkt(0x33, 1, 1)[-1] == 0x33 ^ 1 ^ 1
async def main(name):
    d = await BleakScanner.find_device_by_filter(lambda d, a: (a.local_name or "") == name, timeout=20)
    if not d: sys.exit(f"not found: {name}")
    async with BleakClient(d, timeout=20) as c:
        print("connected", name)
        for s in c.services:
            print(" svc", s.uuid, [ (ch.uuid[-4:], ch.properties) for ch in s.characteristics])
        await c.start_notify(N, lambda _, data: print("  reply", data.hex()))
        await c.write_gatt_char(W, pkt(0xaa, 0x01), response=False); await asyncio.sleep(1)
        await c.write_gatt_char(W, pkt(0x33, 0x01, 0x00), response=False); print("off"); await asyncio.sleep(2)
        await c.write_gatt_char(W, pkt(0x33, 0x01, 0x01), response=False); print("on"); await asyncio.sleep(1)
asyncio.run(main(sys.argv[1]))
