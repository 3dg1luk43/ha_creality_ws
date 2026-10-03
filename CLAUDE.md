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
.venv/bin/python -m pytest                       # full suite, ~10 s, includes the node card suites
.venv/bin/python -m pytest tools/tests/test_notification_live.py -k name -v
node --check custom_components/ha_creality_ws/www/*.js
python3 -m compileall -q custom_components/ha_creality_ws
tools/release_check.sh                           # release preflight (what CI runs)
tools/release_check.sh --tag v0.9.9              # also require tag == manifest version
python3 tools/creality_printer_test_server.py --help   # simulated printer (tools/simulator/); control UI :8099/ui/
./webrtc_test_server.sh on|off|status            # mock K2 Plus in the background for camera work
```

`.venv` is Python 3.11 and CI runs 3.11/3.13; HA itself needs 3.14 (register R49). A CI-like venv
(pytest, voluptuous, pyyaml only) shows 6 skips; `.venv` shows 0.

## What the tests can and cannot prove

Home Assistant is **stubbed** in `tools/tests/conftest.py`; there is no real HA in any test.
Setup, unload, reload, the config flow, the camera decision tree, MJPEG, light, image and number
setters never run under test, and the `KClient` stub's `is_connected` is always False. Coverage
is 61%. So for lifecycle, config-flow, camera or power-switch changes, a green suite is not
evidence: verify on a real HA (`tools/test_files/deploy_to_ha.sh`, maintainer-local) or against
the mock printer, and say which in the commit or report.

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
- No linter or formatter is configured: do not reformat, match the surrounding style.
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
- **`diagnostic_dump` returns nothing.** It logs a "CREALITY DIAGNOSTIC DATA" block at WARNING
  and posts a persistent notification.
- **Telemetry is cumulative:** `ws_client._state` is never cleared, so a key the printer stops
  sending keeps its last value, and a blank `""` persists until re-sent.
- **Capabilities are cached in `entry.data`** (model, camera type, firmware) and refreshed only
  when the integration version changes.
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
  config or job. Judge each finding on its merits; see `.claude/skills/coderabbit-loop/`.

## Release checklist

1. Bump `manifest.json` and add `## X.Y.Z - Unreleased` in a `chore:` commit.
2. On release day: date the heading (local date), run `tools/release_check.sh --tag vX.Y.Z`.
3. Merge to `main`, tag the merge commit, push only that tag.
4. Publish the drafted release (never create it in the UI); that fires `release_references`.
