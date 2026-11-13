# Protocol notes

How this app talks to Govee lights. Tested on two H805D strips.

## Wi-Fi (LAN API)

This is what the app uses. LAN Control must be on for each light in the Govee Home app. Every message is JSON over UDP.

| What | Send | Reply |
| --- | --- | --- |
| Find lights | `{"msg":{"cmd":"scan","data":{"account_topic":"reserve"}}}` to `239.255.255.250:4001` | To local port 4002: `device`, `ip`, `sku` |
| Power | `{"msg":{"cmd":"turn","data":{"value":1}}}` to `<light ip>:4003` (0 = off) | none |
| Brightness | `{"msg":{"cmd":"brightness","data":{"value":100}}}` (1 to 100) | none |
| Status | `{"msg":{"cmd":"devStatus","data":{}}}` | To port 4002: `onOff`, `brightness`, `color`, `colorTemInKelvin` |
| Per-bulb colors | `{"msg":{"cmd":"razer","data":{"pt":"<base64 frame>"}}}` | none |

A razer frame is `BB 00 <length> <payload>`, then one byte that XORs all the bytes before it. The whole thing is base64-encoded into `pt`.

- Turn per-bulb mode on: payload `B1 01`, so `bb 00 01 b1 01 0a`. Off: `B1 00`.
- Colors: payload `B0 00 <count> R G B R G B ...`. The `00` turns off blending between frames. The length is two bytes (`BB <hi> <lo>`), so a frame can hold more than 84 colors.
- The app sends 132 colors to an H805D (20 to an H619A) 12 times a second. The H805D has 132 bulbs. Sent 84 colors, it stretched them: the first 48 colors lit 2 bulbs each, the last 36 lit 1 each (tested Nov 7, 2026). Sent 132, every bulb is its own color.

What we learned on real strips:

- Turn per-bulb mode on once, after power on. Sending it again while frames are streaming blacks the strip out, so never re-send it on a timer.
- After a power cut the strip leaves per-bulb mode and ignores frames until it is turned on again. Reconnect in the app does this.
- The Govee Home app or a Govee timer can take a light over. `devStatus` shows the power change, but not whether per-bulb mode is still on or what caused it. The app pauses streaming to that light and never turns it back on by itself.
- Scan and status replies both arrive on port 4002. Two listeners on that port split the replies, so the app runs one at a time.
- A light's IP can change. The app matches lights by `device` id and scans again every 2 minutes.
- The H805D is missing from Govee's published LAN device list, but it works.

Tested Nov 11, 2026 on an H608B (Wi-Fi firmware 1.01.17), H612B (1.06.24), H16C0 (1.01.14), H61D3 (1.06.24), H610A (2.06.02) and H6056 (1.00.16):

- Slot counts: H608B 15, H612B 10, H16C0 7, H61D3 7. Each of these four was painted half red, half blue through the app and showed two colors. On the H16C0 slot 1 is the bottom. The H61D3 count is not its segment count in community tables: one lists 15, and sent 15 colors it showed one flat color (tested Sep 5, 2026).
- H610A: 24 slots, the count SignalRGB lists. Set to red and blue alternating one per slot (tested Nov 12, 2026), it showed 24 clean blocks.
- H6056: 12 slots, 6 on each bar. Sent 6 colors it stretched each over two segments, the way the H805D stretched 84; sent 24 it went dark. One slot lit at a time in a 12-color frame lit half the width that one slot of a 6-color frame did.
- The byte after `B0` can be `00` or `01` on the H608B, H612B, H16C0, H61D3 and H6056: both showed the painted pattern, not one flat color. Each was tried from a fresh per-bulb enable with one value only.
- Lights drop commands that arrive back to back. With color, power, brightness and per-bulb mode sent with no gap, per-bulb mode came on for 5 of 12 lights over three runs (H608B, H612B, H16C0, H61D3), and the lights that missed it showed their old solid color while frames streamed. With 0.12 s or 0.25 s between commands to one light it came on for 12 of 12. The app leaves 0.12 s.
- The gap does not make color and brightness certain. Five lights were added and then set to a new color at 35%, and `devStatus` was read from each: with no gap 1 of 5 matched; with 0.12 s, 3 of 5; with 0.25 s, 3 of 5 and then 5 of 5. Nothing is acknowledged, so `devStatus` is the only way to know a color or brightness command arrived.
- An H6056 answers the scan without an `ip` field. The address the reply came from is the light.
- A plain color command takes a light out of per-bulb mode: an H6056 streaming a pattern showed one solid color within 1.5 s of a `colorwc`, with no per-bulb-mode-off packet.
- `devStatus` never shows per-bulb colors or whether per-bulb mode is on. Check by eye or camera.
- Slot counts for 101 models, most of them unconfirmed, are in [models.json](../models.json).

Sources: [Govee LAN guide](https://app-h5.govee.com/user-manual/wlan-guide), [LAN protocol notes, razer section](https://github.com/damient/govee-toolkit/blob/main/docs/protocol/lan.md#23-per-segment-color--the-razer-raw-channel).

## Bluetooth

Bluetooth works through the `govee-ble-local` library, which handles the encryption (plain frames are ignored). On the H805D it reaches only 33 groups of 4 bulbs, and each command waits for an acknowledgement, so it is too coarse and slow for these effects. No supported way was found to upload a custom effect that runs on the light by itself: effects have to be streamed.

The app does not use Bluetooth.

## Govee cloud API

The optional Govee account feature calls three read-only endpoints under `https://openapi.api.govee.com/router/api/v1/`, with the key in a `Govee-API-Key` header: `user/devices`, `device/scenes` and `device/diy-scenes`. Scene values are opaque: numbers or `{"id", "paramId"}` objects. The server rejects any other endpoint, including `device/control`. Govee's API does not expose timers or schedules.

Sources: [device list](https://developer.govee.com/reference/get-you-devices), [scene queries](https://developer.govee.com/reference/get-light-scene).
