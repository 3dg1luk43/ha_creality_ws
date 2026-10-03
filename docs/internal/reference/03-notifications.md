# Technical Reference: the notification subsystem

**ha_creality_ws - Internal Engineering Reference**
Written: 2026-10-02 | Branch: 0.9.9 | HEAD: `da0295a`

Paths without a directory are under `custom_components/ha_creality_ws/`. Line numbers are against `da0295a`. External code is cited by repository, commit and path; it is what this integration's payloads actually land in, and several traps below only make sense once you have read it.

---

## 1. Overview

The subsystem turns the printer's WebSocket telemetry into phone notifications and bus events. It is split three ways:

| Layer | File | Role |
|---|---|---|
| Pure rules | `notification_rules.py` | Decisions and payload construction. Imports nothing from Home Assistant (`notification_rules.py:1-21`), so it is tested with no stubs (`tools/tests/test_notification_rules.py:1-5`). |
| Wiring | `coordinator.py` | Per-frame state machine, translation lookup, media/link resolution, fan-out and delivery (`coordinator.py:849-1220`, `1259-2248`). |
| Entry glue | `__init__.py`, `config_flow.py` | Action-event listener, periodic tick, options apply/reload, entry removal (`__init__.py:424-456`, `971-1053`); the options page (`config_flow.py:185-205`, `560-729`). |

House rules that shape the code:

- No user-visible text in `notification_rules.py`; every word comes from `strings.json` `common` and is passed in resolved (`notification_rules.py:17-20`, `coordinator.py:1656-1717`).
- `const.py` must stay import-free, which is why option coercion lives in `notification_rules.py` (`notification_rules.py:14-16`).
- Sends are never awaited on the WebSocket path: `ws_client` awaits the frame handler inline (`ws_client.py:284`), so every delivery is a fire-and-forget task (`coordinator.py:2117-2156`, `2073-2097`). Inside that task the service call itself is also non-blocking (trap 4).

---

## 2. The per-frame pipeline

Every telemetry frame goes through `_handle_message` (`coordinator.py:691-746`):

1. Merge the frame into `self.data` (`coordinator.py:710`). Merge, not replace: a field the frame does not carry keeps its last value.
2. Recompute the paused flag from `state == 5` or the pause fields (`coordinator.py:468-474`, called at `712`).
3. Derive the job state once (`coordinator.py:715`) and flush any queued pause/resume (`coordinator.py:719`).
4. `await self._check_notifications(payload)` (`coordinator.py:724`). Everything in sections 4 to 6 happens here.
5. Listener updates are throttled while busy (`coordinator.py:740-746`); notification logic is not throttled.

Inside `_check_notifications` (`coordinator.py:849-1140`), in order:

| Step | Lines | What |
|---|---|---|
| a | `852-859` | Read `printFileName`; progress is `printProgress`, falling back to `dProgress` only when it is `None` (explicit `None` check, not `or`). |
| b | `865-871` | Priming (section 4). Returns early until primed. |
| c | `879-884` | `deliver = bool(targets) and strings loaded`. Detection below is unconditional; only sends are gated on `deliver`. |
| d | `896-903` | Job clock: `job_restarted` when `printJobTime` went backwards. |
| e | `917-936` | Stop watch (section 5.3). Reset on a new non-empty file name and on every frame after completion was announced. |
| f | `938-946` | `RESTARTED` re-arms the job; `ENDED_EARLY` announces a stop (section 6.3). |
| g | `950-962` | File name changed: reset per-job latches, baseline completion off the visible progress, `_reset_for_new_job`. |
| h | `964-965` | No file name: return. Nothing below runs without a job name. |
| i | `976-978` | `print_started` bus event on the first frame of a job with progress below 100. |
| j | `993-1003` | Same-file re-arm via `is_new_job_cycle` (section 5.2). |
| k | `1008-1009` | Live card (section 6.1), only when `deliver`. Runs before the one-shot events so a terminal banner lands after the card is retired. |
| l | `1022-1028` | Completion. |
| m | `1035-1045` | Stop by `state == 4` when the watch did not already announce it. |
| n | `1048-1060` | Error. |
| o | `1064-1084` | Filament runout. |
| p | `1091-1098` | Alert dismissal once error and runout have both cleared. |
| q | `1102-1123` | Finishing soon. |
| r | `1131-1140` | Settle owed dismissals for the card and the reminder. |

---

## 3. Inputs: the derived state

### 3.1 `derive_print_state` (`utils.py:494-552`)

Priority order, first match wins:

| Condition | Result | Lines |
|---|---|---|
| power switch off | `off` | `utils.py:508-509` |
| no frame for `STALE_AFTER_SECS` (15 s, `const.py:48`) | `unknown` | `utils.py:510-511`, `coordinator.py:420-422` |
| `err.errcode != 0` | `error` | `utils.py:518-520` |
| `1 <= withSelfTest <= 99` | `self-testing` | `utils.py:522-524` |
| file name and progress >= 100 | `completed` | `utils.py:540-541` |
| file name and (`state == 5` or paused flag) | `paused` | `utils.py:542-543` |
| file name and `state == 4` | `stopped` | `utils.py:544-545` |
| file name and `state == 1` | `printing` | `utils.py:546-547` |
| file name and `state == 0` | `processing` | `utils.py:548-549` |
| anything else (including `state` 2 or 3 below 100%) | `idle` | `utils.py:551` |

`self-testing` outranks `stopped`, a cleared file name and 100%: while `withSelfTest` is mid-range nothing else about the job is visible.

### 3.2 Activity state

`derive_activity_state` re-derives with `err` blanked when the first pass said `error` (`utils.py:555-585`), so a code the printer never clears cannot pin the job to `error`. The coordinator's `_job_state` and the live snapshot both use it (`coordinator.py:446-466`, `1316-1333`). The status sensor still shows `error`.

### 3.3 Two state sets that are deliberately different

| Set | Members | Used for | Where |
|---|---|---|---|
| `BUSY_PRINT_STATES` | printing, paused, processing, self-testing | live card `job_active`, pause queueing, prime adopt-clear | `utils.py:486`, `coordinator.py:1327`, `831` |
| `RUNNING_JOB_STATES` | printing, paused | arming the stop watch | `notification_rules.py:533` |
| `UNOBSERVABLE_JOB_STATES` | unknown, off | stop watch ignores the frame and clears its timer | `notification_rules.py:538`, `626-632` |

`processing` is both pre-print warm-up and what a cancelled job idles in (`notification_rules.py:527-532`), which is why the watch never arms on it.

---

## 4. Priming (issue #112)

The printer keeps reporting the last job's file and 100% indefinitely (`coordinator.py:765-769`). Until primed, frames only build a baseline:

- Prime on the first frame carrying both a file name and a progress, or after `NOTIFY_PRIME_GRACE_SECS` = 10 s (`const.py:128`, `coordinator.py:865-871`).
- `_prime_notification_state` (`coordinator.py:762-847`) marks already-true conditions as already notified: completion if progress >= 100 (`785`), the current error code (`787-790`), runout if `materialStatus == 1` (`792-795`), finishing-soon if already inside the window (`797-800`), started always (`807`), stopped if the state is `stopped` (`808-814`).
- The live card is baselined (`820`) and, when the printer is not busy and the card is enabled, a dismiss sentinel is sent for the live tag (`831-836`, kind `live:clear:adopt`), because `card_active` is in memory and a card from before the restart would otherwise never be removed (`822-830`).
- Pinned by `tools/tests/test_notifications.py:84-174` (startup baseline, grace window, stale error).

---

## 5. Job-cycle detection

### 5.1 File name change (`coordinator.py:950-962`)

Any change of `printFileName`, including to empty: resets finishing-soon and runout latches, sets `_last_error_code = 0`, baselines `_notified_completed = progress >= 100` (the first frame of a new job usually still carries the old 100), and calls `_reset_for_new_job` (`coordinator.py:1201-1220`), which dismisses any active card before clearing its state, then re-arms started, stopped and finishing-soon.

### 5.2 `is_new_job_cycle` (`notification_rules.py:472-509`)

Used for a same-file reprint (`coordinator.py:993-1003`). False until an ending was observed (`ended_at_completion` or `ended_early`); false while progress >= 100; true on a job-clock restart; otherwise true only after a completion with progress <= `NOTIFY_REARM_PROGRESS_MAX` = 90 (`const.py:136`). The band exists because the printer reports 100, then 99 once more, then finishes (`notification_rules.py:493-498`).

### 5.3 `JobEndWatch` (`notification_rules.py:548-668`)

The printer reports state, not events, and a stop from its screen, the Creality app or Home Assistant all look like `state` 0 with the file kept and progress reset (`notification_rules.py:557-564`). The watch remembers the last running frame's `job_name` and `progress` (`notification_rules.py:566-569`, `616-619`) because both are gone by the frame that reveals the stop.

`observe()` decision table (`notification_rules.py:592-668`):

| Frame state | Watch condition | Result |
|---|---|---|
| printing / paused | was `ended` | reset, arm, return `RESTARTED` (`606-621`) |
| printing / paused | otherwise | arm, record name/progress, clear timer, `None` |
| any other | not armed, or already ended | `None` (`623-624`) |
| unknown / off | armed | clear timer, `None` (`626-632`) |
| self-testing | armed | clear timer, `None` (`634-641`, the #124 fix) |
| progress >= 100 | armed | clear timer, `None` (`643-648`) |
| stopped, or file name empty | armed | `ENDED_EARLY` immediately (`653-656`) |
| idle / processing / error / other | armed | start timer; `ENDED_EARLY` once it has persisted `NOTIFY_END_CONFIRM_SECS` = 15 s (`658-668`, `const.py:148`) |

The confirmation is measured between frames; there is no timer task, so a printer that goes silent cannot be declared stopped (`tools/tests/test_notification_live.py:1223-1234`).

While a confirmation is pending the live card is frozen (`coordinator.py:1399-1407`) so a cancelled job's last card is not a 0% refresh.

---

## 6. Event catalogue

Common facts: the title is always `_notify_title()`, the printer `hostname`, else `model`, else host (`coordinator.py:1815-1834`). Tags are `ha_creality_ws_<entry_id>_<suffix>` (`notification_rules.py:396-414`, `coordinator.py:1924-1932`), sanitised to `[A-Za-z0-9_-]{1,64}` (`notification_rules.py:54-55`, `390-393`). `group` is the tag base on every push (`coordinator.py:1473`, `1953`, `1973`, `1994`).

### 6.1 Live card

| | |
|---|---|
| Option | `notify_live` (`coordinator.py:1396-1397`) |
| Tag / channel | `_live` / `channel_live` (`coordinator.py:1464`, `1470`) |
| Builder | `build_live_payload` (`notification_rules.py:951-1080`) |
| Body | custom `live` template, else `body_progress` or `body_paused`, `body_layer`, `body_time_left` joined by `" - "` (`coordinator.py:1351-1388`, `const.py:296`) |
| Gate | `deliver`, not pending a stop, `job_active` (`coordinator.py:1008-1009`, `1399-1438`) |
| Dispatch | `live_only=True`: mobile targets only, macOS skipped (`coordinator.py:1486-1488`, `2139-2154`, `notification_rules.py:115-126`) |

`LiveCardState.decide()` (`notification_rules.py:733-797`), first match wins:

| Order | Condition | Reason |
|---|---|---|
| 1 | `job_finished` latch | none (`737-743`) |
| 2 | not `job_active` | none (`744-745`) |
| 3 | `pushes_this_job >= 600` | none (`746-747`, `const.py:263`) |
| 4 | no card active | `START` (`748-752`) |
| 5 | activity state changed | `TRANSITION`, floor 0 s (`756-764`, `const.py:259`) |
| 6 | less than 30 s since last push | none (`766-767`, `const.py:253`) |
| 7 | progress crossed a milestone (step 1%) | `MILESTONE` (`769-772`, `const.py:239`) |
| 8 | 300 s since last push and progress or deadline changed | `REFRESH` (`774-785`, `const.py:250`) |
| 9 | countdown reached its deadline once | `OVERRUN` (`790-795`) |

Phase: paused, else `start` for a `START` push, else `printing` (`coordinator.py:1454-1460`). `refresh` is `card_active` (`coordinator.py:1481`). After 8 h (`const.py:269`) `live_update=False` and the push degrades to a plain tagged notification (`coordinator.py:1451`, `1472`; `notification_rules.py:727-731`). Status text when there is no countdown: `status_paused`, `status_starting` for phase start, else `status_finishing` (`coordinator.py:1497-1503`).

Retirement:

- Not busy any more: state retired, dismissal owed, sent at frame end unless a terminal banner cancels the debt (`coordinator.py:1409-1438`, `1131-1133`, `2008`). Only `progress >= 100` sets `job_finished` here (`coordinator.py:1422`).
- Stop: `_announce_early_end` finishes the card, silently if a banner replaces it (`coordinator.py:1164-1173`).
- No telemetry for 90 s: `notifier_tick` dismisses (`coordinator.py:1638-1654`, `const.py:271`), driven every 5 s by `_interval_check` (`__init__.py:444-456`).
- Hide button: dismiss and `finish()` (`coordinator.py:1614-1620`).
- Options: switched off or target removed (`coordinator.py:1836-1878`); text changed, resync in place (`coordinator.py:1901-1922`).

### 6.2 Completed

| | |
|---|---|
| Trigger | `progress >= 100` and not `_notified_completed` (`coordinator.py:1022`) |
| Option | `notify_completed` (`coordinator.py:1023`) |
| Text | `completed` template, else `completed_detailed` when `usedMaterialLength` is real, else `completed` (`coordinator.py:2014-2037`) |
| Payload | `build_event_payload(kind=completed, tag=_live, channel_finished, ends_activity=True)`, preview plus snapshot (`coordinator.py:1988-1998`) |
| Delivery | `_replace_card_with`: per live-capable mobile target one task that sends the dismiss sentinel then the banner (`coordinator.py:2073-2115`). The order is only an order of *scheduling*: see trap 4 |
| Latch | `_notified_completed = True` regardless of option (`coordinator.py:1028`); re-armed by sections 5.1 and 5.2 |
| Bus | `ha_creality_ws_print_finished` (`coordinator.py:1027`) |

### 6.3 Stopped

Two paths, both gated on `notify_completed` and both firing `ha_creality_ws_print_stopped`:

- Watch path (`coordinator.py:945-946`, `1142-1199`): text uses the watch's remembered name and progress, `stopped` template fields are `device, filename, progress, nozzle, bed` (`notification_rules.py:283-289`). Retires the card, clears the finishing-soon reminder (`coordinator.py:1188-1194`), sets `_notified_stopped`.
- State path (`coordinator.py:1035-1045`): `state == "stopped"` when `_notified_stopped` is still false, with the frame's progress.

Same payload shape and delivery as completed, `kind=stopped`, icon `mdi:stop-circle`, colour `#ffa726` (`notification_rules.py:859-863`).

### 6.4 Finishing soon

| | |
|---|---|
| Trigger | `0 < printLeftTime/60 <= minutes_to_end_value` (`coordinator.py:1102-1107`); `printLeftTime`, fallback `printTimeLeft` (`coordinator.py:748-760`) |
| Re-arm | estimate rises more than 2 min above the window (`coordinator.py:1121-1123`), new job (`coordinator.py:952`, `1220`) |
| Option | `notify_minutes_to_end`, value `minutes_to_end_value` default 5 (`coordinator.py:298-299`) |
| Payload | `build_event_payload(kind=soon, tag=_soon, channel_soon)`, preview only (`coordinator.py:1960-1978`) |
| Dismissal | when the card retires (`coordinator.py:1436`, `1134-1140`) and on a stop (`coordinator.py:1188-1194`) |

### 6.5 Error

| | |
|---|---|
| Trigger | `err.errcode != 0` and different from `_last_error_code` (`coordinator.py:1048-1049`) |
| Baseline | prime (`coordinator.py:787-790`); `0` on every file name change (`coordinator.py:954`); updated every frame with a file name (`coordinator.py:1060`) |
| Option | `notify_error` (`coordinator.py:1051`) |
| Text | `error` template (`error_key` passed in) else `error` string with `{key}` and `{code}` (`coordinator.py:1052-1058`) |
| Payload | `build_alert_payload(kind=error, tag=_alert, channel_alerts)`, snapshot (`coordinator.py:1946-1959`) |
| Bus | `ha_creality_ws_print_error` (`coordinator.py:1059`) |

### 6.6 Filament runout

`materialStatus == 1` and not latched (`coordinator.py:1064-1081`); re-arms when it clears (`coordinator.py:1082-1084`) and on a file name change (`coordinator.py:953`). Shares `notify_error` and the `_alert` tag. No debounce. No bus event.

### 6.7 Alert dismissal

Error and runout share one tag, so the alert is cleared only when both are clear (`coordinator.py:1086-1098`, `194-197`).

### 6.8 Bus events

Fired regardless of targets or options (`coordinator.py:873-878`, `1011-1021`, `1531-1564`). Payload: `entry_id, host, device_name, filename, progress, layer, total_layers, left_seconds, err_code`. Names at `const.py:309-312`. This is the supported route to per-user language (README "Building your own notifications").

---

## 7. Targets and delivery

### 7.1 Targets

`coerce_targets` (`notification_rules.py:79-108`): `notify_targets` list, or the legacy single `notify_device` only when `notify_targets` is absent; deduplicated and stripped. The options page offers legacy `notify.<service>` names and notify entities (`config_flow.py:531-558`).

| Kind | Test | Gets |
|---|---|---|
| `notify.mobile_app_<slug>` | `is_mobile_target` (`notification_rules.py:138-152`) | title, message, `data`; live pushes unless macOS |
| notify entity (`notify.<x>` with a state) | `coordinator.py:2184-2212` | `notify.send_message` with title and message only; sentinel never sent |
| other `domain.service` | `coordinator.py:2223-2235` | title and message only; sentinel never sent |
| no dot | `coordinator.py:2214-2221` | warning, nothing sent |

Platform lookup: `_target_platform` matches `slugify(device_name)` of `mobile_app` entries against the service slug and caches `(os_name, manufacturer)`; `_target_os` and `_target_is_apple` read it. Cleared on every options load. `os_name` drives the macOS live gate; `_target_is_apple` (`manufacturer == "Apple"`, as core decides, else an Apple `os_name`) drives wire shaping. Fixed for R1 (#125).

### 7.2 Fan-out

`_notify_dispatch` (`coordinator.py:2117-2175`): one task per target; `mobile_only` and `live_only` filter non-mobile; `live_only` also filters macOS. INFO log carries only kind and count; live pushes log at DEBUG only (`coordinator.py:2160-2175`).

### 7.3 Wire shaping

`_async_deliver_one` shapes a mobile target's `data` per platform. Apple targets get `native_data`: `None` dropped at the same depths, every type kept. Android and unidentified targets get `stringify_data`: top-level scalars and scalars inside dicts nested in lists become strings (bools lowercase), dict values pass through whole, `None` drops the key; `_warn_on_unsendable` then logs any native scalar left. Before R1 (#125) every mobile target got `stringify_data`.

---

## 8. Payload shapes

### 8.1 What the integration builds

Live card (`notification_rules.py:983-1080`):

| Key | Value | When |
|---|---|---|
| `tag` | `..._live` | always (`984`) |
| `notification_icon` | `mdi:pause-circle` / `mdi:printer-3d-nozzle` | always (`985`) |
| `channel` | translated `channel_live` | always (`986`) |
| `alert_once` | `True` | always (`990`) |
| `activity` | `"start"` | first push of a card (`1019-1021`) |
| `silent`, `push.interruption-level` | `True`, `"passive"` | refresh pushes (`1010-1018`) |
| `content_state` | copy of the typed keys the relay reads: `progress`, `progress_max`, `chronometer`, `critical_text`, and `when` as `countdown_end` | `live_update`. A dict survives `stringify_data` whole, so an unidentified iPhone still decodes. Before R1 it held `state, device, progress_pct, eta_timestamp, program`, which nothing read |
| `subtitle` | job file name | `live_update` and a name (`1041-1043`) |
| `progress`, `progress_max` | 0-100, 100 | progress known (`1046-1051`) |
| `progress_indeterminate` | `True` | progress unknown (`1047-1048`) |
| `live_update` | `True` | not past 8 h (`1053-1054`) |
| `progress_bar_direction`, `progress_bar_color`, `notification_icon_color` | `increasing`, phase colour | `live_update` (`1055-1057`) |
| `chronometer`, `when` | `True`, epoch seconds | `live_update`, not paused, ETA known (`1058-1061`) |
| `chronometer`, `critical_text` | `False`, status word | `live_update` otherwise (`1062-1071`) |
| `icon_url` | `/api/image_proxy/<image entity>` | preview usable (`1076-1077`, `coordinator.py:1275-1280`) |
| `group`, `clickAction`, `url`, `actions` | see 6, 9 | `_apply_common` (`931-948`) |

Terminal and finishing-soon banner (`notification_rules.py:1119-1141`): `tag, notification_icon, notification_icon_color, color, channel, importance: high, push.interruption-level: time-sensitive, activity: end` (terminal only), `icon_url, image` (snapshot `/api/camera_proxy/<camera>` when `snapshot_supported`, `coordinator.py:1282-1288`), `group, clickAction, url`. No progress keys, no `alert_once`, no actions (`notification_rules.py:1096-1117`).

Alert (`notification_rules.py:1161-1178`): `tag _alert`, icon `mdi:printer-3d-nozzle-alert` (runout) or `mdi:alert-circle`, colour `#e53935`, `channel_alerts`, `importance: high`, `push.interruption-level: time-sensitive`, `image`, `group, clickAction, url`.

Dismiss (`notification_rules.py:1181-1191`): `{"message": "clear_notification", "data": {"tag": ...}}`, no title.

Tap target: dashboard path wins on both platforms, else `entityId:<camera>`, else `entityId:<preview>` for Android only (`coordinator.py:1292-1314`).

### 8.2 What each platform does with it

Verified against HA core 2026.9.3 (`homeassistant/components/mobile_app`), the push relay `home-assistant/mobile-apps-fcm-push@991d2f6ae3`, and the iOS app `home-assistant/iOS@778f722dff`.

**Android** (`functions/android.js`): a whitelist of top-level keys is copied with `String()` (`android.js:121`), so the relay itself stringifies top-level scalars. `actions` is flattened to `action_N_key/title/uri/behavior/authenticationRequired`, and `authenticationRequired` is copied raw (`android.js:32`); a native bool there is what FCM rejects. Not whitelisted, therefore dropped: `content_state`, `push`, `subtitle`, `silent`, `activity`, `url`, `progress_bar_color`, `progress_bar_direction`.

**iOS Live Activity** (remote push only):

1. Core: for an Apple registration, `prepare_live_activity_remote_push` runs (`mobile_app/notify.py:248-251`). `resolve_live_activity_push` (`mobile_app/live_activity/__init__.py:84-115`) needs a `tag`; `clear_notification` with a stored per-activity token becomes `event: end`; a truthy `live_update` becomes `update` if a token is stored for the tag, else `start` with the registration's push-to-start token. Tokens are stored per tag by the app's `live_activity_token` webhook and removed by `live_activity_dismissed` or after a successful end (`live_activity/webhook.py`, `store.py`).
2. Relay (`functions/live-activity.js`): `content-state` is built from top-level `message, title, critical_text, progress, progress_max, chronometer, notification_icon, notification_icon_color, progress_bar_color, progress_bar_direction, url, when` (as `countdown_end`), then overridden by `content_state.{title, message, critical_text, progress, progress_max, chronometer, countdown_end, icon, color, background_color, text_color, progress_bar_color, progress_bar_direction, url}` (`live-activity.js:184-234`). Start always carries an alert (`live-activity.js:90-92`); an update is quiet and sent at APNs priority 5 only when `data.silent === true` (`live-activity.js:94`, `122`).
3. App: `ContentState` decodes `progress`/`progress_max` leniently, but `chronometer` as a strict `Bool` and `countdown_end` as a strict `Double` (`Sources/Shared/LiveActivity/HALiveActivityAttributes.swift:223-226`). A string in either throws, and ActivityKit drops the push silently.

The integration's own `activity` key is read by none of the three (tests use it to tell terminal payloads apart). Since R1, `content_state` carries only relay keys.

**iOS ordinary notification** (`functions/legacy.js`, used when no live-activity token applies): `tag` to `apns-collapse-id`, `group` to `thread-id`, `subtitle`, `push` merged into `aps`, `actions` passed through, `image` as attachment, `url`, `icon_url` (`legacy.js:120-237`). Actions are parsed with ObjectMapper `value(key, default:)`, which falls back to the default on a type mismatch (`Sources/Shared/API/Responses/MobileAppConfig/MobileAppConfigPushCategory.swift:21,24`; ObjectMapper `Sources/Map.swift:220-226`), so `"true"` reads as `false`.

**iOS local push** (phone on a local-push SSID): core sends over the WebSocket channel first and falls back to remote only if the app does not confirm within 10 s (`mobile_app/notify.py:220-226`, `push_notification.py:10`, `33-63`). The app treats a message as a live-activity command only if `live_update` is a real `Bool` (`Sources/Shared/Notifications/LocalPush/LocalPushManager.swift:251-254`); otherwise it posts an ordinary banner and confirms, so the remote fallback never happens (`LocalPushManager.swift:186-246`).

---

## 9. Actions

- Ids are `CREALITY_{PAUSE,RESUME,STOP,DISMISS}_<ENTRY_ID>` from the whole entry id (`notification_rules.py:1209-1226`).
- Buttons: Pause or Resume, Stop (`destructive`, `authenticationRequired`), Hide; `notify_actions` off keeps only Hide (`notification_rules.py:1229-1279`, `coordinator.py:1577-1595`). Live card only; banners and alerts carry none (`coordinator.py:1940-1958`, `notification_rules.py:1140`).
- One `mobile_app_notification_action` listener per entry, registered unconditionally (`__init__.py:426-437`); exact id match (`coordinator.py:1597-1624`). Pause/Resume go through `request_pause`/`request_resume` (`coordinator.py:477-531`); Stop through `async_stop_print` (`coordinator.py:1626-1636`); Hide dismisses on every target and finishes the card.
- An iOS Live Activity has no action buttons at all; they appear on iOS only when the card is an ordinary notification.

---

## 10. Text and language

- Strings: `strings.json` `common` (`strings.json:24-53`), mirrored in `translations/en.json` and `translations/es.json` (key and placeholder sets match).
- Loaded once per options load via `async_get_translations(hass, hass.config.language, "common", {DOMAIN})` (`coordinator.py:1656-1692`). An integration is never told which user a `notify` call is for, so every body follows the **server** language; per-user language is only possible through the bus events (`coordinator.py:1659-1665`, `1536-1540`). No strings means no delivery (`coordinator.py:884`, `1687-1692`).
- `_t` returns empty on a missing key or broken placeholder rather than raising; `key` is positional-only because the error string has a `{key}` placeholder (`coordinator.py:1694-1717`).
- Durations and filament length use translated templates with pre-formatted numbers (`notification_rules.py:178-241`).
- File names are basenamed for bodies only (`notification_rules.py:160-175`, `coordinator.py:969`).
- Custom templates: one option per notification (`const.py:171-190`, mapping at `183-190`), allowed placeholders per notification (`notification_rules.py:253-290`), `[optional]` segments and `[[`/`]]` escapes (`notification_rules.py:296-387`). Unknown placeholders are refused by the options flow (`config_flow.py:588-603`) and fall back to the shipped text at run time with one warning per template (`coordinator.py:1784-1813`). Values come from one frame (`coordinator.py:1719-1770`).
- `{state}` in `filament_runout` and in templates is the raw state slug (`coordinator.py:1077`, `1760`), not a translated word.

---

## 11. Options

| Key | Default | Read at | Section |
|---|---|---|---|
| `notify_targets` | absent, then legacy `notify_device` | `coordinator.py:276` | top of page |
| `notify_live` | `False` | `coordinator.py:277` | events |
| `notify_completed` | `False` | `coordinator.py:296` | events |
| `notify_error` | `False` | `coordinator.py:297` | events |
| `notify_minutes_to_end` | `False` | `coordinator.py:298` | events |
| `minutes_to_end_value` | `5` (1-60, a float from the selector) | `coordinator.py:299`, `config_flow.py:645-652` | events |
| `notify_actions` | `False` | `coordinator.py:278` | extras |
| `notify_preview_image` | `True` | `coordinator.py:279` | extras |
| `notify_camera_snapshot` | `True` | `coordinator.py:280` | extras |
| `notify_tap_path` | `""` | `coordinator.py:281` | extras |
| `notify_template_{live,completed,stopped,soon,error,runout}` | `""` | `coordinator.py:285-290` | text |

Defaults live once in `_NOTIFY_SECTIONS` (`config_flow.py:185-205`). A change confined to `NOTIFY_ONLY_OPTION_KEYS` (`const.py:204-219`) is applied in place (`__init__.py:975-985`, `coordinator.py:1880-1922`); anything else reloads, after `notify_options_changed` dismisses the card from phones that lost it (`__init__.py:987-993`). Deleting the entry sends the sentinel for `_live`, `_soon` and `_alert` to every mobile target (`__init__.py:1011-1053`). Unload sends nothing (`coordinator.py:1516-1518`).

---

## 12. persistent_notification

Not used for print notifications. `notify.persistent_notification` as a target is a non-mobile service and gets title and message only. `persistent_notification.async_create` is used by the CFS and diagnostic services (`__init__.py:567-572`, `650-669`, `943-948`) with inline English titles and bodies.

---

## 13. Tests

| File | Drives | Notes |
|---|---|---|
| `tools/tests/test_notification_rules.py` | pure module | 83 test functions; watch tables at `1049-1240` |
| `tools/tests/test_notification_live.py` | real `_check_notifications`, fake clock | `_frame` at `132-140`; #124 at `1320-1332` |
| `tools/tests/test_notifications.py` | gating, `_notify_event` monkeypatched | stub deliberately lacks services (`32-44`) |
| `tools/tests/test_notification_dispatch.py` | `_notify_dispatch`, `_async_deliver_one`, per-platform shaping | stub carries `mobile_entries` for `config_entries.async_entries` since R1 |
| `tools/tests/test_notification_payload.py` | media, links, tags | |
| `tools/tests/test_notification_actions.py` | ids, buttons, handler | asserts pre-wire dicts |
| `tools/tests/test_notification_templates.py` | custom text | |
| `tools/tests/test_notification_wire_format.py` | `stringify_data` | Android rules; Apple shaping is tested in `test_notification_dispatch.py` |
| `tools/tests/test_options_flow.py:350-` | notifications page | |

All drive production code; none reimplements the logic under test. The #124 tests fail when the fix is reverted (checked by disabling `notification_rules.py:634` in a scratch copy).

---

## 14. Traps

1. **One wire format for every mobile target.** FIXED (R1): Apple targets now get native types. Historical: `stringify_data` is Android's rule and was applied to iPhones too, breaking the Live Activity decode, the local-push live check, `silent` and the Stop action's flags.
2. **`activity: start/end` does nothing.** Start versus update is decided by core from its per-tag token store (section 8.2). Every `live_update` push sent while no token is stored is a push-to-start with an alert.
3. **`content_state` keys are not the relay's.** FIXED (R1): it now repeats the relay's typed keys.
4. **One tag carries three things, and their order is not guaranteed.** `_live` holds the card, the terminal banner and the dismiss sentinel. `_async_replace_one` awaits the dismissal before the banner (`coordinator.py:2111-2115`), but `_async_deliver_one` calls `hass.services.async_call` without `blocking=True` (`coordinator.py:2209-2211`, `2245`). Core then runs the service as a background task and returns at once (`homeassistant/core.py:2953-2959` in 2026.9.3), so both HTTPS posts to the relay are in flight together and can arrive in either order. For the same reason the `except` at `coordinator.py:2246-2248` never sees a failed push; core logs those itself.
5. **Detection runs without targets.** Latches and the job clock advance with no target or with an event toggle off (`coordinator.py:873-878`, `1011-1021`); only sends are gated.
6. **`printProgress` uses an explicit `None` check** in four places (`coordinator.py:857-859`, `778-780`, `2042-2044`, `utils.py:527-531`). `or` would read a real 0% as missing and fall back to a stale `dProgress` of 100.
7. **`self-testing` outranks everything** in `derive_print_state` (`utils.py:522-524`). While `withSelfTest` sits in 1-99 a stop, a cleared file name and completion are invisible.
8. **`processing` is ambiguous.** It is both warm-up and post-cancel idle, so it counts as busy for the card but never arms the watch (`notification_rules.py:527-533`).
9. **`card_active` is memory only.** A restart forgets the card; priming sends an adopt-clear when idle and a mid-job `START` replaces it in place (`coordinator.py:822-836`). `notifier_tick` ignores a card it does not know about (`coordinator.py:1646-1647`).
10. **`_reset_for_new_job` order.** Dismiss before clearing state, or nothing remembers the card (`coordinator.py:1201-1212`).
11. **Tag base from entry id, not host.** Dots are illegal in a tag, and the same base must be rebuilt by `async_remove_entry` without a coordinator (`notification_rules.py:396-414`).
12. **Language is the server's.** Channel names too, so a server language change creates new Android channels.
13. **Placeholder braces in step descriptions** must arrive through `description_placeholders`, or ICU replaces the whole description with "Translation error" (`config_flow.py:240-250`, ICU note at `245-248`).
14. **Hide is global.** It dismisses the card on every phone (`coordinator.py:1614-1620`, `1521-1525`).
15. **Action ids are per printer, not per job** (`notification_rules.py:1209-1226`). An old notification's Stop acts on whatever that printer is printing now.
16. **Test harnesses skip `_handle_message`.** They set `coord.data` and call `_check_notifications` directly, so the paused flag comes only from `state == 5` in `derive_print_state`, and the dispatch harness cannot resolve a target platform (section 13).

---

## 15. Known defects at `da0295a`

Verified by reading the code end to end and by driving `_check_notifications` with the suite's own fixtures. Detail and fixes are in the audit that produced this document.

| Defect | Where |
|---|---|
| iOS receives Android's stringified payload: no Live Activity (issue #125), no local-push live handling, noisy refreshes, Stop without authentication | `coordinator.py:2240-2244`, section 8.2 |
| FIXED (R10): a completed print whose progress later resets to 0 in `state` 0 stood up a phantom card and fired `print_started`. The `is_new_job_cycle` re-arm now also requires `REARM_JOB_STATES` (printing, paused, self-testing), and the started event is checked after the re-arm so a same-file reprint is announced on the frame that re-arms it | `coordinator.py` `_check_notifications` |
| FIXED (R11, by R10's gate): a `state` 4 stop that also reset `printJobTime` was announced twice and fired `print_started`, because the clock reset re-armed the stop latch. `stopped` is not a `REARM_JOB_STATES` state. Pinned by `test_a_stop_reported_with_its_clock_reset_is_announced_once` | `coordinator.py` `_check_notifications` |
| A sticky error code or `materialStatus` fires an alert at every new print | `coordinator.py:953-954`, `1049`, `1070` |
| Dismiss-then-banner order is not guaranteed (non-blocking service call) | `coordinator.py:2111-2115`, `2245` |
| "Finishing" during warm-up and self-test; "0s" as time left | `coordinator.py:1497-1503`, `1383-1385` |
| A finishing-soon dismissal is sent at every completion even with that option off | `coordinator.py:1120`, `1436`, `1134-1140` |
| `{state}` is an untranslated slug inside translated text | `coordinator.py:1077`, `strings.json:51` |
