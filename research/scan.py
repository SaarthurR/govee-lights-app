import asyncio
from bleak import BleakScanner
async def main():
    found = await BleakScanner.discover(timeout=12, return_adv=True)
    for addr, (d, adv) in sorted(found.items(), key=lambda x: -x[1][1].rssi):
        name = adv.local_name or d.name or ""
        mfr = {k: v.hex() for k, v in adv.manufacturer_data.items()}
        print(f"{adv.rssi:4} {addr} {name!r} mfr={mfr} svc={adv.service_uuids}")
asyncio.run(main())
