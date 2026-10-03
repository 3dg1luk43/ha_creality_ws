# The test box - a real Home Assistant with a mock printer

A disposable Home Assistant container plus the mock printer
(`tools/creality_printer_test_server.py`), on a private network, with the
working tree bind-mounted in. Ported from ha_washdata's test box.

It exists because the unit suite stubs Home Assistant (`tools/tests/conftest.py`).
Setup, unload, reload, the config and options flows, the entity and device
registries, the notify schemas and `mobile_app`'s push path never run there, so a
change in any of them can pass every test and still be broken. Here they are all
real. On its first runs it reproduced #121 (a blank value leaves a sensor frozen,
with `non-numeric value` errors in the log) and #39 (a host change doubles every
entity to `_2` on a second device), and caught a deprecated device-registry call
in the fix for the latter.

The unit suite is still where logic belongs; the box is for whatever crosses
into Home Assistant. Timing here is real, so it proves behaviour, not timing.

## Quick start

```bash
cd tools/testbox
./up.sh --fresh                       # ~40 s cold: both containers, config, owner, token
../../.venv/bin/python3 hactl.py setup   # add the printer through the real user flow
../../.venv/bin/python3 hactl.py states sensor.creality_k1c_
./down.sh                             # stop (add --wipe to delete config/ too)
```

- UI: <http://127.0.0.1:8322>, user `testbox`, password `testbox-pw-0123`.
- The printer is `172.31.77.10` (network `box`) and also `172.31.78.10` (network
  `alt`), so a test can move it: `docker network disconnect creality-testbox_alt
  creality-testbox-printer` kills the second address.
- `PRINTER_MODEL=k2plus ./up.sh` picks another mock model (see `MODEL_CONFIGS` in
  the server).
- The integration is mounted read-only, so the box always runs the code being
  edited; `hactl.py restart` reloads it. An edit to the mock printer needs
  `docker compose restart printer`.

## What is where

| Path | What it is |
|---|---|
| `docker-compose.yml` | the two containers, both networks, ports bound to 127.0.0.1 |
| `printer/Dockerfile` | python 3.13 + the mock's dependencies (aiortc is not in the HA image) |
| `up.sh` / `down.sh` | start + onboard / stop (`--wipe` deletes `config/`) |
| `hactl.py` | the driver: REST, WebSocket, flows, registry, pushes, mock control |
| `smoke.py` | the end-to-end checks; exit code is the number of failures |
| `card_check.mjs`, `package.json` | the bundled cards in headless Chromium against the box |
| `support/configuration.yaml` | baseline HA config, copied into `config/` on first start |
| `support/custom_components/testbox_tools/` | push capture endpoint + zeroconf injection |
| `support/custom_components/testbox_notify/` | `notify.plain_testbox`, a non-mobile target that records payloads |
| `config/` | generated, gitignored: `.storage`, `home-assistant.log`, captures, token |

## Driving it

```bash
H="../../.venv/bin/python3 hactl.py"
$H printer-set 'bedTemp0=""'          # force telemetry on the mock (JSON values)
$H printer-reset                       # drop every forced field
$H options $($H entry-id) connection '{"host":"172.31.78.10","polling_rate":0}'
$H registry                            # this integration's devices and entities
$H zeroconf --host 172.31.78.10 --hostname creality-k1c.local.   # returns the flow result
$H flows                               # in-progress flows (a discovery awaiting confirm)
$H register-phone "Testbox iPhone" --os iOS --manufacturer Apple
$H register-phone "Testbox Pixel" --os Android --manufacturer Google --model Pixel
$H pushes                              # what mobile_app posted to the "relay"
$H errors                              # ERROR/WARNING lines from the HA log
```

`register-phone` creates a real `mobile_app` registration whose `push_url`
points back into the box, so `pushes` shows exactly what core sends to the push
relay, after its own Live Activity routing. That is the only place the iOS and
Android wire formats can be checked end to end without a phone.

## The smoke run

```bash
./up.sh --fresh && ../../.venv/bin/python3 smoke.py     # ~5 min, 16 checks
```

Adds the mock through the user flow, then checks on real Home Assistant:
entities and numeric states, a blank value reading unknown (#121), the plug
switch stopping and restarting the connection (#45), a two-minute mock print
announced to a fake iPhone (native types, routed by core as a Live Activity), a
fake Android phone (strings) and a plain target (text only) (#125), the
diagnostics download hiding the address, a host change keeping every entity
(#39), and no integration errors in the log. Against the code from before the
2026-10-02 fixes it fails 7 of the 16, each on the bug it pins.

### Upgrade test

`INTEGRATION_DIR` mounts another checkout in place of the working tree:

```bash
git worktree add /tmp/old v0.9.8
INTEGRATION_DIR=/tmp/old/custom_components/ha_creality_ws ./up.sh --fresh
../../.venv/bin/python3 hactl.py setup && ../../.venv/bin/python3 hactl.py registry > before.json
docker compose up -d --force-recreate ha     # back to the working tree, same config/
../../.venv/bin/python3 hactl.py registry > after.json   # compare, then run smoke.py
```

## The cards in a real browser

```bash
npm install                        # once, here; Playwright reuses cached browsers
node card_check.mjs                # phone width (390 px), 20 s of live telemetry
node card_check.mjs --width 300    # narrow enough to wrap the telemetry row
```

Builds a throwaway dashboard of two printer cards on the mock printer (one named
in Czech), opens it in headless Chromium and fails on an error card, a rebuild
loop or a card whose DOM is replaced by a state update. Against the 0.9.8 card it
reports all three: the Czech-named card is an error card, and at 300 px Lovelace
built 12 card elements in 20 seconds.

## Pointing it at a real printer

`hactl.py setup --host <printer IP>` adds a real printer instead of the mock: the
containers can reach the LAN through Docker's NAT. Keep it read-only (telemetry,
camera, discovery) unless the owner of the printer has agreed to commands being
sent to it, and remember the real printer's Home Assistant may be connected at
the same time.
