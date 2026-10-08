# CLAUDE.md

Guidance for Claude Code in this repository. Rules and traps only. The "how it works" and the
open defect list live in [`docs/internal/INTEGRATION_REFERENCE.md`](docs/internal/INTEGRATION_REFERENCE.md)
and its six deep-dives under `docs/internal/reference/`. Read the relevant deep-dive before
changing a subsystem, and update it (and the register status) in the same change.

## Project

Home Assistant custom integration for Creality K1/K2/Hi/Ender 3 V3 printers over a local
WebSocket (`ws://<host>:9999`, subprotocol `wsslicer`, `local_push`), plus two bundled Lovelace
cards (`www/k_printer_card.js`, `www/k_cfs_card.js`). Minimum HA 2026.7.0, so Python 3.14.

## Commands

```bash
.venv/bin/python -m pytest                       # full suite, ~90 s, includes the node card suites
.venv/bin/python -m pytest tools/tests/test_notification_live.py -k name -v
python -m pytest tools/tests_ha                  # inside a real HA; Python 3.14 + tools/requirements-ha.txt
node --check custom_components/ha_creality_ws/www/*.js
python3 -m compileall -q custom_components/ha_creality_ws
tools/release_check.sh                           # release preflight (what CI runs)
tools/release_check.sh --tag v0.9.9              # also require tag == manifest version
python3 tools/creality_printer_test_server.py --help   # simulated printer (tools/simulator/); control UI :8099/ui/
tools/simulator.sh on [model]|off|status|logs   # simulator as a systemd service, control UI :8888/ui/
```

`.venv` is Python 3.11 (rebuilding it needs a 3.14 interpreter on the host); CI runs 3.13 and 3.14,
and `tools/tests_ha` needs 3.14. `.venv` shows no skips; a CI-like venv
(`tools/requirements-test.txt`) skips the simulator tests that need `aiohttp`/`websockets`.

## What the tests can and cannot prove

Home Assistant is **stubbed** in `tools/tests/conftest.py`, and the `KClient` stub's
`is_connected` is always False. `tools/tests_ha` runs the integration inside a real HA
(pytest-homeassistant-custom-component, its own CI job) for entry setup, unload and reload, the
config and options flows, the registries and the power switch; never run both suites in one
process. The camera decision tree, MJPEG, image and number setters still run only stubbed. So for
camera changes, or anything `tools/tests_ha` does not reach, a green suite is not evidence:
verify on the test box (`tools/testbox`) or a real HA, and say which in the commit or report.

- A new test is not done until its **name** appears in the output and it **fails with the fix
  reverted**. Tests here have repeatedly passed while proving nothing.
- In `tools/tests/js/*.mjs`, register new `test(...)` calls **above** the runner loop at the
  bottom of the file.
- Python tests that touch the loop use `asyncio.run`; the suite must be order-independent.
- Never re-implement production logic in a test body; call the real function.

## Rules

- **No em dash (U+2014) anywhere.** Test-enforced. Use " - " or reword.
- **Every user-visible string lives in `strings.json` + every `translations/*.json`, or the
  card's `www/i18n/*.json`.** No inline literals in `.py`/`.js`, including notification bodies and
  exception messages. Add a new key to all locales in the same change.
- **Non-English translations are produced by a subagent** given the UI context and the locale
  file's existing tone. Never machine-translate inline.
- **No fabricated data.** Refuse features that need values the printer does not stream (#93,
  #117). Verify the field exists in real telemetry before building on it.
- Local only: no cloud calls. Async only: no blocking I/O on the loop (`test_no_blocking_calls.py`
  does not catch everything, e.g. `socket.gethostbyname`).
- Do not rename unique_id suffixes, option keys or card config keys without a migration.
- `const.py` must stay import-free (`test_const_standalone.py`).
- `.github/copilot-instructions.md` is read by review bots as ground truth and pinned by
  `test_copilot_instructions.py`; keep it consistent with this file.
- No formatter, and no lint config: CI runs only `ruff check --isolated --select F,E9`. Do not
  reformat; match the surrounding style. Keep each file's line endings (`test_line_endings.py`).
- Commits: conventional, lowercase (`fix: ...`, `ci: ...`). Version bumps in their own commit.
- CHANGELOG: `## X.Y.Z - Unreleased` heading, TL;DR rewritten as a whole, entries as symptom,
  cause, fix, issue link. The repo-process model is `/root/ha_washdata`; adapt, don't copy.
- Times for people in Europe/Prague local; UTC only when comparing with API timestamps.

## Traps

- **CRLF files:** `button.py`, `config_flow.py`, `const.py`, `coordinator.py`, `entity.py`,
  `ws_client.py`, `www/k_printer_card.js`. No `.gitattributes`. Python `write_text()` silently
  converts them to LF; use the Edit tool or `sed -i`, or write bytes. If `git diff --stat` shows
  hundreds of lines for a small change, the endings flipped.
- **Local `main` is stale.** Compare against `origin/main`.
- **Never `git push --tags`.** Junk tags (`v0.00`-`v0.99` family, `v0.4.7`, `v0.5.0`) are
  fetched from the `RobertJansen1` fork remote and would fire `release.yml`. Real releases are
  dotted (`v0.9.7`, `v0.9.8`); `v0.98` is not 0.9.8. Push only the one release tag.
- **GitHub reads issue forms, PR templates, `dependabot.yml` and scheduled /
  `pull_request_target` workflows from `main`.** A workflow change is untested until merged.
- **`diagnostic_dump` answers three ways**: the data as its response (redacted unless
  `include_sensitive_data`), a "CREALITY DIAGNOSTIC DATA" block at WARNING, and a notification.
- **Telemetry is cumulative:** `ws_client._state` is never cleared, so a key the printer stops
  sending keeps its last value, and a blank `""` persists until re-sent.
- **Capabilities are cached in `entry.data`** (model, camera type, firmware). The firmware and the
  camera type follow telemetry (R40, R5); the rest is re-cached on an integration upgrade, a moved
  printer or a missing key, never mid-session.
- **Entity unique_ids contain the host** (`entity.py:23`). Changing that needs a registry migration.
- The simulator (`tools/simulator/`, R77) mimics real ports: MJPEG on `:8080/?action=stream`,
  WebRTC on `:8000`, preview on `:80`, Moonraker on `:7125` (K2 Base). Changing its model on the same
  IP leaves the integration's cached camera type behind (by design, #46): re-add the entry.
  Camera media is pre-rendered in `tools/simulator/media/`; never encode at runtime.
- On the test box, go2rtc receives no RTP from the simulator (old or new) although ICE connects and an
  aiortc client decodes it fine (R78); WebRTC snapshots through go2rtc time out there.
- `tools/test_files/` is gitignored and holds a secret (`deploy_to_ha.sh`). Never copy from it
  into tracked files.
- `backups/`, `conductor/`, `.agent/` and `tools/test_files/internal_docs/` are stale scaffolding,
  not guidance.
- CodeRabbit often claims a fix is needed to "pass the configured lint checks". There is no lint
  config, and the one lint job checks F and E9 only. Judge each finding on its merits; see `.claude/skills/coderabbit-loop/`.

## Release checklist

1. Bump `manifest.json` and add `## X.Y.Z - Unreleased` in a `chore:` commit.
2. On release day: date the heading (local date), run `tools/release_check.sh --tag vX.Y.Z`.
3. Merge to `main`, tag the merge commit, push only that tag.
4. Publish the drafted release (never create it in the UI); that fires `release_references`.
