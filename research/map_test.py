"""H805D manual hardware checks. No radio activity without --run.

Offline: .venv/bin/python map_test.py --self-test
Hardware: .venv/bin/python map_test.py --run --case pieces
Findings are in FUNCTIONS.md under Bluetooth.
"""
import argparse
import asyncio
import statistics
import time
from functools import reduce

GOLD, SILVER = (255, 200, 80), (255, 255, 255)


def pattern(n, step):
    return list(range(step % 2, n, 2))


def self_test():
    from govee_ble_local import Capability, Scene, profile_for
    from govee_ble_local.scenes import scene_upload_frames
    from govee_ble_local.wire import build

    p = profile_for("H805D")
    assert Capability.SCENES not in p.capabilities and not p.scene_versions
    assert not p.segment_color_temp and p.gradual and p.segment_brightness
    assert scene_upload_frames(Scene("check", 1, "AA==", scene_type=2),
                               p.scene_dialect, p.scene_versions) is None
    assert pattern(5, 0) == [0, 2, 4] and pattern(5, 1) == [1, 3]
    frame = build.segment_rgb(1, *GOLD, p.color_scheme, 33)
    assert len(frame) == 20 and frame[:4] == bytes.fromhex("33051501")
    assert frame[-1] == reduce(int.__xor__, frame[:-1])
    assert build.ic_count_query().hex() == "aa400000000000000000000000000000000000ea"
    print("Offline checks passed; no Bluetooth scan, connect, or write.")


async def run(args):
    from bleak import BleakScanner
    from govee_ble_local import create_device

    found = await BleakScanner.discover(timeout=10, return_adv=True)
    hits = [(d, a) for d, a in found.values()
            if (a.local_name or d.name) == args.name]
    if len(hits) != 1:
        raise RuntimeError(f"Expected exactly one {args.name}; found {len(hits)}")
    d, a = hits[0]
    dev = create_device(d, "H805D", a)
    try:
        state = await dev.update()
        n = dev.segment_count
        if dev.segment_count_read_at is None or n < 2:
            raise RuntimeError("No live segment count; refusing a guessed mask")
        print(f"{args.name}: pieces={n}, firmware={state.firmware_version}", flush=True)
        # Set both global and piece sliders to 100% before displaying test colours.
        await dev.set_brightness(100)
        await dev.set_segment_brightness(range(n), 100)
        await dev.set_power(True)
        await dev.set_rgb(GOLD)
        if args.case == "basics":
            for kelvin in (2700, 6500):
                await dev.set_color_temp(kelvin)
                await asyncio.sleep(3)
            await dev.set_rgb(SILVER)
            await asyncio.sleep(3)
        elif args.case == "pieces":
            for i in range(n):
                if i:
                    await dev.set_segment_rgb([i - 1], GOLD)
                await dev.set_segment_rgb([i], SILVER)
                print(f"piece {i}: count physical bulbs that changed", flush=True)
                await asyncio.sleep(2)
        else:
            # shortcut: ACK timing cannot prove visible frame delivery; an observer gates each stage.
            for interval in (0.30, 0.20, 0.15, 0.10, 0.075, 0.05):
                answer = await asyncio.to_thread(
                    input, f"Enter to test {interval:.3f}s/command, q to stop: ")
                if answer.strip().lower() == "q":
                    break
                latencies = []
                started = time.monotonic()
                for step in range(40):
                    tick = time.monotonic()
                    await dev.set_segment_rgb(pattern(n, step),
                                              SILVER if (step // 2) % 2 else GOLD)
                    elapsed = time.monotonic() - tick
                    latencies.append(elapsed)
                    await asyncio.sleep(max(0, interval - elapsed))
                print(f"{40 / (time.monotonic() - started):.2f} completed commands/s; "
                      f"ACK median={statistics.median(latencies):.3f}s, "
                      f"max={max(latencies):.3f}s; record visible skips/stutter", flush=True)
                answer = await asyncio.to_thread(input, "Visible stutter/skips? [y/N]: ")
                if answer.strip().lower() not in ("", "n", "no"):
                    break
        await dev.set_rgb(GOLD)
        print("Finished at gold, global and segment brightness 100%.")
    finally:
        # Do not retry writes after an error; stop only releases the connection.
        await dev.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Govee_H805D_XXXX", help="Bluetooth name; XXXX = the 4 characters the Govee app shows")
    parser.add_argument("--case", choices=("basics", "pieces", "rate"), default="pieces")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="explicitly enable light-changing writes")
    mode.add_argument("--self-test", action="store_true", help="offline asserts only")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    elif args.run:
        asyncio.run(run(args))
    else:
        print(f"DRY RUN: {args.name}, {args.case}, brightness 100%. "
              "No radio activity. Add --run only when ready to change the light.")


if __name__ == "__main__":
    main()
