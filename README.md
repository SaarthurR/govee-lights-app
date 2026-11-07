# Lights

Moving, bulb-by-bulb effects and smarter timers for Govee light strips, from any phone or computer on your Wi-Fi. No cloud, no account.

<p>
  <img src="docs/control.png" width="250" alt="Control page: live strip of bulbs, power, brightness and colors">
  <img src="docs/schedule.png" width="250" alt="Schedule page: a list of timers with the next one shown on top">
  <img src="docs/timer.png" width="250" alt="Add timer sheet: on or off, at a time or at sunset, repeat days">
</p>

## Features

- **Live strip.** See every bulb, tap bulbs to paint them, set power and brightness.
- **Effects in your colors.** Wave, shimmer, twinkle, comet, fire, aurora, chase, breathe, rainbow and more, with speed and amount.
- **Presets and your own themes,** with Undo.
- **Timers like the Govee app, but better.** Up to 10 per light. On or off at a time or at sunset (up to 2 hours before or after), on the days you pick. An On timer can bring up a theme. The page says what happens next and warns you when two timers clash.
- **Setup by Wi-Fi scan.** Tap **Flash** to blink a light and see which one it is.
- **Phone first.** Scan a QR code to open it on your phone, then add it to your home screen.
- **Optional:** an Ask Claude chat ("make it a slow police light", "turn off at midnight on weekends"), and reading your scene names from a Govee account.

## What you need

- A computer that stays on, with Python 3.10 or newer. Nothing else to install. Tested on macOS.
- A Govee light with **LAN Control** on. See Supported lights below; Wi-Fi alone does not mean LAN Control is available.
- Optional: the [Claude Code](https://docs.anthropic.com/en/docs/claude-code) `claude` command, signed in, for Ask Claude. Without it the chat card stays hidden. The welcome screen asks before turning it on, and you can change it at the bottom of the page.

## Supported lights

| Level | Model | What works |
| --- | --- | --- |
| Tested | H805D | Existing 84-color stream, effects, paint, power, brightness and schedules. Physical LED mapping is unmeasured. |
| Should work | H619A | 20 streaming color slots, effects and paint, from [OpenRGB's model table](https://github.com/CalcProgrammer1/OpenRGB/blob/master/Controllers/GoveeController/RGBController_Govee.cpp#L38-L58) and [Hypercolor](https://hyperb1iss.github.io/hypercolor/hardware/govee/); untested on this app's hardware. |
| Basic mode | H6008 and other discovered models | On/off, brightness, one solid color and schedules through the documented LAN API. No per-bulb stream. LAN Control must be available and enabled. |

The [official LAN guide](https://app-h5.govee.com/user-manual/wlan-guide) lists eligible models. LAN replies do not give a bulb count; the app only enables streaming for its small verified model table. Basic mode uses a theme's first color when a scheduled theme runs.

Please open an issue with your model number and whether it worked.

## Start

On a Mac or Linux, paste this into Terminal:

```sh
curl -fsSL https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.sh | sh
```

On Windows, paste this into PowerShell:

```powershell
irm https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.ps1 | iex
```

It checks for Python 3.10 or newer, downloads this repo into `govee-lights` in your home folder (no git needed), starts the app and opens it in your browser. It starts at login and restarts after a crash (LaunchAgent on macOS, systemd user service on Linux, Scheduled Task on Windows; no admin). Run the command again to update; your lights and themes are kept. Linux without a working systemd user session runs in the foreground. Read it first: [install.sh](install.sh), [install.ps1](install.ps1).

### Uninstall

Mac / Linux (keeps `~/govee-lights/state.json`; add `--purge` to remove it):

```sh
curl -fsSL https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.sh | sh -s -- --uninstall
```

Windows (keeps state; add `-Purge` to remove it):

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.ps1))) -Uninstall
```

Or clone the repo, then in its folder run `python3 server.py` (on Windows: `py server.py`) and open <http://localhost:8765>.

1. In the Govee Home app, open your light, tap the settings gear and turn on **LAN Control**. Do this for each light.
2. The welcome screen finds the lights on your Wi-Fi. Click **Flash** next to one to make it blink, then give each one a name.
3. **Open it on your phone:** on the computer, click **Open on phone** and scan the code with your phone camera, then **Share → Add to Home Screen**. The phone must be on the same Wi-Fi. The link uses the computer’s `.local` name when it resolves; an IP fallback appears below the code.

To rename or remove a light later, use **Manage lights** at the bottom of the page.

## Troubleshooting

**No lights found.** Check that LAN Control is on for each light, and that the light and the computer are on the same Wi-Fi (not a guest network). Some routers block the scan between devices; turn off "client isolation" or "AP isolation" if yours has it. If your computer has a firewall, allow incoming connections for Python. Then select **Scan again**.

**Lights stuck or not changing.** Use **Reconnect** at the bottom of the page. After a power cut a strip drops out of per-bulb mode, and Reconnect brings it back. Run only one copy of this app at a time, or the copies fight over the lights.

**A timer didn't run.** Timers only run while the computer is awake and `server.py` is running. To use sunset, add your location on the Schedule page.

**Lights turn on or off by themselves.** Timers made in the Govee Home app keep running, and Govee doesn't let other apps read them. Check Timer and Automation for that light in Govee Home. To use only Govee's timers for a light, turn off **Use this app's schedule** on the Schedule page; your timers here stay saved. When something else changes a light, this app stops streaming to it until you tap a look or **Reconnect**, and warns you if it keeps happening at the same time.

**The phone can't open the page.** Use the same Wi-Fi as the computer, and allow incoming connections on port 8765 in its firewall.

Use **Send feedback** at the bottom of the page to open a GitHub issue draft. It includes only the app date, OS, browser and model SKUs. A GitHub account is needed to submit it.

## How it works

- `server.py` talks to the lights with the Govee LAN API over UDP (scan on ports 4001 and 4002, commands on 4003). Supported streaming models use their table count (84 for H805D, 20 for H619A), 12 frames a second, in Govee's per-bulb `razer` mode. Other models use `turn`, `brightness` and `colorwc`. Protocol notes are in `FUNCTIONS.md`.
- `index.html` is the whole page, served by `server.py`. The server only answers devices on your home network.
- Everything is saved in `state.json` next to `server.py`: lights, last looks, themes, timers and location (rounded to about 1 km). Delete it to start over.
- An Off timer that comes before every On timer of that light belongs to the night before. For example, "Off at 12:00 AM, Sat and Sun" turns the light off after Saturday and Sunday evenings.
- The Govee account is optional and read-only. It reads light and scene names with an API key from Govee Home (Profile → Settings → Apply for API key). The key stays in `state.json` and is sent only to Govee.
- Ask Claude uses the Haiku model with a short prompt, so each ask stays cheap. Claude can only return a checked list of actions (looks, painting, timers, themes), never code.
- Try it without lights: `GOVEE_DRY=1 GOVEE_FAKE_LIGHTS=3 GOVEE_FAKE_SKUS=H805D,H619A,H6008 PORT=8768 python3 server.py` sends nothing and pretends to find three lights. `python3 -m unittest test_ask test_models` runs the offline tests.

## License

MIT, see [LICENSE](LICENSE).

Not affiliated with Govee. Govee is a trademark of its owner.
