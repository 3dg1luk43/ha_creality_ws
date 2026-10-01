# Security Policy

## Reporting a vulnerability

Do not open a public issue. Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/3dg1luk43/ha_creality_ws/security/advisories/new)).

Include:

- the affected version, and the printer model if it matters
- what an attacker can do, and from where (LAN, Home Assistant user, internet)
- steps to reproduce, and a fix if you have one
- whether you want credit in the advisory

This is a volunteer, single-maintainer project. Expect an acknowledgement within a few days. Fixes ship in the next release and are published as a GitHub security advisory. Please keep the details private until that release is out.

## Supported versions

Only the latest release. Fixes are not backported; update to get them.

## Scope

In scope: the integration under `custom_components/ha_creality_ws/` and the two Lovelace cards it serves.

Out of scope, report upstream: printer firmware, Creality's apps and cloud, Home Assistant core, go2rtc, and the companion apps. A firmware weakness that this integration makes worse is in scope.

## How the integration is exposed

Worth knowing before you report, and before you deploy:

- **The printer's WebSocket is unauthenticated.** Creality printers accept commands on `ws://<printer>:9999` from anything on the LAN, and offer no authentication for the integration to use. Whatever this integration can do, any device on your network can do without it. Keep printers on a trusted network or VLAN, and never forward the printer's ports to the internet.
- **It acts on the printer.** Home Assistant users who can reach its entities can pause, resume and stop prints, change temperatures, fan and print speeds, toggle the light, write CFS filament data (`ha_creality_ws.set_cfs_material`), and turn off a bound power switch mid-print. Limit who can with Home Assistant's own user and dashboard permissions.
- **Camera streams** are read from the printer over the LAN, like the WebSocket, and reach users through Home Assistant's authenticated camera endpoints (WebRTC goes through go2rtc). If you run a stand-alone go2rtc instead of Home Assistant's, securing it is up to you.
- **Notification buttons.** Pause and Resume on the live print card take the same path as the button entities. Stop is marked destructive and requires device authentication on the phone, so a tap on a locked screen cannot end a print.
- **No data leaves your LAN** except what you send yourself: notifications go through the notify targets you pick (the companion app's push relay, for example). There is no telemetry, analytics or cloud call, and a test rejects unexpected external URLs.
- **The diagnostic dump** (`ha_creality_ws.diagnostic_dump`) holds printer telemetry, model and feature detection, options and printer-local URLs. It is meant to be shareable, but it includes your printers' LAN addresses and hostnames; redact them before posting if that matters to you.

## Not a vulnerability

- Anything that needs LAN access to an unauthenticated printer, unless the integration widens it.
- Anything that needs a Home Assistant administrator account.

Ask general security questions in [GitHub Discussions](https://github.com/3dg1luk43/ha_creality_ws/discussions), without sensitive details.
