# Refusal policy and standing-rejection ledger

Read this before triaging. If a finding matches something here, reuse the recorded
rationale rather than re-deriving it, and do not re-litigate a rejection.

## When refusal is the right verdict

1. **The premise is factually wrong.** Verify before accepting. CodeRabbit invents
   "needed to pass the configured lint checks" for rules the repo does not
   configure. Concretely: this repo has **no ruff, flake8 or pylint config at
   all** - `pyproject.toml` holds only `[tool.pytest.ini_options]`, and no linter
   runs in `.github/workflows/`. A stray `.ruff_cache/` is someone's ad-hoc run,
   not configuration. `# pylint: disable=broad-except` is a local convention, not
   evidence of a configured linter.
2. **Correct in general, wrong against the hardware or the push relay.** Printer
   firmware and the companion-app relay both behave in ways no amount of reading
   predicts. Where this file records a measurement, the measurement wins.
3. **Empirically disproven.** See the locked list below.
4. **Out of scope for the PR.** Real but unrelated. DEFER, do not silently expand
   the diff.

Refusal needs evidence: a code line, a test name, or an on-device measurement.
Post it as the inline reply. Vague disagreement is not a refusal, and CodeRabbit
will re-flag it next round.

## Locked: measured on real devices, never "fix" from first principles

- **Top-level values in a companion `data` payload must be strings.** Android
  delivery is an FCM data message, whose payload is `map<string, string>`. A
  native `int` or `bool` at the top level makes the relay reject the **entire**
  push with `data must only contain string values`, and Home Assistant surfaces
  that only as `Error sending notification to <device>` with the reason at DEBUG.
  A finding asking for "proper booleans" or "real ints" in that dict is asking to
  break every Android notification. `stringify_data` does the coercion;
  `test_notification_wire_format.py` pins it.
- **Nesting is not uniformly exempt, and the asymmetry is measured.** `actions` is
  a **list** and is flattened into the same FCM map, so a bool inside it (
  `destructive`, `authenticationRequired`) is rejected exactly like a top-level
  one. `push` and `content_state` are plain **dicts**, are not flattened, and keep
  native ints - iOS renders a Live Activity from `content_state`, so coercing its
  numbers would be the wrong fix. Bisected key-by-key against a Galaxy S24.
- **A live card cannot be made unswipeable, and `persistent` does not do it.**
  Tested on Android 16 with `persistent`, without it, and with the action buttons
  removed: swipeable in every combination. The companion documentation says why in
  the same breath as documenting the key - "starting in Android 14 persistent
  notifications will be dismissable except when the device is locked". So
  `persistent`, `sticky` and `importance` are deliberately absent from the live
  payload; a finding asking to add them back is asking for keys that measurably do
  nothing, and `importance: low` additionally requests the minimised presentation
  a live card does not want. The Hide action is the way out.
- **`chronometer` + a future `when` already counts down.** The docs state the timer
  "will continue decrementing into negative values". `countdown` was added on the
  opposite assumption, measured against nothing, and reverted. Do not re-add it
  without a device observation.
- **The relay allows ~500 pushes per device per day** (it says so in its own
  rate-limit log line). The live-card cadence - a 1% milestone, a 30s floor, a
  300s refresh - is sized against that budget. A finding asking for "more
  responsive" updates needs to say what it costs against 500/day for a user with
  two printers.

## Standing rejections

| Finding | Verdict | Evidence to reply with |
|---|---|---|
| "Normalise line endings" / a whitespace or formatting sweep across a CRLF file | REFUSE | The repo is intentionally mixed CRLF/LF with no `.gitattributes`. `coordinator.py` and `const.py` are CRLF. Rewriting them to LF turns a two-line fix into a whole-file diff and buries the actual change. Out of scope for any review finding; it would be its own commit with its own decision. |
| `const.py` should import a shared helper / use an enum from elsewhere | REFUSE | It must stay import-free. `tools/tests/test_const_standalone.py` execs it standalone and asserts zero imports, because `hacs.json`/manifest tooling reads it without Home Assistant present. Option-coercion helpers live in `notification_rules.py` for exactly this reason. |
| `derive_activity_state` "duplicates" `derive_print_state` - collapse them | REFUSE | Deliberately different (`utils.py:465` and `:513`). `derive_print_state` returns `"error"` whenever `err.errcode != 0`, including a stale code the printer never clears. The card and the control decisions need the error collapsed back to the underlying job state, or a stale code pins the card to "error" for a whole print. The one-shot error notification still keys on the code *changing*. |
| Simplify the completion re-arm / the `99 -> 100 -> 99` progress handling | REFUSE | That jitter is why `NOTIFY_REARM_PROGRESS_MAX` exists (commit b2ac2ea). The printer rounds progress up to 100 a second before the job ends, reports 99 once more, then finishes - so re-arming on any dip below 100 sent the completion notification **twice per print**. `is_new_job_cycle` needs both signals (`ended_at_completion` via the progress drop, `ended_early` via the job clock only) because a job stopped at 30% never leaves the jitter band. |
| Inline English in a notification body or card label "should be translated" | CHECK FIRST, usually FIX | Real rule: no user-visible string belongs in `.py` or `.js`. They live in `strings.json` + `translations/*.json`, and card text in `www/i18n/*.json`. But **notification bodies follow the *server* language by design** - an integration is never told which user a `notify` call is for - so a finding asking for per-user localisation of a notification body is refused, and the bus events (`ha_creality_ws_print_*`) are the documented path for a user who wants their own text. |
| "Machine-translate the missing locale keys" / add a translation inline | REFUSE | Translations come from subagents with domain context, never from a machine translator or from guessing. This is a standing repo rule. |
| Removing `_warn_on_unsendable` as dead code / defensive cruft | REFUSE | It is the only attribution this integration has. Home Assistant catches the relay's rejection internally, so a failed push never reaches our code and there is nothing to except-handle. This exact defect class survived two releases precisely because nothing on our side could see it; the warning names the offending keys. |
| Add a feature that needs a telemetry value the printer does not stream | REFUSE | Standing rule: no fabricated data. If the printer does not report it (filament *mass*, for instance), the integration does not invent, estimate or derive it into a sensor. |
| "Await the notify call" / drop the `async_create_task` fan-out | REFUSE | `ws_client` awaits `_on_message` inline in its receive loop. A notify call is an HTTPS POST to the push relay, so awaiting one there stalls the loop, lets `client.last_rx_monotonic()` go stale, and flips every entity unavailable at `STALE_AFTER_SECS`. One task per target, never awaited from the frame path. The single exception is `_async_replace_one`, which awaits *two* sends inside one task to guarantee their order. |
| "Send the dismiss sentinel to every configured target" | REFUSE | `clear_notification` is only meaningful to the companion app. Any other notify platform renders it as visible body text, and `notify.send_message` cannot carry a tag at all. Both exits are guarded, and `test_notification_dispatch.py` pins them. |

## Repo rules that override any finding

- **Never machine-translate.** Translations come from subagents with domain
  context. This has its own memory entry.
- **No user-visible strings inline in `.py` or `.js`.** `strings.json` plus
  `translations/*.json` for the HA layer, `www/i18n/*.json` for the cards.
- **No fabricated data.** A sensor reports what the printer streams, or it does
  not exist.
- **Restore line endings before committing.** See `mechanics.md`. A whole-file
  diff on a CRLF file is the tell.
- **Verify a new regression test by reverting its fix.** A test that passes
  without the fix pins nothing. Two "fixes" in this repo were deleted after
  mutation testing showed their tests were decorative - that check is cheap and it
  has paid for itself.
- **CI is the validator for hassfest and HACS, not the local suite.** The repo's
  hassfest tests are a model of that validator built by reading its source. A
  green `pytest` does not prove `strings.json` passes.
- **Do not hand-edit generated or vendored card assets.** Fix the source.

Note that em dashes are used freely in this repo's prose and comments, unlike its
sibling project. Do not "fix" them.

## Typical DEFER territory

Real, but the maintainer's call:

- Anything changing the WebSocket protocol or a printer command payload.
- Anything changing notification cadence constants, which are budgeted against the
  relay's daily push limit.
- Raising or lowering the minimum Home Assistant version.
- Removing or reshaping a shipped entity, which breaks users' dashboards and
  automations.
- Camera/WebRTC negotiation behaviour, which is firmware-specific per model and
  cannot be verified without that hardware.

## Feeding this file

When a round produces a new rejection with durable rationale, add a row. That is
what stops round N+3 re-arguing it. Findings that were valid and fixed do not
belong here.
