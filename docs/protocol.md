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
- Colors: payload `B0 00 <count> R G B R G B ...`. The `00` turns off blending between frames. The length is one byte, so a frame holds at most 84 colors (3 × 84 + 2 = 254).
- The app sends 84 colors to an H805D (20 to an H619A) 12 times a second. The H805D reports 132 LED chips; how it spreads 84 colors over them was not measured.

What we learned on real strips:

- Turn per-bulb mode on once, after power on. Sending it again while frames are streaming blacks the strip out, so never re-send it on a timer.
- After a power cut the strip leaves per-bulb mode and ignores frames until it is turned on again. Reconnect in the app does this.
- The Govee Home app or a Govee timer can take a light over. `devStatus` shows the power change, but not whether per-bulb mode is still on or what caused it. The app pauses streaming to that light and never turns it back on by itself.
- Scan and status replies both arrive on port 4002. Two listeners on that port split the replies, so the app runs one at a time.
- A light's IP can change. The app matches lights by `device` id and scans again every 2 minutes.
- The H805D is missing from Govee's published LAN device list, but it works.

Sources: [Govee LAN guide](https://app-h5.govee.com/user-manual/wlan-guide), [LAN protocol notes, razer section](https://github.com/damient/govee-toolkit/blob/main/docs/protocol/lan.md#23-per-segment-color--the-razer-raw-channel).

## Bluetooth

Bluetooth works through the `govee-ble-local` library, which handles the encryption (plain frames are ignored). On the H805D it reaches only 33 groups of 4 bulbs, and each command waits for an acknowledgement, so it is too coarse and slow for these effects. No supported way was found to upload a custom effect that runs on the light by itself: effects have to be streamed.

The app does not use Bluetooth.

## Govee cloud API

The optional Govee account feature calls three read-only endpoints under `https://openapi.api.govee.com/router/api/v1/`, with the key in a `Govee-API-Key` header: `user/devices`, `device/scenes` and `device/diy-scenes`. Scene values are opaque: numbers or `{"id", "paramId"}` objects. The server rejects any other endpoint, including `device/control`. Govee's API does not expose timers or schedules.

Sources: [device list](https://developer.govee.com/reference/get-you-devices), [scene queries](https://developer.govee.com/reference/get-light-scene).
