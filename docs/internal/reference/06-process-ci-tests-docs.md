# ha_creality_ws process, CI, tests and docs - Engineering Reference

**Scope:** GitHub workflows and repo settings, the release flow, the pytest and node test suites, the printer simulator and dev scripts, the user and contributor docs, AI-agent scaffolding and repo hygiene. Runtime code is covered by 01-05 and is only cited here where a doc or test makes a claim about it.
**Branch / version:** `0.9.9` at `da0295a`, `manifest.json` version `0.9.9`, top CHANGELOG heading `## 0.9.9 - Unreleased`, minimum core `2026.7.0` (`hacs.json`, `const.py:18`).
**Audit date:** 2026-10-02. Reference model for process: `/root/ha_washdata` (same maintainer).

Anchors are `path:line` from the repo root. "Verified" means the claim was checked by running something or reading the code; anything else is marked as suspected.

---

## 0. Headline facts

- Home Assistant 2026.7.0 requires **Python >= 3.14.2** (PyPI `requires_python`). CI tests 3.11 and 3.13 only; the dev `.venv` is 3.11.2. The suite passes on 3.14.5 when run by hand (section 3.2).
- Every issue/PR workflow, the issue forms, `config.yml`, the PR templates and `dependabot.yml` exist only on `0.9.9`. GitHub reads all of them from the **default branch** (`main`), so none of the ported automation is live until `0.9.9` merges. The live repo currently reports `isBlankIssuesEnabled: true` and no PR templates.
- `main` is not protected: there are no required checks.
- Tests: 834 pass in about 10 s locally. In a CI-like venv, 828 pass and 6 skip. Line coverage is 61%: setup, unload, the config flow, camera, image and light are mostly or entirely unexercised. Home Assistant is stubbed; there is no real-HA harness.
- The 13 junk `v*` tags are **local only**. They come from the `RobertJansen1` fork remote, and origin has none of them.

---

## 1. Workflow map

All JS actions are pinned by SHA. `hassfest` is deliberately unpinned (`@master`). Default workflow token permission on the repo is `read`, and `can_approve_pull_request_reviews` is false.

| File | Trigger | What it does | Permissions | Gaps |
|---|---|---|---|---|
| `tests.yml` | push to any branch, `pull_request`, nightly 02:15 UTC, dispatch | `pip install -r tools/requirements-test.txt` (pinned), `python -m pytest` on 3.13 and 3.14, with node 22 for the card harnesses (R49) | `contents: read` | Same-repo PRs run twice (push + PR). |
| `hassfest.yml` | push (all refs incl. tags), PR, nightly, dispatch | `home-assistant/actions/hassfest@master` | `contents: read` | None material. |
| `validate.yaml` | push, PR, nightly, dispatch | `hacs/action@1ebf01c` (main, 2026-06-08), category integration | `{}` | `.yaml` extension where every other file is `.yml`. |
| `release.yml` | tag push `v*`, dispatch (`tag` input) | Job `preflight` runs `tools/release_check.sh [--tag]` on 3.13. Job `draft` (tag push only) cuts the CHANGELOG section into notes and runs `gh release create --draft --verify-tag`. `--prerelease` is added if the tag contains `-`. A release that already exists is left alone. | `contents: read`, `draft` job `contents: write` | No check that the tag commit is on `origin/main`. `v*` also matches the junk tags. A release created in the UI is verified only after it is public. |
| `release_references.yml` | `release: published`, dispatch | Scans the release body for `#N` and issue URLs. Each open issue created before publication gets a marker comment `<!-- release-reference-bot --> tag:<tag>` and the `done` label. | `issues: write`, `contents: read` | Any `#N` in the notes counts, including issues mentioned only in passing. |
| `close_done_issues.yml` | daily 09:00 UTC, dispatch | Closes `done` issues inactive for 5 days, but only when the last comment is the owner's or the release bot's | `issues: write` | Identical to washdata's. It trusts any owner comment, so a `done` applied by hand before a release (or left on an issue the owner wants open, #122 on 2026-10-02) closes the issue anyway. `release_references.yml` itself only applies `done` on publish. |
| `issue_validator.yml` | `issues` opened/edited/reopened, `issue_comment` created, dispatch (dry run) | Five jobs. `close-templateless-issues` needs both no template label and no known heading (headings may be indented 0-3 spaces, as CommonMark allows, since R8; the harness runs this job too). `/fine` clears `more info required`. A reporter comment swaps to `awaiting maintainer review`. `restore-template-label` needs 2+ distinctive headings. `validate-bug-report` checks the version against the latest releases, HA against the `hacs.json` minimum, and logs. | `issues: write` | The logs instructions point at a service response that does not exist. The attachment hint conflicts with `render: shell`. The placeholder comparison at `:427` is dead code, since placeholders are never submitted. The comment jobs also fire on PR comments. |
| `close_incomplete_issues.yml` | daily 10:00 UTC, dispatch | `more info required`: one reminder after 2 days, closes after 5. A reporter reply after the flag moves the issue to `awaiting maintainer review`. | `issues: write` | `alreadyWarned` matches any earlier warning, so a re-flagged issue gets no second reminder. |
| `close_incomplete_prs.yml` | daily 11:00 UTC, dispatch | `needs description` closes after 5 days. `needs accepted issue` closes after 3 days, unless the linked issue has since been accepted, in which case it clears both labels and the notice. | `issues`, `pull-requests: write` | `awaiting maintainer` PRs are never re-evaluated. |
| `validate_pr.yml` | `pull_request_target` opened/edited/reopened/synchronize | Completeness: Description over 10 chars and one Type of Change box. A template deleted wholesale means an immediate close. Issue link: translation-only PRs are exempt; otherwise accepted, awaiting or needs-issue. Owner and `type == Bot` authors are exempt. No checkout, so it never runs PR code. | top `contents: read`, jobs `issues`, `pull-requests: write` | Text at `:293` says the check "passes automatically" once the issue is accepted, but nothing re-runs on an issue label event. The bare `#(\d+)` catch-all (`:214`) treats any `#N` as an issue. No job ever fails (comments only), so it cannot be a required check. |
| `protect_accepted_label.yml` | `issues: labeled` | Removes `accepted` when a non-owner applies it, and comments | `issues: write` | Issues only, which is intended. |
| `auto_assign_issues.yml` | `issues` opened/reopened | Assigns the owner. Skips templateless external issues using the same two signals as the validator. | `issues: write` | `KNOWN_HEADINGS` is a third copy of the list. |

Other `.github` files:

- `dependabot.yml`: github-actions only, weekly, grouped, prefix `chore(ci)`. Inactive until it is on `main`.
- `FUNDING.yml`: `ko_fi: 3dg1luk43`.
- `pr-assets/wrapped-telemetry-overlap.png` is a 180 KB PR screenshot.

`.coderabbit.yaml` (repo root) limits CodeRabbit to `custom_components/`, `.github/`, the release script, the requirements files, `tools/simulator/` and `README.md`, because CodeRabbit reviews at most 100 files per PR and `0.9.9` touches 195. Tests and the other docs are not reviewed.

### 1.1 Labels

All labels the workflows use exist on the repo (verified with `gh label list`): `bug`, `feature request`, `documentation`, `more info required`, `awaiting maintainer review`, `accepted`, `done`, `needs description`, `needs accepted issue`, `awaiting maintainer`.

### 1.2 Action pins

| Action | Pinned SHA | Comment says | Actually | Runtime | Latest |
|---|---|---|---|---|---|
| actions/checkout | `11d5960a` | v4.4.0 | v4.4.0 | node20 | v7.0.1 |
| actions/setup-python | `a26af69b` | v5.6.0 | v5.6.0 | node20 | v7.0.0 |
| actions/setup-node | `49933ea5` | v4.4.0 | v4.4.0 | node20 | v7.0.0 |
| actions/github-script | `f28e40c7` | **v7.0.1** | **v7.1.0** (v7.0.1 is `60a0d830`) | node20 | v9.0.0 |
| hacs/action | `1ebf01c4` | main | main @ 2026-06-08 | docker | same |
| home-assistant/actions/hassfest | `@master` | unpinned on purpose | - | docker | - |

### 1.3 Port review against ha_washdata (`da0295a`)

The seven automation workflows are line-for-line ports of washdata's, with these changes:

- the creality field headings
- GitLocalize removed
- a generic `type == Bot` exemption
- the translation paths extended to `www/i18n/`
- the issue validator rewritten to parse versions once, read the HA minimum from `hacs.json`, and skip the legacy markdown template (`## Environment`)
- em dashes and emoji removed from the bot text

No washdata strings, labels or paths remain. The only "ha_washdata" mentions are provenance comments:

- `issue_validator.yml:3`
- `tests.yml:5`
- `release_check.sh:3`
- `tools/tests/js/issue_validator_harness.mjs:2`
- `tools/tests/test_issue_validator_workflow.py:1`
- `www/k_printer_card.js:68`

Not ported, and correctly so:

- `translate_check.yml` (GitLocalize/machine translation)
- washdata's "Checks" job for the minified bundle

`release.yml` is new. It folds washdata's `release_preflight.yml` together with a draft step that washdata does not have.

Inherited weaknesses carried over unchanged:

- the false "passes automatically" text
- the bare `#N` matcher
- the dead placeholder comparison
- `render: shell` on the logs field

---

## 2. Release flow

### 2.1 Version sources

| Source | Value | Who reads it |
|---|---|---|
| `custom_components/ha_creality_ws/manifest.json` `version` | `0.9.9` | HA, HACS, `issue_validator` hint text |
| `CHANGELOG.md` top `## X.Y.Z - <date or Unreleased>` | `0.9.9 - Unreleased` | `release_check.sh`, `release.yml` notes |
| git tag `vX.Y.Z` | not yet | `release.yml`, HACS (via the release) |
| `hacs.json` `homeassistant` / `const.py:18 MINIMUM_HA_VERSION` | `2026.7.0` / `(2026, 7)` | HACS install gate, runtime gate, issue validator. The pair is pinned by `tools/tests/test_utils.py:164`. |

`CHANGELOG.md` arrived on this branch in `df19ff3`, a fix commit that also bumps the version. It was renamed from `.release_notes/RELEASE_NOTES.md`, which is what `main` still has. Bumps are manual edits, and on the feature branch the version went 0.9.8 -> 0.9.9 -> 0.9.8 (`9660889`, `b2ac2ea`).

### 2.2 Steps as designed (after the port)

1. Cut a version branch named `X.Y.Z` from `main`. Bump `manifest.json` and open `## X.Y.Z - Unreleased`.
2. Land work on that branch. Contributor PRs target it, after an issue is `accepted`. Run the CodeRabbit loop (`.claude/skills/coderabbit-loop/`) on the `X.Y.Z -> main` PR.
3. On release day, date the heading (`## X.Y.Z - YYYY-MM-DD`). Then run `tools/release_check.sh --tag vX.Y.Z`. It checks version agreement, a dated heading, a non-empty section, that every shipped JSON parses, `compileall`, `node --check` of the cards, the full pytest run, and a clean tree. Its exit code is the number of failures.
4. Merge the PR into `main` with a merge commit (v0.9.8 = `25c77db`, "Merge pull request #119").
5. `git tag vX.Y.Z <merge commit> && git push origin vX.Y.Z`. `release.yml` then runs the preflight and creates a draft whose body is the CHANGELOG section.
6. Publish the draft by hand. That publish event, made by a human, fires `release_references.yml`: comments and the `done` label. `close_done_issues.yml` closes those issues after 5 quiet days.
7. HACS offers the release (no `zip_release`, so the content is the repo at the tag).

### 2.3 Gaps in the flow

- Nothing checks that the tag commit is on `origin/main`. A tag pushed from the version branch before the merge drafts a release from unmerged code.
- Publishing from the UI (as with v0.9.8, published 2026-09-26T15:18Z) creates the tag and the release together. The preflight then runs after users can already install it.
- If the preflight fails, there is no draft. Recovery means deleting the tag and pushing it again; this is not documented.
- The draft's notes include the `> [List of issues (X.Y.Z)](...milestone...)` line, which is good. `release_references` then treats every `#N` in the section as fixed.
- CI never runs `release_check.sh` on a PR, so version disagreement surfaces only at the tag.

### 2.4 Tags

- **Real releases** (`gh release list`) run `v0.4.5` .. `v0.9.8`, dotted, plus pre-releases `0.6.6-alpha*`, `0.8.0-alpha`, `v0.8.0-alpha2/3`, `v0.7.2-alpha`, `v0.6.5-alpha`, `v0.6.3.6-alpha`.
- **Oddities:**
  - release `v0.9.0` is on tag `v.0.9.0`
  - release `v0.9.5` is on tag `v0.9.5-alpha` but is not marked pre-release
  - four-part versions (`0.9.6.1`, `0.6.3.x`)
- **Junk tags:** `v0.00 v0.01 v0.1 v0.2 v0.3 v0.4 v0.5 v0.9 v0.95 v0.96 v0.97 v0.98 v0.99` (13).
  - They are local only. `git ls-remote --tags RobertJansen1` lists exactly these, and `origin` has none.
  - `v0.00` and `v0.98` point at `34d0d9c` (= `v0.9.6.1`).
  - The rest point at 2026-07-08 commits on the `led_brightness` fork branch (descendants of v0.9.6.1, not in v0.9.7).
  - No `remote.RobertJansen1.tagOpt` is set, so every fetch re-imports them.
  - Risk: a `git push --tags` publishes them, fires `release.yml` 13 times, and puts `v0.99` in front of HACS users. If a release were ever made from one, the validator would parse it as 0.99 > 0.9.9 and tell every reporter they are outdated.
  - Nothing has been deleted.

### 2.5 Branches and divergence

- Local `main` is `122d65d` (v0.9.7), **111 commits behind `origin/main`** (`25c77db`, v0.9.8).
- So `git log main..0.9.9` shows 112 commits, while the real divergence is **2 ahead** (`df19ff3`, `da0295a`) and **1 behind** (the merge commit) `origin/main`.
- Stale local branches: `0.8.0 0.9.3 0.9.5 0.9.6 0.9.6.1 0.9.8 dev v0.6.5-fix1 v0.9.0 pr-70 pr-75 pr-104-latest pr/hawky358/5 translations buzato/main fix/* feat/cfs-material-editing led_brightness`.
- `origin` uses the redirected URL `3dg1luk43/ha-creality-ws.git`; the canonical name is `ha_creality_ws`.
- Model: one long-lived version branch per release, merged to `main` by PR, then tagged on `main`. There is no `dev` branch in use (the local `dev` is from 0.6.5).

---

## 3. Test suite

### 3.1 Commands

```bash
.venv/bin/python -m pytest                 # 834 tests, ~10 s (pyproject: testpaths tools/tests, addopts -q)
.venv/bin/python -m pytest tools/tests/test_X.py
python -m compileall -q custom_components/ha_creality_ws tools
node --check custom_components/ha_creality_ws/www/k_printer_card.js
tools/release_check.sh [--tag vX.Y.Z]
```

There is no `run_tests.sh`; pytest drives the node suites. `.vscode/settings.json` points VS Code's test runner at `tools`.

### 3.2 Results by interpreter (verified 2026-10-02)

| Interpreter | Deps | Result |
|---|---|---|
| `.venv` 3.11.2 | aiohttp, websockets, numpy, aiortc, voluptuous, pyyaml, pytest 9.0.1 | 834 passed, 0 skipped, 10.1 s |
| scratch 3.13.2 (= CI install) | pytest voluptuous pyyaml | 828 passed, 6 skipped, 45 warnings, 6.7 s |
| scratch 3.14.5 | + websockets numpy | 831 passed, 3 skipped (aiohttp absent), 270 warnings, 10.8 s |

The CI skips are the simulator tests: 4 in `test_cfs_simulator.py` and 2 in `test_fan.py`. The live simulator tests also require `.venv/bin/python`, so they never run in CI.

Warnings:
- `__package__ != __spec__.parent` from `test_entry_removal.py:39`. This breaks once Python stops honouring `__package__`.
- `asyncio.get_event_loop_policy` is deprecated in 3.14 and removed in 3.16. It is used by 8 notification/discovery test files.

### 3.3 How Home Assistant is faked

`tools/tests/conftest.py` installs hand-written `homeassistant.*` modules and a bare namespace package for `custom_components.ha_creality_ws`, so `__init__.py` does not run on import. It also installs a fake `ws_client.KClient`. The stubs are shells:

- `DataUpdateCoordinator` has no listeners and no `last_update_success`.
- `CoordinatorEntity` only stores the coordinator.
- `Entity`, `SensorEntity` and `NumberEntity` are empty classes.
- `ConfigEntry` is a `SimpleNamespace(entry_id, options, data)`.

`aiohttp`, `websockets` and `go2rtc_client` are stubbed per test file, and only when they are absent. So CI runs against MagicMock aiohttp while dev runs the real one. Because of these stubs, entity lifecycle, platform forwarding, unload/reload, registries and HA schema validation are never exercised.

**There is no in-process real-HA harness and no real-HA test box.** washdata has `devtools/testbox/`: a real HA container with the tree bind-mounted in, a recording notify platform and smoke scripts. pytest-homeassistant-custom-component 0.13.368 pins `homeassistant==2026.10.0b0` and needs Python >= 3.14, so an in-process harness is now possible; it was not attempted.

### 3.4 File map

"Real" means the test imports integration code and calls it. "Static" means regex/AST/JSON checks on files.

| File | Tests | Covers | Kind |
|---|---|---|---|
| `test_camera_stream_config.py` | 20 | go2rtc stream setup, RTSP URL, fallback | real, go2rtc/aiohttp/Camera mocked, `_BaseCamera.__init__` patched out |
| `test_camera_webrtc_repro.py` | 1 | WebRTC offer error path | real + mocks; docstring claims a cross-file assertion that does not exist |
| `test_card_install.py` | 8 | `frontend.card_version`, `_register_static_path` | real, one regex |
| `test_cfs_card.py` | 33 | runs the 11 `js/test_*.mjs` suites (196 JS tests), `node --check`, i18n/NOTICE/README/services guards | node + static |
| `test_cfs_filament.py` | 37 | CFS filament helpers | real, pure |
| `test_cfs_material_payload.py` | 38 | `modifyMaterial` payload builder | real, pure |
| `test_cfs_material_service.py` | 17 | `set_cfs_material` handler | real handler, fake coordinator, real voluptuous |
| `test_cfs_sensors.py` | 17 | CFS slot sensors | real entities on a SimpleNamespace coordinator |
| `test_cfs_simulator.py` | 11 | the dev simulator | mostly source regex; live tests skip in CI |
| `test_code_hygiene.py` | 4 | PLATFORMS unique, cloud URLs, em dash, callbacks | static; the URL check allows any `http://` or `ws://` URL and scans `*.py` only |
| `test_const_standalone.py` | 2 | `const.py` imports nothing | real import + AST |
| `test_coordinator.py` | 10 | coordinator helpers, `wait_for_fields` | real, stub KClient |
| `test_copilot_instructions.py` | 5 | three headings and three banned phrases in `copilot-instructions.md` | static; does not catch the drift in section 5 |
| `test_entity_cache.py` | 16 | `KEntity` cache reads | real |
| `test_entry_removal.py` | 9 | `async_remove_entry` | real `__init__` loaded by file path |
| `test_fan.py` | 19 | fan entities, setup, simulator fan control | real + simulator |
| `test_gcode_file_info.py` | 38 | G-code metadata, matching | real; `:453-476` only checks that strings are present (still passes with the bug it describes) |
| `test_hacs_static.py` / `test_manifest_static.py` | 2 / 3 | key presence | static; "semver" test checks no format |
| `test_issue_validator_workflow.py` | 27 | runs `restore-template-label` and `validate-bug-report` JS through node; YAML consistency | node; the other three jobs only have their constants checked |
| `test_late_discovery.py` | 33 | capability promotion | real, some regex |
| `test_model_detection.py` | 1 | `ModelDetection` | real |
| `test_no_blocking_calls.py` | 1 | blocking-call scan | static |
| `test_notification_*.py`, `test_notifications.py` | 361 | rules, payload, wire format, dispatch, actions, live card, templates | real; each file has its own HassStub |
| `test_options_flow.py` | 44 | options camera/notification steps | real, ConfigFlow base stubbed, selectors MagicMock |
| `test_printer_card_editor.py` / `test_printer_card_layout.py` | 12 / 8 | printer card | regex on JS (one cross-check against `derive_print_state`) |
| `test_specs_unique.py` | 2 | sensor uid uniqueness | line parser + registry |
| `test_translations.py` | 20 | key parity, placeholders, references | static |
| `test_utils.py` | 32 | utils, min-HA pin | real |
| `test_ws_client_reconnect.py` | 3 | real `KClient` reconnect/power gate | real, `websockets.connect` patched |

### 3.5 JS harnesses (`tools/tests/js/`)

- `test_cfs_card.py:54` globs all 11 `test_*.mjs`. The wrapper (`:77-90`) asserts the exit code only.
- All three harnesses are used: `cfs_card_harness.mjs`, `printer_card_harness.mjs`, and `issue_validator_harness.mjs` (by the Python test).
- **No node means a skip, not a failure**: 32 tests skip and the suite stays green. The skip reason "expected in CI" (`test_cfs_card.py:31`) is wrong, because CI installs node.
- `test_printer_editor.mjs:472`, `test_printer_migration.mjs:188` and `test_printer_telemetry.mjs:297` call `run();` without `await`. A test awaiting a promise that never settles ends the process with exit 0 and no summary line.
- Four other suites call `fn()` without `await` (latent; none of them has async tests yet).

### 3.6 Order dependence (verified)

- `test_ws_client_reconnect.py:50-78` installs a `websockets` stub with no `__spec__` at import time. `test_cfs_simulator.py:120` calls `find_spec("websockets")`, which raises `ValueError` and aborts collection. Running `pytest tools/tests/test_ws_client_reconnect.py tools/tests/test_cfs_simulator.py` in the CI-like venv gives "Interrupted: 1 error during collection". The default order passes only because "c" sorts before "w".
- `test_options_flow.py` installs its stubs at import and removes them in `teardown_module`. Under an item-level shuffle, three of its tests fail.
- `test_ws_client_reconnect.py:83` loads a second copy of the integration as top-level `ha_creality_ws`, through `conftest.py` putting `custom_components/` on `sys.path`.

### 3.7 Coverage (CI-like venv, 61% overall)

| Module | Lines covered | Largest untouched areas |
|---|---|---|
| const, entity | 100% | - |
| notification_rules | 99% | - |
| utils | 92% | - |
| fan | 89% | - |
| coordinator | 86% | `_poll_moonraker_extras`, `async_handle_power_change`, `ensure_connected`, `async_start` |
| number | 66% | target temperature set/read |
| config_flow | 60% | `async_step_user`, `async_step_zeroconf`, probes, `async_step_power`, `_detect_camera_type` |
| sensor | 59% | `add_cfs_entities`, most `native_value`s |
| button | 57% | home-all press |
| ws_client | 41% | receive loop, `_heartbeat`, `_periodic_gets`, `send_set_retry` |
| camera | 36% | platform setup, the whole MJPEG path, direct WebRTC, the WebRTC snapshot |
| `__init__` | 24% | `async_setup_entry`, `async_unload_entry`, options listener, go2rtc migration, `diagnostic_dump`, `request_cfs_info`, device removal |
| frontend | 24% | Lovelace resource registration/migration |
| image, light | 0% | everything |

### 3.8 Lint

There is no linter configuration, and `tools/tests/test_copilot_instructions.py:30-43` asserts that none exists.

A config-less `ruff check --isolated --select F,E9 custom_components` (ruff 0.6.9 from `.venv`) reports **zero** findings today; `tools/` has one unused import. A ruff `ASYNC` pass flags `__init__.py:94`, which is a false positive (the `open` runs inside the executor).

Pyright is not useful until a real `homeassistant` package is installed.

---

## 4. Simulator and dev scripts

| Path | Tracked | What | Notes |
|---|---|---|---|
| `tools/creality_printer_test_server.py` | yes | WS telemetry :9999, HTTP :8000 with WebRTC signaling, MJPEG at `/stream.mjpeg`, CFS `boxsInfo`/`modifyMaterial`, `reqGcodeFile`, `/test/*` control, `--deterministic`, `--cfs-variant edge` | The integration reads MJPEG from `http://host:8080/?action=stream` (`const.py:20,27`), so an MJPEG model cannot stream from the simulator without a Custom URL. It has no preview image (`/downloads/original/current_print_image.png`), no Moonraker :7125 (K2 Base) and no K1C-2025 `webrtcSupport` variant. Its `k2` model has `box_control: False`, whereas the integration grants control to the whole K2 family. |
| `tools/h264_timing.py` | yes | clip timestamp maths, split out for testing | tested via the simulator tests |
| `webrtc_test_server.sh` | yes, **repo root** | starts/stops the simulator as k2plus with a one-year print | writes `.webrtc_test_server.{pid,log}` to the root; tooling is meant to live in `tools/` |
| `tools/release_check.sh` | yes | release preflight (section 2.2) | also what CI runs |
| `tools/requirements.txt` | yes | local work: `-r requirements-test.txt` plus the simulator's runtime (R49) | `requirements-test.txt` is what CI installs, pinned |
| `tools/test_files/` | **no (ignored)** | `deploy_to_ha.sh`, a 137 MB HA log (2025-11-09), K1C WS captures, a HAR, go2rtc OpenAPI, Go WebRTC clients, `internal_docs/` | `deploy_to_ha.sh:17-18` holds the production HA URL and a **plaintext long-lived token**. It was never committed: `git log --all -S` finds nothing. |
| `backups/` | no (ignored) | 215 snapshot directories, 75 MB, written by `deploy_to_ha.sh` | inside the work tree |

---

## 5. Docs map

| Document | Status | Main problems |
|---|---|---|
| `README.md` (906 lines) | significant drift | **Diagnostic Service (`:846-895`)** describes a service response and a saved file. The service returns `None` (`__init__.py:757`) and logs a `CREALITY DIAGNOSTIC DATA` block plus a persistent notification. Camera modes (`:98-101`) omit `webrtc_direct` and `custom`. `:798` says the K1C 2025 camera is unsupported, although WebRTC is used when `webrtcSupport == 1`. Power off makes entities **unavailable**, not zeroed (`:82, :90, :767-770`). YAML resources (`:165-173`) omit `k_cfs_card.js`. Light example `switch.k1c_light` should be `light.*`. CFS entity IDs use unique-id suffixes, not entity IDs. "No sensitive personal data" in the dump (`:880`) is false (IPs, hostnames, entity states). "No polling" ignores the K2 Base Moonraker poll. Undocumented: `request_cfs_info`, the Home/Reconnect buttons, Print Tuning, light brightness, `active_filament_slot`, several card keys. Accurate: the min-HA gate, anchors, `img/` paths, `set_cfs_material`, events, notification placeholders, RTSP ports, filament estimates. |
| `CHANGELOG.md` | minor drift | Same format as washdata (`## X.Y.Z - date`, TL;DR, Keep a Changelog). It claims SemVer while using four-part versions. History starts at 0.7.0. Dates for 0.7.0 (2025-12-19 vs release 2025-12-10) and 0.7.1 (2026-01-04 vs 2025-12-17) disagree with the releases. |
| `CONTRIBUTING.md` | minor drift, one high | `:26` "copy the `printers` section of the response" is the same false premise. `:75` "Python 3.11 or 3.13" is wrong for HA 2026.7. `:96` says to add the simulator "as you would a real printer", which fails for MJPEG. `:104` promises a URL test that does not check `http://`. |
| `SECURITY.md` | accurate | `:32` "WebRTC goes through go2rtc" is not true for `webrtc_direct`. |
| `NOTICE`, `CODE_OF_CONDUCT.md`, `LICENSE` | accurate | - |
| `tools/README.md` | minor drift | `:136-200` documents `./tools/deploy_to_ha.sh`, but the script is at `tools/test_files/deploy_to_ha.sh` (ignored, maintainer-local, hard-coded `/root` paths). `:124` "camera mode selected automatically" runs into the MJPEG port mismatch. |
| `.github/copilot-instructions.md` | significant drift | `:11, :103, :124`: Python 3.11/3.13. `:34, :41`: power off zeroes, where it actually makes entities unavailable. `:42, :112`: Power chip pinned by CSS, where `button_order` actually decides. `:58` excludes the K1 SE from the K1 family, where code includes it. `:64` says chamber control is K2 Pro/Plus only, where code applies it to the whole K2 family plus live `targetBoxTemp`. `:75`: go2rtc `localhost:11984` is only an options default. `:135` tells contributors to return service responses, which no service does (likely the source of the README error). `:152-165` "Recent updates (2025-11-12)" is stale. The module map omits `frontend.py`, `notification_rules.py` and `const.py`. Review bots read this file as ground truth. |
| `.github/ISSUE_TEMPLATE/*.yml` | minor drift, one high | `bug_report.yml:131`: the false "printers section of the response". `:96-102`: Camera Mode is missing `webrtc_direct` and `custom`. `:141` `render: shell` disables attachments, yet `:131/:140` tell users to attach. |
| `.github/pull_request_template.md`, `PULL_REQUEST_TEMPLATE/translation.md` | accurate | Two templates by design: the default plus `?template=translation.md`, as in washdata. `translation.md:58-59` lists `strings.json` (the English source) next to "did not change English wording". |
| `.claude/skills/coderabbit-loop/*` | minor drift | `SKILL.md:117-132` and `mechanics.md:319-334` say the suite takes ~3 s with "5 skipped", and that CI installs only pytest and voluptuous. In fact it takes ~10 s, with 0 skips in `.venv` and 6 in a CI-like venv, and CI also installs pyyaml. |
| `conductor/*` (tracked) | stale, contradicts house rules | Gemini "conductor" scaffolding from `e24100f` (2026-02-08). `workflow.md:8,49-58`: >80% coverage, agent commits, git notes. `code_styleguides/python.md:6,16`: pylint, 80-char lines. The only track, `fix_webrtc_20260208` (status `new`, all tasks unchecked), is for #76, closed 2026-03-11. |
| `.agent/*`, `.issues/*` (ignored) | irrelevant | Antigravity Kit: 20 generic personas (game, SEO, mobile), 36 skills, `mcp_config.json` with a placeholder key. `.issues/` holds a copy of #76. |
| `tools/test_files/internal_docs/*` (ignored) | stale | Code review, change notes and release notes from November 2025 (0.6.5 era). They describe K1-only preview gating and a web-root crawl. |
| `CLAUDE.md` | missing | washdata has one (rules, commands, traps, pointer to `docs/internal`). |

---

## 6. Repo hygiene inventory

| Item | State | Note |
|---|---|---|
| `.gitignore` | 13 lines | Lists `conductor`, but 13 `conductor/` files are tracked, so the rule does nothing for them and silently drops new ones. Missing: `.claude/settings.local.json` (ignored only by the user's global `~/.config/git/ignore`), a generic `__pycache__/`, `.coverage`. `.pytest_cache` and `.ruff_cache` ignore themselves. |
| Line endings | no `.gitattributes`, `core.autocrlf` unset | CRLF: `button.py`, `config_flow.py`, `const.py`, `coordinator.py`, `entity.py`, `ws_client.py`, `www/k_printer_card.js`. The other 125 text files are LF; none is mixed within a file. |
| Em dash U+2014 | 0 in tracked files | Enforced by `test_code_hygiene.py:56`. En dashes (U+2013) remain in `copilot-instructions.md` (16), `README.md:539`, `CHANGELOG.md:145`, `tools/README.md:20`, the simulator, and `k_printer_card.js` comments; the rule allows them. |
| `.claude/` | skill + command tracked, `settings.local.json` untracked | correct |
| `.vscode/settings.json` | tracked | harmless; `ha_creality_ws.code-workspace` is ignored and points at `../ha_config` |
| `.venv` | 278 MB, Python 3.11.2, ignored | wrong Python for HA 2026.7 |
| `backups/` | 215 dirs, 75 MB, ignored | inside the repo; `grep -r` and tools that ignore `.gitignore` index stale copies |
| `.webrtc_test_server.log` | root, ignored | from `webrtc_test_server.sh` |
| `.git` | 22 MB | fine |
| Remotes | `origin` (redirect URL), `RobertJansen1`, `Ahmed-max`, `oscfdezdz` | contributor remotes fetch their tags |

---

## 7. Open items register

Severities match the final audit reply. `[CI]` = workflows, `[REL]` = release, `[TEST]` = tests, `[DOC]` = docs, `[HYG]` = hygiene, `[SEC]` = security, `[PROC]` = process.

1. [DOC, high] The `diagnostic_dump` "response" premise appears in README, CONTRIBUTING, the issue form, the validator message and copilot-instructions.
2. [TEST, high] No real-HA coverage of setup, unload, reload or the config flow; image and light at 0%.
3. [CI, medium] Python matrix 3.11/3.13 vs HA's 3.14.2; dev venv 3.11.
4. [PROC, medium] `main` unprotected; nothing required to merge.
5. [PROC, medium] Automation inert until `0.9.9` is merged; first live run is untested.
6. [REL, medium] Junk tags re-imported from a fork remote; `v*` tag filter would accept them.
7. [SEC, medium] Plaintext HA token in `tools/test_files/deploy_to_ha.sh`.
8. [TEST, medium] Order dependence (`websockets` stub without `__spec__`).
9. [TEST, medium] Unawaited `run()` in three printer-card suites; wrapper checks only the exit code.
10. [PROC, medium] `conductor/` tracked but ignored, and stale.
11. [DOC, medium] copilot-instructions and README drift (section 5).
12. [TEST, medium] Simulator MJPEG URL differs from the integration's.
13. [REL, low] No on-main check for tags; UI releases bypass preflight; version bump mixed into fix commits.
14. [CI, low] Node 20 EOL, node20 action runtimes, wrong `# v7.0.1` comment, no timeouts, unpinned deps.
15. [TEST, low] URL hygiene test allows any `http://`; string-presence tests; deprecation warnings.
16. [HYG, low] `backups/` in the tree, root-level `webrtc_test_server.sh`, `.gitignore` gaps, stale branches, stale local `main`.
