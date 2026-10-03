# ha_creality_ws Integration Reference (Internal)

> **Status:** internal engineering reference, generated 2026-10-02 against branch `0.9.9`
> (manifest `0.9.9`, HEAD `da0295a`; `origin/main` is `25c77db`).
> **Audience:** maintainers and Claude. Not shipped to users: HACS installs only
> `custom_components/ha_creality_ws/` (`hacs.json` `content_in_root: false`).
>
> **How to use it:** this file is the synthesis and index. Each subsystem has a summary here
> and a full deep-dive under [`reference/`](reference/) with `file:line` anchors. When this file
> and a deep-dive disagree, the deep-dive wins, and the code wins over both. The
> [register](#7-improvement--tech-debt-register) in section 7 is the actionable part.
>
> **House style:** no em dash characters anywhere in this repo (`test_no_em_dashes_anywhere`).
>
> **Maintenance rule:** when a register item is fixed, change its status in place (do not
> delete it) and cite the commit. When behaviour, a constant or a process changes, update the
> deep-dive that describes it in the same change.

---

## 1. What the integration is

A Home Assistant custom integration (`domain: ha_creality_ws`, `iot_class: local_push`, min
HA `2026.7.0`, so Python 3.14) for Creality K1 / K1C / K1 Max / K1 SE, K2 / K2 Pro / K2 Plus /
K2 Base, Hi and Ender 3 V3 printers. It holds one plain `ws://<host>:9999` WebSocket per printer
(subprotocol `wsslicer`, no auth, no TLS), merges the cumulative telemetry frames, and exposes:

- sensors (temperatures, progress, layers, times, job and status, CFS boxes and slots,
  slicer estimates from G-code metadata), numbers (targets, print tuning), light, fans,
  buttons, a print-preview image and a camera (MJPEG, WebRTC via go2rtc, direct WebRTC, custom URL);
- an optional power-switch entity that gates connecting;
- print notifications to companion apps (Android, iOS Live Activity, actionable buttons) and
  persistent notifications;
- three services (`request_cfs_info`, `set_cfs_material`, `diagnostic_dump`);
- two bundled Lovelace cards, `k-printer-card` and `k-cfs-card`, auto-registered as resources.

Everything is local. The only HTTP side channels are the printer's own web endpoints (camera,
preview image, Moonraker on K2 Base for chamber target).

## 2. Module map

| File | Lines | Role | Deep dive |
|---|---|---|---|
| `ws_client.py` | 535 | `KClient`: connect loop, backoff, heartbeat watchdog, periodic GETs, `send_set_retry`. Cumulative `_state` dict. | [01](reference/01-protocol-and-core.md) |
| `coordinator.py` | 2279 | Push coordinator: frame merge, late discovery, G-code metadata, Moonraker poll, pause queue, power switch, **all notification delivery**. | [01](reference/01-protocol-and-core.md), [03](reference/03-notifications.md) |
| `__init__.py` | 1112 | Setup/unload/reload, option migrations, capability cache in `entry.data`, services, notification-action listener, device removal. | [01](reference/01-protocol-and-core.md), [02](reference/02-entities-camera-config.md) |
| `notification_rules.py` | 1279 | Pure decision layer: priming, `is_new_job_cycle`, `JobEndWatch`, live-card payload builders, action ids. | [03](reference/03-notifications.md) |
| `utils.py` | 716 | `coerce_numbers`, `derive_print_state`, model detection, zeroconf helpers, CFS payload validation. | [01](reference/01-protocol-and-core.md) |
| `const.py` | 321 | Constants. Must stay import-free (`test_const_standalone.py`). | [01](reference/01-protocol-and-core.md) |
| `entity.py` | 130 | `KEntity` base: availability, unique_id, device info. | [01](reference/01-protocol-and-core.md), [02](reference/02-entities-camera-config.md) |
| `sensor.py` | 1288 | SPECS-driven sensors, CFS sensors, slicer estimates, late discovery. | [02](reference/02-entities-camera-config.md) |
| `camera.py` | 1549 | MJPEG camera, WebRTC camera (go2rtc stream management, direct mode), mode decision tree. | [02](reference/02-entities-camera-config.md) |
| `config_flow.py` | 811 | User + zeroconf steps, multi-page options flow, camera auto-detect. | [02](reference/02-entities-camera-config.md) |
| `number.py` / `light.py` / `fan.py` / `button.py` / `image.py` | 286 / 116 / 90 / 106 / 150 | Remaining platforms. | [02](reference/02-entities-camera-config.md) |
| `frontend.py` | 370 | Static paths, Lovelace resource registration and cache-busting, legacy `/local/` migration. | [04](reference/04-frontend-cards.md) |
| `www/k_printer_card.js` | 2061 | Printer card + editor (Style Editor, device prefill, presets). | [04](reference/04-frontend-cards.md) |
| `www/k_cfs_card.js` | 2705 | CFS card + editor + material-edit dialog. | [04](reference/04-frontend-cards.md) |
| `strings.json`, `translations/{en,es}.json`, `www/i18n/{en,es}.json` | 346 / 169 | HA and card strings. Two locales. | [02](reference/02-entities-camera-config.md), [04](reference/04-frontend-cards.md) |

Tooling: `tools/tests/` (pytest + `js/*.mjs` harnesses, 834 tests, ~10 s), `tools/creality_printer_test_server.py`
(mock printer), `tools/release_check.sh`, `.github/workflows/` (12 workflows). See [06](reference/06-process-ci-tests-docs.md).

## 3. Data flow

```
printer :9999 --frames--> KClient._loop --merge into _state--> coordinator._handle_message
                                                                  |-- merge_telemetry / late discovery signal
                                                                  |-- derive_print_state (utils)
                                                                  |-- _flush_pending (queued pause/resume)
                                                                  |-- _check_notifications --> notification_rules (decide)
                                                                  |                        --> _async_deliver_one (deliver)
                                                                  |-- _maybe_request_gcode_info / Moonraker poll
                                                                  '-- async_update_listeners --> entities --> cards
power switch state change --> async_handle_power_change --> client.start()/stop()
```

## 4. Subsystem summaries

### 4.1 Protocol and core ([01](reference/01-protocol-and-core.md))
One task per printer runs connect, read, heartbeat and periodic GETs. Backoff grows to 300 s
(`const.py:50`). Telemetry is cumulative: `ws_client._state` is never cleared, so a key the printer
stops sending keeps its last value. Availability is "connected and not powered off".
Capabilities (model, camera type, firmware) are cached in `entry.data` once and refreshed only
when the integration version changes. That single fact is behind R5 and R40.

### 4.2 Entities, camera, config ([02](reference/02-entities-camera-config.md))
Entity unique_ids and the device identifier are `"<host>-<key>"` (R4). The camera mode is decided
at setup from the cached type or a forced option; WebRTC goes through HA's go2rtc (stream named
per entry, source `webrtc:http://<ip>:8000/call/webrtc_local#format=creality` on K2), direct
mode signals the printer itself. The options flow is multi-page and edits `entry.data["host"]`
directly; there is no reconfigure step.

### 4.3 Notifications ([03](reference/03-notifications.md))
Decision in `notification_rules.py`, delivery in `coordinator.py`. Events: live card (progress
updates), completed, stopped, finishing soon, error, runout, dismissals, plus bus events.
Priming (#112) suppresses events on the first frames after connect. Bodies follow
`hass.config.language` because an integration is never told the recipient's language. **One wire
format is applied to every mobile target** (R1), which breaks iOS.

### 4.4 Frontend cards ([04](reference/04-frontend-cards.md))
Vanilla custom elements with shadow DOM, no build step, no minified bundle. Both cards carry a
duplicated i18n loader and fallback English dict. The printer card fully re-renders on every
`hass` assignment and uses `ll-rebuild` to grow (R6). `frontend.py` registers resources with a
`?v=` hash and migrates old `/local/` installs.

### 4.5 Issue history ([05](reference/05-issue-history.md))
68 issues: 20 fixed and tested, 26 fixed without a test, 8 closed but only partly fixed, 9 closed
without a fix, 1 unclear, 4 open (#46, #122, #124, #125). Recurring themes: camera (10),
CFS (10), card (10), availability after power cycle (9), model detection (7), notifications (5).

### 4.6 Process, CI, tests, docs ([06](reference/06-process-ci-tests-docs.md))
CI runs hassfest, HACS validate and pytest on Python 3.11/3.13. Home Assistant is **stubbed** in
`conftest.py`; no test runs a real setup, unload or config flow. Coverage is 61% (`__init__` 24%,
`camera` 36%, `ws_client` 41%, `frontend` 24%, `image` and `light` 0%). The ported issue/PR
automation only goes live once it is on `main`.

## 5. Cross-cutting concerns

- **Strings:** policy is that every user-visible string lives in `strings.json` /
  `translations/` / `www/i18n/`. Violations exist in Python (service errors, persistent
  notifications, sensor literals, WebRTC errors) and JS (card picker names). See R33.
- **Line endings:** 7 CRLF files (`button.py`, `config_flow.py`, `const.py`, `coordinator.py`,
  `entity.py`, `ws_client.py`, `www/k_printer_card.js`), no `.gitattributes`.
- **Data honesty:** features that need values the printer does not report are refused (#93,
  #117). Note: #93 grams may now be derivable from printer-reported slicer weight and length for
  single-material files (R56).
- **Real hardware is the only end-to-end test.** The mock printer lacks CFS, Moonraker, the
  preview image and a K1C 2025 variant, and serves MJPEG at a different URL from the one the
  integration reads (R48).

## 6. Documentation map

| Doc | Audience | Status (2026-10-02) |
|---|---|---|
| `README.md` | users | Drifted: camera modes, K1C 2025 camera, power-off "zeroes", resource YAML, entity ids, diagnostic "response" (R9, R61) |
| `CHANGELOG.md` | users | washdata format since 0.9.9; broken milestone links, wrong 0.7.x dates (R67) |
| `CONTRIBUTING.md`, `SECURITY.md` | contributors | `CONTRIBUTING.md:26` diagnostic "response" (R9) |
| `.github/copilot-instructions.md` | review bots | Drifted, pinned by `test_copilot_instructions.py` (R62) |
| `tools/README.md` | maintainers | Wrong deploy path (R63) |
| `CLAUDE.md` | Claude | New in this pass; rules and traps only |
| `docs/internal/` | maintainers, Claude | This reference, new in this pass |
| `conductor/`, `.agent/`, `tools/test_files/internal_docs/` | none | Stale AI scaffolding and Nov 2025 notes; not guidance (R64) |

---

## 7. Improvement / tech-debt register

Status: `OPEN` unless stated. Severity: H / M / L. Verification: `V` = verified by reading the code
(and by probe where noted in the deep-dive); `S` = suspected, needs a capture or hardware.
Source column points at the deep-dive section with the full scenario and fix.

**Progress log.**

- 2026-10-02/03, P0 section: R1-R9 fixed (see each row), with R30, part of R25 (the zeroconf double reload), part of R28 (the progress `or`), part of R65 (the camera signalling probes) and part of R47 (the test box). **Section check on the test box:** `tools/testbox/smoke.py` passes 16/16 on a fresh box and 16/16 after an upgrade from the pre-fix code (registry identical: 59 entity ids and unique ids, one device). The pre-fix code fails 7 of the 16, each on its bug. `card_check.mjs` passes at 300 and 390 px.

- 2026-10-03, P1 section: R10-R25, R27, R28 fixed (see each row); R26 needs a decision; R29 needs a capture. Along the way: R30 (diagnostics), part of R33 (new strings are all translated, en + es), R65 (dead camera probes), R47 (test box gained `card_check.mjs` sections and `hactl.py go2rtc`). **Section check:** smoke 16/16 on a fresh box; upgrade from the pre-session code `da0295a` keeps the registry identical (59 ids, one device) with no integration errors, then smoke 16/16; `card_check.mjs` passes at 300 and 390 px including the CFS sections overlap check. 949 unit tests, 13 JS suites.

### 7.1 P0 - user-visible breakage now

| # | Sev | V | Area | Finding | Anchor | Fix | Src |
|---|---|---|---|---|---|---|---|
| R1 | H | V | notify | **FIXED `75f1ba1`** (part 1 of #125; part 2 open, see R70). **iOS gets Android's all-strings payload (#125).** `stringify_data` runs for every mobile target. The iOS app decodes `chronometer` as Bool and `countdown_end` as Double, so the Live Activity push-to-start and every update are silently dropped; `live_update`/`silent` strings defeat local push and make every refresh alert; Stop loses `destructive` and `authenticationRequired`. `test_notification_wire_format.py:88` pins the wrong behaviour. | `coordinator.py:2240-2244` | Shape per target OS in `_async_deliver_one`: native types for Apple, strings for Android. Put typed keys in `content_state`. Add an iOS wire test (the dispatch stub lacks `config_entries.async_entries`, so `_target_os` is always None in tests). | 03 |
| R2 | H | V | core | **FIXED `5666b59`** (unavailable/unknown blips ignored while connected). **Power-off is invisible while the socket looks connected (#45 regressed in `85605c7`, contributes to #121).** `power_is_off()` returns False when connected, so a plug cutting a running printer never records the off edge, and the next on edge does not restart the client. The connect loop's power check runs only on forced attempts (inverted vs its comment), and backoff is not reset after a long session that ends in error. Result: up to 300 s plus boot before data returns. Tests pin the inverted check (`test_ws_client_reconnect.py:173-205`). | `coordinator.py:312-313`, `:381-407`; `ws_client.py:192-215`, `:290-303` | Decide the edge from the switch alone; on unforced iterations with the switch off reset backoff and poll ~10 s; reset counters in `finally` when the session lasted >= 10 s; serialize the handler with a lock. | 01 #1-3, 05 |
| R3 | H | V | entities | **FIXED `8e97d34`** (also the progress `or` from R28). **Empty-string telemetry makes numeric sensors stick at "unavailable" (#121, closed 2026-10-02 but not fixed).** `coerce_numbers` keeps `""`; `KSimpleFieldSensor.native_value` returns it raw; core rejects the state write, so the entity keeps its previous state. The cumulative `_state` keeps the blank until the key is re-sent; a reload clears it. The #121 log shows exactly this for `bedTemp0`/`nozzleTemp`. | `utils.py:23-34`, `sensor.py:288` | Route numeric SPECS fields through `_safe_float` + `isfinite`, map `""` to None. Add a test with a blank frame. Consider reopening #121. | 01 #9, 02 #3 |
| R4 | H | V | config | **FIXED `fe32e9a`** (identity follows the host at setup; zeroconf confirm; a registry already split by the old bug is left alone). **Identity is the host string (#39 closed, not fixed).** Entity unique_ids and the device identifier are `"<host>-..."`; the entry unique_id is the IP. A host change (options Connection page or zeroconf MAC path) orphans every entity and device and recreates them with `_2` ids. Zeroconf has no confirm step, probes port 9999 before the already-configured check, and auto-adds a printer whose DHCP lease changed as a duplicate. The zeroconf MAC branch is dead in production: it reads `bytes` keys, HA gives `str` keys. | `entity.py:23,105,123`; `config_flow.py:137-173`, `:790-802`; `utils.py:176` | Stable id (MAC/hostname or entry_id) with `async_migrate_entry` + `er.async_migrate_entries`; `async_step_reconfigure` with `_abort_if_unique_id_mismatch`; `async_step_zeroconf_confirm`; `_abort_if_unique_id_configured(updates={CONF_HOST: ...})`; fix str keys. | 01 #4-5, 02 #1-2, #27, 05 |
| R5 | H | V | camera | **FIXED `c62af30`** (verified on a real K1C 1.3.5.22: video through go2rtc). Users who chose Auto before must re-select it. **Camera type is detected once and options "Auto" ignores `webrtcSupport` (#46, #60).** Setup caches the type permanently; a first setup with the printer off pins `mjpeg`. Options Auto returns MJPEG for any K1-family or Hi printer before WebRTC is considered and stores the detected value instead of `auto`. Firmware 1.3.5.22 moved K1C/K1 Max to WebRTC; nothing picks it up. | `config_flow.py:316-392`; `__init__.py:289`, `:351-359`; `camera.py:1525` | One shared detection honouring `webrtcSupport`; store `auto` as auto; re-detect when `webrtcSupport` or firmware changes. Split #46: detection (fixable now) vs K1C 2025 payload-type issue (needs an SDP capture). | 01 #10, 02 #4, 05 |
| R6 | H | V | card | **FIXED `f159a65`**. **Printer card rebuild loop and full re-render.** On narrow screens the card fires `ll-rebuild` every ~2 s; HA builds a new element that starts at size 3 again, so Lovelace never sees the bigger size (5 rebuilds per 10 s in the harness). Separately every `hass` assignment rebuilds the whole shadow DOM, re-parses the stylesheet and drops keyboard focus. `test_printer_card_layout.py:25-39` requires the rebuild mechanism. | `k_printer_card.js:895-915`, `:486-499`, `:468-475` | Keep measured size in the module-level map keyed by card id and read it in `getCardSize()`; render once, then patch; skip updates unless a configured entity's state object changed. | 04 #1-2 |
| R7 | H | V | card | **FIXED `f159a65`**. **Non-Latin-1 card names crash the card or silently drop editor edits.** `generateCardId` calls `btoa` on the name; a name containing `č`, an en dash, CJK or an emoji throws. YAML shows an error card; in the editor the throw is inside the debounce, so `config-changed` never fires. Only 16 chars kept, so ids collide. The harness `btoa` never throws, which hid it. | `k_printer_card.js:251-255` (called `:441`, `:2043`) | Drop localStorage theme persistence (config carries the theme) or hash UTF-8 bytes. Make the harness `btoa` spec-compliant. | 04 #3 |
| R8 | H | V | process | **PARTLY FIXED `b32dbd1`** (indented headings). `done` removed from #122 on 2026-10-02 with the owner's approval. Closing free-form issues is the ported policy, not a defect; the closer still trusts any owner comment. **Automation will close valid issues.** (a) #122 has `done` and the owner's "keep this open" as the last comment; `close_done_issues.yml` will close it on its first scheduled run after this lands on `main`. (b) `done` is applied at merge, so issues close before the release exists (#4 would have). (c) Replayed against history, the templateless auto-close would have closed valid #67, #69, #77, #87, #102 because indented headings miss the column-0 regex; #87 was the root-cause report for the 0.9.4 hotfix. | `.github/workflows/close_done_issues.yml`, `close_incomplete_issues.yml`, `issue_validator.yml` | Remove `done` from #122 now; apply `done` at release, not merge; allow leading whitespace in heading regexes; dry-run via `workflow_dispatch` after merging to `main`. | 05 process |
| R9 | H | V | docs | **FIXED `c5a4fa9`, `cba774c`** (with R30). **The diagnostic "response" does not exist.** README, CONTRIBUTING, the bug form, the issue validator and copilot-instructions all tell reporters to paste the `diagnostic_dump` response. The service returns `None` and writes a WARNING log block. The validator then demands something the user cannot produce. | `__init__.py:757`; `README.md:846-895`; `CONTRIBUTING.md:26`; `bug_report.yml:131`; `issue_validator.yml:431-432`; `copilot-instructions.md:135` | Add `diagnostics.py` (R30) and point every doc at "Download diagnostics"; until then, rewrite to "copy the CREALITY DIAGNOSTIC DATA block from Settings > System > Logs". | 06 #1 |

### 7.2 P1 - correctness bugs

| # | Sev | V | Area | Finding | Anchor | Fix | Src |
|---|---|---|---|---|---|---|---|
| R10 | H | V | notify | **FIXED `3059ad0`**. Phantom "0% Starting" live card and false `print_started` after a completed print: progress resets to 0 with the file kept in state 0, the completion latch re-arms and "processing" counts as busy. The existing test checks banners, not the card. | `coordinator.py:976-978`, `:993-1009` | Re-arm only on a running state; assert `_live(payloads) == []`. | 03 #2 |
| R11 | M | V | notify | **FIXED by R10's gate; pinned `39c60df`**. A stop is announced twice ("stopped at 40%" then "0%", two `print_stopped`, a `print_started`) when state 4 arrives with a job-clock reset: `_reset_for_new_job` clears `_notified_stopped` and the legacy branch fires again. | `coordinator.py:938-946`, `:993-1003`, `:1035-1045` | Skip the re-arm on an ENDED_EARLY frame; gate the legacy branch on the watch not being armed. | 03 #3 |
| R12 | M | V | notify | **FIXED `53899a5`** (runout debounce still open, S6). A sticky error code or `materialStatus=1` re-alerts (with snapshot and bus event) at every new print because a file change resets the baseline to 0 (#125 finding 3). Runout has no debounce. | `coordinator.py:950-954`, `:1049`, `:1070` | Baseline to the current code on file change, as priming does; debounce runout. | 03 #4 |
| R13 | M | V | notify | **FIXED `afb1e71`**. `async_call` without `blocking=True`: dismiss-then-banner ordering is not real (a late clear can wipe the completion banner) and the surrounding `except` never sees a failed push. Ordering tests pass vacuously because the stub records synchronously. | `coordinator.py:2111-2115`, `:2209-2211`, `:2245` | `blocking=True` (already in its own task). | 03 #5 |
| R14 | M | V | core | **FIXED `841d544`**. An exception in `_check_notifications` skips G-code requests, the Moonraker poll and `async_update_listeners` for that frame, every frame if it repeats: entities freeze while "available". | `coordinator.py:724` | Wrap like `_flush_pending`. | 01 #8 |
| R15 | M | V | core | **FIXED `14141c4`** (16 MiB). websockets `max_size` defaults to 1 MiB; `retGcodeFileInfo2` is ~150 KiB per ~200 files, so a printer with >~1300 files gets code 1009 disconnects, three per new print. | `ws_client.py:223-227` | `max_size=None` or a generous cap. | 01 #7 |
| R16 | M | V | core | **FIXED `4f6cdb2`**. `socket.gethostbyname` on the event loop on every connect attempt; with a `.local` name and the printer off each retry can block seconds. Also `__init__.py:815`. `test_no_blocking_calls.py` would not catch it. | `ws_client.py:52`, `:176-180`, `:219` | Let websockets resolve, or `loop.getaddrinfo`. | 01 #6 |
| R17 | M | V | camera | **FIXED `7a13f6d`** (reproduced with go2rtc 1.9.14 on the test box). go2rtc stream reuse compares producer URLs exactly; a connected producer reports the bare `http://ip:8000/call/webrtc_local`, so a reload while someone watches deletes a working stream (#88 partly regressed; log on #46 2026-09-09). Test models only the idle form. | `camera.py:849-861` | Normalise both sides (strip scheme prefix and fragment); never delete a stream with an active producer. | 02 #6, 05 |
| R18 | M | V | camera | **FIXED `481f5d1`**. The setup migration strips `localhost:11984` go2rtc options on every setup, before the camera reads them, so the `e322a12` stand-alone go2rtc fix never sees them. | `__init__.py:135-144` (run at `:211`) vs `camera.py:615-618` | Drop that migration branch or gate it on HA's go2rtc being loaded. | 02 #7 |
| R19 | M | V | CFS | **FIXED `56c115a`**. `set_cfs_material` reports success when the write failed (offline printer passes the busy check as "unknown"); the card then shows "Saved". | `__init__.py:642-657`; `k_cfs_card.js:2386-2392` | Raise a translated `HomeAssistantError`; reject unavailable printers. Update `test_a_printer_failure_notifies_and_does_not_raise`. | 02 #5 |
| R20 | M | V | CFS | **FIXED `5e12ef1`** (existing registrations kept). External spool holder without a CFS gets two sensor sets (`cfs_box_0_slot_0_*` and `cfs_external_*`). | `sensor.py:936-946`, `:978-991` | Always skip `type == 1` boxes in the per-box loop. | 02 #8 |
| R21 | M | V | CFS | **FIXED `0c0cc0f`**. A CFS box that appears after the first `boxsInfo` is never created: discovery fires only on a key's first appearance. | `coordinator.py:583-594` | Fire discovery when the set of box/slot ids changes. | 02 #9 |
| R22 | M | V | card | **FIXED `c12883b`**. CFS editor rebuilds on every keystroke (focus lost per character; mode change jumps tabs). | `k_cfs_card.js:2540-2583`, `:2688-2694` | Port the printer editor's build-once, apply-if-changed, 120 ms debounce. Add the echo test. | 04 #4 |
| R23 | M | V | card | **FIXED `bf0330f`** (overlap 176 px / 31 px -> 0 in Chromium). CFS card uses deprecated `getLayoutOptions` with numeric rows and forces `height:auto !important; overflow:visible`, so in sections view it draws over neighbours (#69/#71 shape). | `k_cfs_card.js:2472-2497`, `:536-558` | `getGridOptions()` with `rows: "auto"`; drop the overrides. | 04 #5 |
| R24 | M | V | core | **FIXED `590ef49`** (120 s grace, Repairs issue). A missing or renamed power-switch entity counts as "off" forever: client never starts, every entity unavailable, debug log only. | `coordinator.py:319-321`, `:329-332` | Repair issue; treat missing as "no switch" after a grace period. | 01 #13 |
| R25 | M | V | notify | **FIXED `2983c31`**. "Finishing" shown during warm-up and self-test; "0s" shown as time left. | `coordinator.py:1383-1385`, `:1497-1503` | Treat remaining <= 0 as unknown; pick the status word from the activity state. | 03 #6 |
| R26 | M | V | entities | **NEEDS MAINTAINER DECISION.** The lockstep is in the first commit (`4bbafde`) with no recorded reason; splitting it changes what an existing entity does. Print Tuning writes `setFeedratePct` and `setFlowratePct` in lockstep by design ("keep them in lockstep"), so 150% speed is also a 150% extrusion multiplier. Confirm intent; if unintended it is over-extrusion. | `number.py:149-154` | Separate speed and flow numbers (read-only sensors are already separate). | 02 #10 |
| R27 | M | V | image | **FIXED `9021489`**. Preview image goes stale at job start: `preview_reason` keeps the idle `not_printing`, so the start notification drops the preview and nothing bumps `image_last_updated`. | `image.py:91-95`, `:142`; `coordinator.py:1277-1280` | Reset reason, bump timestamp and clear cache when `printFileName` changes. | 02 #11 |
| R28 | L | V | misc | **FIXED** (all nine): homing `ad9667f`, progress `or` `8e97d34`, soon dismissal `474c2f2`, resources `b29a81d`, `$` patterns `c312d95`, colour fallback `96826cb`, "PLA PLA" `3b198ee`, slot numbering `abd0193`, picker `0eb803e`. Smaller bugs: queued pause flushed while still homing (`coordinator.py:553-559` vs `:492`); progress `printProgress or dProgress` lets a real 0% show a stale value (`sensor.py:286`); finishing-soon dismissal sent even with the option off (`coordinator.py:1120`, `:1134-1140`); legacy `/local/` resource migration builds `...js/?v=1?v=<new>` (`frontend.py:185-197`); `$` patterns in i18n placeholder values (`k_printer_card.js:31-35`, `k_cfs_card.js:35-39`); CFS colour `unknown` skips the `color_hex` fallback (`k_cfs_card.js:1296`, `:1364`); external spool renders "Generic PLA PLA" (#115, `k_cfs_card.js:1606`, `:1661`); slot id 0-based in the success notification (`__init__.py:666`); duplicate `customCards` push. | various | See deep-dives. | 01, 02, 03, 04, 05 |
| R29 | M | S | notify | **OPEN, needs a capture.** #124 has no frames; the owner's K1C could provide one only by starting a print. #124 edge cases: CHANGELOG claims a cancel during self-test is announced once self-test ends, unpinned and false if `withSelfTest` sticks at 1-99; some firmware may report self-test as `state==2` (the mock does), which `derive_print_state` maps to idle; a mid-print state-0 stretch > 15 s (CFS swap) would be announced as a stop; 0 s transition floor lets a flapping printer burn the relay's 500/day budget. | `CHANGELOG.md:17`; `utils.py:522-524`, `:551`; `notification_rules.py:658-668`; `const.py:259` | Needs a frame dump from a Hi/K2 self-test; add the cancel-during-self-test test. | 03 S2-S4 |

### 7.3 P2 - Home Assistant platform practice

| # | Sev | V | Finding | Anchor | Fix | Src |
|---|---|---|---|---|---|---|
| R30 | M | V | **FIXED `c5a4fa9`, `cba774c`**. No diagnostics platform. `diagnostic_dump` logs full telemetry and every entity's attributes at WARNING, unredacted, ignores `include_sensitive_data`, crawls the printer web UI, omits notification config/state. | `__init__.py:754-965`, `:785-797` | `diagnostics.py` with `async_redact_data`, including notification targets, resolved OS and card/watch state; keep the service as a thin wrapper or retire it. | 01 #15, 02 #14, 03 #14 |
| R31 | M | V | **FIXED** (setup no longer waits; on_unload right after start; unload order swapped; NotReady stays for a raising start). Setup blocks HA start ~15 s per offline printer without a switch (up to ~28 s when re-caching); `ConfigEntryNotReady` is unreachable. A failed setup leaves the client task running (no `async_on_unload`). Unload stops the client before platforms. | `__init__.py:227-305`, `:1058-1064` | Drop the waits and rely on late discovery, or raise NotReady deliberately; `entry.async_on_unload(coord.async_stop)` right after start; swap unload order. | 01 #11-12, #20 |
| R32 | M | V | **FIXED** (`send_command`/`KEntity._send`, translated `printer_not_connected`; targets shown only once sent). Entity actions raise bare `RuntimeError` from `send_set_retry`; the home button silently returns when disconnected. | `ws_client.py:520`; `button.py:37-39` | Translated `HomeAssistantError` (`translation_domain`/`translation_key`). | 01 #14, 02 #17 |
| R33 | M | V | **FIXED** in four commits: service errors, `utils` payload errors (`MaterialValueError`) and persistent notifications; sensor text states (slugs + state translations, a raw-state change flagged in CHANGELOG); runout `{state}` and finishing-soon minutes; card default title and picker entries. `test_inline_strings.py` guards raises and notifications. Deliberately left: WebRTC error texts (core's go2rtc sends English too), product names, the `Ns` seconds suffix. Inline English user-visible strings (house-rule violation): service errors (`__init__.py:588-597`, `:617`, `:638-640`, `utils.py:624-697`), persistent notifications (`__init__.py:567-572`, `:649-669`, `:943-948`), sensor literals `not printing`/`N/A`/`External`/`Box N Slot M`/`Unknown` (`sensor.py:563-575`, `:891-894`, `utils.py:421`), WebRTC error texts (`camera.py:966-1143`), untranslated `{state}` slug in bodies (`coordinator.py:1077`, `:1760`), no plurals for "N minutes", card picker names and "3D Printer"/"CFS" in JS. The comment at `__init__.py:193` claiming one inline string is false. `test_translations.py` cannot catch new literals. | listed | `exceptions` keys, ENUM state translations, map `{state}` through `print_status` translations; add a lint-style test that flags string literals in `raise`/`persistent_notification` calls. | 01 #14, 02 #12, 03 #10-12, 04 #19 |
| R34 | L | V | **PARTLY FIXED**: actions registered in `async_setup`, `request_cfs_info` schema, no-entry tolerance. `runtime_data` deferred: no user-visible effect, and it touches every platform and most test stubs. `hass.data[DOMAIN]` instead of `entry.runtime_data`; services registered in `async_setup_entry`, never removed, `request_cfs_info` has no schema. | `__init__.py:412`, `:498-502`, `:742-751` | Register in `async_setup`; raise when no entry is loaded. | 01 #16, 02 #24 |
| R35 | L | V | **FIXED** apart from the reconfigure step: the options host is checked (reachable, not another entry's), device removal refuses the live device and no longer wipes the cache; zeroconf double reload was R25. A separate reconfigure step is not added: the host already lives on the Connection page users know, and two places for one setting would confuse more than it helps. No reconfigure step; options flow writes `entry.data` through `async_update_entry` without connectivity or uniqueness check and never ends with `async_create_entry`; every `entry.data` write reloads (device removal re-creates the device; zeroconf MAC path reloads twice). | `config_flow.py:285-308`, `:794-799`; `__init__.py:1095-1104` | Reconfigure step; options-only writes; avoid data writes on removal. | 01 #25, 02 #16 |
| R36 | L | V | **PARTLY FIXED**: print status, filament status and print control are ENUM sensors (an enum's state is always an option or None), print control, max temps and reconnect are DIAGNOSTIC, position X/Y/Z are off for new installs. Left, with reasons: `icons.json` and EntityDescription (no user-visible effect), `used_material_length` units (changing them raises a statistics unit-change repair for every user), `filament_status` as a binary sensor (breaking), PARALLEL_UPDATES (the default already applies). Entity modelling: no `icons.json`; no EntityDescription; `print_status`/`filament_status`/`print_control` should be ENUM; missing `entity_category` on diagnostics, max temps, reconnect; `used_material_length` converts mm to cm by hand with MEASUREMENT; `KMappedSensor` returns the string `"unknown"`; no `PARALLEL_UPDATES`; position X/Y/Z enabled by default (recorder load); `filament_status` fits a PROBLEM binary_sensor. | `sensor.py:320-329`, `:390-405`; platforms | Per deep-dive. | 02 #22-24, 01 #17 |
| R37 | L | V | **FIXED**: `integration_type: device`, `loggers` = websockets + go2rtc_client (the own package is implicit), version from `async_get_integration`. Manifest: no `integration_type: device`; `loggers` lists the integration's own package instead of `websockets`; version read from the manifest by hand instead of `async_get_integration`. | `manifest.json:11`; `__init__.py:88-100` | Fix keys. | 01 #17, #28 |
| R38 | L | V | **FIXED**: HA's proxy helper, CancelledError propagates, `?action=snapshot` first, no dialling an unreachable printer (MJPEG and go2rtc snapshots); `test_camera_mjpeg.py` runs it against a real aiohttp server. MJPEG camera is hand-rolled: swallows `CancelledError`, proxy uses `timeout=None` so a stalled printer holds a request forever; snapshots open a full stream. WebRTC snapshots keep dialling the printer while it is off (WARNING per call, seen in #121 log). | `camera.py:195`, `:282`, `:303`, `:706-774` | `async_aiohttp_proxy_web` + `sock_read` timeout; `?action=snapshot`; return last frame when unavailable. | 02 #13, #25 |
| R39 | L | V/S | **PARTLY FIXED**: client stopped on `EVENT_HOMEASSISTANT_STOP` (clean close at shutdown), card-registration filesystem work moved to the executor, suspected ICE camelCase bug confirmed and fixed (bundle-aware). Left, with reasons: the client's own tasks are referenced and cancelled in `stop()`; the 5 Hz ticker only checks timers; leftover go2rtc streams for an old address are inert and vanish with go2rtc's restart; `hass.data["go2rtc"]` is private but has no public alternative and is wrapped in error handling. Untracked background work: bare `asyncio.create_task` in the client, `_log_material_echo` via `hass.async_create_task`, 5 Hz GET ticker; go2rtc streams never deleted on removal or host change; card registration does blocking `stat`/`unlink` on the loop each setup. Suspected: reads `hass.data["go2rtc"]` private fields; direct-mode ICE filter reads camelCase attrs that HA exposes as snake_case. | `ws_client.py:107`, `:239-240`, `:457-493`; `__init__.py:670`; `frontend.py:238-281`; `camera.py:664-680`, `:1254-1258` | `entry.async_create_background_task`; executor for fs; stream cleanup on unload/remove. | 01 #28, 02 #29, 04 #21 |
| R40 | L | V | Device firmware version never refreshes after a printer update (cache refreshed only on integration version change); number maxima read once in `__init__`; max-temp sensors have no late discovery. | `__init__.py:245-320`; `number.py:170-172`, `:209-211`, `:248-259`; `sensor.py:1193-1199` | Refresh cache from live telemetry on change. | 01 #19, 02 #19 |
| R41 | L | S | `homeassistant.update_entity` raises `NotImplementedError` (no `_async_update_data`). Moonraker query `?objects=temperature_fan%20chamber_fan` may not match Moonraker's form, so K2 Base chamber target may never update. K2 Base latch can stick False if `F021` arrives late. State codes 2/3 unmapped. | `coordinator.py:150-156`, `:700-703`; `const.py:321`; `utils.py:540-552` | Verify with curl/hardware. | 01 suspected |

### 7.4 P2 - frontend quality

| # | Sev | V | Finding | Anchor | Fix | Src |
|---|---|---|---|---|---|---|
| R42 | M | V | Accessibility: printer chips `outline:none` with no `:focus-visible`; off-state chips 2.26:1 on dark themes; CFS text fails on light themes (1.73:1 temperature); clickable divs without role/tabindex/keys; toast lacks `aria-live`; dialog has no focus trap; pulse ignores `prefers-reduced-motion`; editor tabs not focusable. | `k_printer_card.js:596`, `:346-372`, `:1669-1670`; `k_cfs_card.js:409-411`, `:736-742`, `:862`, `:1986-2072`, `:2436-2447` | Theme variables, `:focus-visible`, native `<dialog>.showModal()`, `role="button"`. | 04 #9-12 |
| R43 | M | V | CFS card has no device picker: users fill 51 entity pickers by hand although the sensors carry `box_id`/`slot_id` attributes. | `k_cfs_card.js:2598-2655` | Reuse `entitiesForDevice`. | 04 #13 |
| R44 | L | V | Editors persist every default into dashboard YAML (CFS 62 keys, printer 28 + 23 theme), so later default changes never reach existing cards. | `k_printer_card.js:2039-2049`; `k_cfs_card.js:2688-2694` | Strip values equal to defaults. | 04 #14 |
| R45 | L | V | i18n fetches: missing-locale 404 not cached (with R6, a `cs`/`de` user requests a 404 every ~2 s); no `?v=` on i18n files; each card fetches `en.json` separately. | `k_printer_card.js:7-19`; `k_cfs_card.js:11-23`; `frontend.py:280-289` | Cache misses; append the module's `v`. | 04 #7 |
| R46 | L | V/S | Misc: custom fan/cover chips only turn on, automation chip enables rather than triggers; theme values and icon overrides unescaped into `innerHTML` (admin-only); dead `_resolveLanguage` x4, `rename()`, CSS rules, i18n keys; `www/ha_creality_ws.code-workspace` shipped to users; `getStubConfig` ignores `hass`; card stores `device` but never uses it. Suspected: CFS dialog under other cards (`z-index:1`); presets undeletable on iOS (right-click only); `confirm()` suppressed in kiosk webviews silently skips Stop. Only two locales ship (en, es). | `k_printer_card.js:513-538`, `:754-763`, `:1110-1113`, `:1146` | Per deep-dive. | 04 #15-24 |

### 7.5 P3 - tests and tooling

| # | Sev | V | Finding | Anchor | Fix | Src |
|---|---|---|---|---|---|---|
| R47 | H | V | **No real Home Assistant in any test.** `conftest.py` stubs HA; setup/unload/reload, config flow user/zeroconf, camera decision tree, MJPEG, light, buttons, image and number setters never run. The `KClient` stub has `is_connected` always False, which makes R2 untestable. This is why lifecycle bugs (R2, R4, R18, R31) ship green. | `tools/tests/conftest.py:461-498` | Add a `pytest-homeassistant-custom-component` job on Python 3.14 for lifecycle and config-flow tests; consider porting washdata's `devtools/testbox` as `tools/testbox`. | 06 #2 |
| R48 | M | V | Fixtures are invented, not captured. Detection tests never use the real `"K1"` model string; the mock serves MJPEG at `:8000/stream.mjpeg` while the integration reads `:8080/?action=stream`; no CFS, Moonraker, preview or K1C 2025 in the mock. 26 closed issues are fixed but untested (notably #99 CFS first-poll timers, which already regressed once; #45; #53; #84; light #29/#102). | `tools/creality_printer_test_server.py:1226`, `:1917`; `const.py:20,27` | Build a `tools/tests/fixtures/` corpus of real frames per model from issue logs and diagnostics; replay power-cycle and boot traces with a fake clock; one regression test per closed bug. | 05, 06 #14 |
| R49 | M | V | CI Python 3.11/3.13 while HA 2026.7 requires >= 3.14.2; `.venv` is 3.11. Node 20 (EOL 2026-04-30) and node20-runtime action pins; github-script pin comment says v7.0.1 but the SHA is v7.1.0. No `timeout-minutes`, unpinned test deps, `tools/requirements.txt` unused and wrong. | `.github/workflows/tests.yml:28-43`; `release.yml:33-35` | Matrix `["3.14"]`; rebuild `.venv`; Node 22/24; `requirements-dev.txt`. | 06 #3, #23-24 |
| R50 | M | V | Test quality: three JS runners not awaited and the pytest wrapper checks only exit code (a hung test exits 0); order dependence between `test_ws_client_reconnect.py` and `test_cfs_simulator.py` (passes only alphabetically), `test_options_flow.py` breaks under shuffle; `test_gcode_file_info.py:453-476` passes with its bug reintroduced; regex-on-source tests for layout/editor/install; node missing silently skips 32 tests; cloud-URL hygiene test allows any `http://` and scans only `.py`; `test_utils.py:244` name says "error", asserts `idle`; `get_event_loop_policy` in 8 files (270 warnings on 3.14). | `js/test_printer_editor.mjs:472`; `js/test_printer_migration.mjs:188`; `js/test_printer_telemetry.mjs:297`; `test_cfs_card.py:29-32`, `:77-90`; `test_code_hygiene.py:10`, `:45-53` | `await run()` and check the "N passed" line; fixture-scoped stubs; drive the real `KClient` with a fake socket; fail on missing node when `CI` is set. | 06 #8-9, #26-29 |
| R51 | L | V | No lint. A `ruff check --isolated --select F,E9 custom_components` step has zero findings today and would catch undefined names in the 0%-covered modules. No formatter (CRLF files). No guard on line endings. | n/a | Add the step; add a test pinning each file's EOL, or one deliberate normalisation commit. | 06 #25, #32 |

### 7.6 P3 - process, repo, docs

| # | Sev | V | Finding | Anchor | Fix | Src |
|---|---|---|---|---|---|---|
| R52 | M | V | `main` is unprotected; nothing must be green to merge or tag. Release tags are not checked for being on `main`; v0.9.8 was created in the UI, so `release_check` ran after it was public. | repo settings; `release.yml:65-106` | Protect `main` (Tests, hassfest, HACS); `git merge-base --is-ancestor` in `release.yml`; always push the tag. | 06 #4, #19 |
| R53 | M | V | Ported automation is live only after merging to `main` (GitHub reads forms, templates, `dependabot.yml`, schedule and `pull_request_target` workflows from the default branch); live repo has blank issues enabled and no PR template. Also inherited from washdata: logs textarea with `render:` cannot take attachments; "passes automatically" in `validate_pr` is false; any `#N` counts as an issue link; `release_references` treats every `#N` in notes as fixed. | `bug_report.yml:96-102`, `:141`; `validate_pr.yml:214`, `:293`; `close_incomplete_prs.yml:95` | Dry-run after merge; add `webrtc_direct`/`custom` to the camera dropdown; closing-keyword links. | 06 #5, #13, #16-18 |
| R54 | M | V | Junk tags are local-only, fetched from the `RobertJansen1` fork remote (no `tagOpt`): `v0.00`, `v0.98` = 0.9.6.1 release commit; `v0.01`, `v0.1`-`v0.5`, `v0.9`, `v0.95`-`v0.99` = later fork commits whose manifest still says 0.9.6.1; `v0.4.7`, `v0.5.0` mislabelled (0.4.5 / 0.4.7). A `git push --tags` would publish them and fire `release.yml` up to 15 times. Local `main` is 111 commits behind `origin/main` (true divergence: 2 ahead, 1 behind). | local refs | `git config remote.RobertJansen1.tagOpt --no-tags`; delete locally (maintainer's call); compare against `origin/main`. | 06 #6, #21 |
| R55 | M | V | Plaintext long-lived HA token in `tools/test_files/deploy_to_ha.sh:17-18` (gitignored, never committed). | same | Read from env or a file outside the repo; rotate if the tree was ever shared. | 06 #7 |
| R56 | L | V | Issue triage: first-response median rose from 5.3 h (Oct 2025 to Jan 2026) to ~21 days (since Feb 2026); 8 reporter issues have no maintainer comment; only 12 of 64 closed issues link a commit or PR; six closed as "completed" were not fixed (#24, #33, #34, #47, #67, #76); #121 closed without reporter confirmation while R3 remains. #93 (grams) is now derivable from printer-reported slicer weight/length for single-material files; worth revisiting. | GitHub | Close via PR keywords; "not planned" for unfixed; reopen or re-scope #121. | 05 |
| R57 | L | V | Version bumps and CHANGELOG renames hidden in fix commits (`df19ff3`; `9660889`/`b2ac2ea` flip 0.9.9 -> 0.9.8 -> 0.9.9). | git history | Bump in its own `chore:` commit. | 06 #20 |
| R58 | L | V | Repo hygiene: `backups/` holds 215 copies (75 MB) inside the repo; `conductor/` is tracked although `.gitignore` lists it, and is stale (tells agents to commit, pylint, 80 columns; only track is #76, closed 2026-03-11); `.agent/` is untracked scaffolding; no generic `__pycache__/` rule; `.claude/settings.local.json` ignored only by the global gitignore; `webrtc_test_server.sh` at root; ~15 stale local branches; `origin` uses the redirected `ha-creality-ws` URL; `tools/test_files/internal_docs` (Nov 2025) stale. | `.gitignore`; tree | `git rm -r --cached conductor`; move `backups/` out; fix `.gitignore`. | 06 #10, #31 |
| R59 | L | V | Stale Claude tooling: `.claude/skills/coderabbit-loop` baseline numbers (~3 s, 5 skipped) are wrong (~10 s, 0 skipped in `.venv`, 6 in CI-like venv), and the skill reads skip counts as a signal. | `SKILL.md:117-132`; `mechanics.md:319-334` | Update. | 06 #30 |
| R61 | M | V | README drift: missing `webrtc_direct`/`custom` camera modes; says K1C 2025 camera unsupported; says power-off zeroes entities (they go unavailable); YAML resources omit `k_cfs_card.js`; light shown as `switch.`; wrong entity ids in the card example; Pause shown for nonexistent `resuming`/`pausing`; Hi camera listed as MJPEG while users report WebRTC (#60); "no sensitive data" claim for a dump that includes IPs and hostnames. | `README.md:98-101`, `:165-173`, `:370`, `:767-770`, `:798`, `:813`, `:880` | Fix each line. | 04 #23, 06 #12 |
| R62 | M | V | `copilot-instructions.md` (treated as ground truth by review bots) claims power-off zeroes entities, K1 SE is outside the K1 family, chamber control is K2 Pro/Plus only, Power chip is CSS-pinned, and keeps a stale 2025-11-12 section; the pinning test guards three phrases only. | `.github/copilot-instructions.md:34-75`, `:152-165` | Trim to pointers into `CLAUDE.md` and `docs/internal`. | 06 #11 |
| R63 | M | V | `tools/README.md` documents `./tools/deploy_to_ha.sh`; it is `tools/test_files/deploy_to_ha.sh` and gitignored. | `tools/README.md:136-200` | Fix path; mark maintainer-local. | 06 #15 |
| R64 | L | V | Stale comments and docs in code: `ws_client.py:192-205` (power-check comment, "sleeping 60s" while it sleeps 10 s); `__init__.py:231` "~5 retries"; `coordinator.py:409-419` thread-safe leftover; `coordinator.py:1516-1518` "any options change reloads"; `coordinator.py:2164` "~20 per print" (~100); stringify rationale names the wrong offender (`actions[].authenticationRequired` was the real one); CFS comments claim admin-only WS calls; `frontend.py:213-227` "resources never touched"; typo "Lovlace" `frontend.py:118`; dead mDNS fallback logging WARNING every ~5 min (`ws_client.py:348-367`). | listed | Correct in passing. | 01 #22-23, #34, 03 #15, 04 #21-22 |
| R65 | L | V | Dead code: `_should_zero()` is the negation of `available` (HA never reads an unavailable entity's state); `camera.py:1425-1459` duplicate probe; `_last_error` never set; `GO2RTC_CLIENT_AVAILABLE` fallback; `fan.py:27` `_attr_percentage_step`; unused `name` placeholders and SPECS `name` fields; unused `config.error.not_K`. | `entity.py:35-41` | Remove. | 02 #18, #26 |
| R66 | L | V | `coerce_numbers` over-coerces (`"007"` -> 7); model substrings `"hi"` and `"k1"` are over-broad; two clocks mixed (`hass.loop.time()` vs `time.monotonic()`); `ensure_connected` true during a 300 s backoff sleep. | `utils.py:23-34`, `:267-280`; `coordinator.py:336-345`, `:423`; `ws_client.py:94-96`, `:386` | Per deep-dive. | 01 #26-27, #29-30 |
| R67 | L | V | CHANGELOG: claims SemVer with 4-part versions; history starts at 0.7.0; 0.7.0/0.7.1 dates disagree with releases (2025-12-10, 2025-12-17); milestone links for 0.7.1 and 0.9.0-0.9.7 point at nonexistent milestones; six links missing `)` (`CHANGELOG.md:295,303,319,344,366,375`); 0.7.0-0.9.2 cite no issue numbers. | `CHANGELOG.md` | Fix. | 05, 06 #22 |

### 7.7 Product gaps (not bugs; record for planning)

| # | Finding | Src |
|---|---|---|
| R68 | No way to retire an iOS Live Activity (no action buttons there; a swipe drops the token and the next push recreates it). Hide is global across phones. Action ids are per printer, not per job, and Stop is sent regardless of state, so a lingering notification can stop a later print. | 03 #7-9 |
| R69 | No power-loss notification (#43); pause/resume/stop/light buttons cannot be hidden on the card (#37); `realTimeSpeed` streamed but not exposed; fans created on every model; `mjpeg_optional` models get a 1x1 white camera. | 02, 05 |
| R70 | #122 remainder needs a multi-colour K2+CFS capture; per-material weights (`filamentWeight` comma list) are printer-reported and within policy once the delimiter is confirmed. #125 part 2 (no card even with native types) unexplained: check `.storage/mobile_app` `live_activity_tokens` for the fixed tag `ha_creality_ws_<entry>_live`; mitigation is a `clear_notification` on `_live` to Apple targets before a job's first START. | 03, 05 |

### 7.8 Found while fixing P0

| # | Sev | Finding | Src |
|---|---|---|---|
| R71 | L | A K1C exposes a "Chamber Target" number although it has no chamber heater: the setup cache promotes chamber control whenever telemetry carries `targetBoxTemp`, and the K1C sends it (seen on the real K1C at 192.168.0.90). | `__init__.py` "Feature Promotion" |
| R72 | L | The go2rtc stream is named `creality_k2_<ip>` for every printer, K1C included. Cosmetic, but it appears in the camera's attributes and in go2rtc's UI. | `camera.py` |
| R73 | M | On the test box every iOS live-card push is a Live Activity START, because no app registers an activity token; on a real phone the first START is followed by UPDATEs. Relevant to #125 part 2 (R70): if a START is lost or the stored token is stale, nothing recovers. | test box, core `live_activity/__init__.py` |
| R74 | L | `close_done_issues.yml` is identical in ha_washdata, so the "owner comment is last" hazard (R8) exists there too. Outside this repo. | `/root/ha_washdata/.github/workflows/close_done_issues.yml` |
| R75 | L | The test box's mock printer serves MJPEG on `:8000/stream.mjpeg` while the integration reads `:8080/?action=stream` (R48), so the MJPEG camera path is untested there. | `tools/creality_printer_test_server.py` |
| R76 | L | Every save in the CFS card leaves two notifications in the bell: `set_cfs_material` posts "CFS material changed" and the card's follow-up `request_cfs_info` posts "CFS information request". The card already shows its own toast, and the action now raises on failure (R19), so the success notifications only duplicate it. Found while translating them (R33); behaviour kept, needs a decision: drop the success notifications, or keep them only for calls that do not come from the card. | `__init__.py` `request_cfs_info`, `set_cfs_material`; `k_cfs_card.js` save path |

## 8. Index of deep-dive files

| File | Covers |
|---|---|
| [01-protocol-and-core.md](reference/01-protocol-and-core.md) | Wire protocol, `KClient` state machine, coordinator frame path, availability and power switch, print state derivation, capability cache, setup/unload, services, zeroconf, timers, traps. |
| [02-entities-camera-config.md](reference/02-entities-camera-config.md) | Entity catalog, onboarding cache, every platform, camera classes and endpoints per model, camera decision tree, config and options flow, services, translation coverage. |
| [03-notifications.md](reference/03-notifications.md) | Per-frame pipeline, priming, job-cycle detection, event catalogue, targets and wire shaping, payload per platform, actions, language, options, test map, known defects. |
| [04-frontend-cards.md](reference/04-frontend-cards.md) | `frontend.py` serving and resources, shared i18n machinery, both cards' config schema, render pipeline, sizing, editors, CFS dialog, traps, README drift. |
| [05-issue-history.md](reference/05-issue-history.md) | All 68 issues classified with fix/test locations, open-issue analysis, theme clusters, process observations. |
| [06-process-ci-tests-docs.md](reference/06-process-ci-tests-docs.md) | Workflow map, release flow, tags and branches, test suite map and coverage, simulator, docs map, hygiene inventory. |
