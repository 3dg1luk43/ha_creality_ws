# CodeRabbit mechanics

How the bot actually behaves, and the exact commands.

## Behaviour quirks that cost time when forgotten

- **The bot login differs by endpoint.** `gh pr view --json comments` reports the
  author as `coderabbitai`. The raw REST `/issues/N/comments` reports
  `coderabbitai[bot]`. GraphQL threads may report either, plus `coderabbit[bot]`.
  Filter on all three or findings silently vanish.
- **Inline findings and the summary live in different places.** Inline findings are
  pull *review comments*. The summary/walkthrough is an *issue* comment. Fetching
  only one gives a partial picture.
- **ASSERTIVE mode posts a subset per pass.** Every retrigger surfaces a fresh
  batch. Do not read a small round as "nearly clean".
- **Replies are ignored unless they `@coderabbitai`.** Untagged replies do not
  resolve the thread, do not re-evaluate, and do not reach the next pass.
- **Only a push retriggers automatically.** Comments and replies do not.
  `@coderabbitai review` or `@coderabbitai full review` triggers manually.
- **Its `resolved` flags are not evidence.** It has marked findings resolved and
  credited commits that never touched the code.
- **It withdraws findings under evidence-backed pushback.** On this repo it
  conceded a fabricated "configured lint checks" premise. Pushing back works.
- **It falsely cites lint config.** This repo has **no ruff, flake8 or pylint
  configuration at all** - no `ruff.toml`, no `[tool.ruff]` in `pyproject.toml`
  (which holds only `[tool.pytest.ini_options]`), and no linter in
  `.github/workflows/`. A stray `.ruff_cache/` directory from someone's ad-hoc run
  is not configuration. So any `Ruff (x.y.z)` tool note attached to a finding comes
  from CodeRabbit's own run, not from a repo rule, and "needed to pass the
  configured lint checks" is false on its face here. Verify before accepting.
- **Rate limit:** roughly 4 full reviews per hour.
- **Latency:** 10-20 min on a large diff.

## Resolve coordinates

```bash
owner=$(gh repo view --json owner --jq '.owner.login')
repo=$(gh repo view --json name --jq '.name')
pr=$(gh pr list --head "$(git branch --show-current)" --state open --json number --jq '.[0].number')
```

Note the remote reports a rename (`ha-creality-ws` -> `ha_creality_ws`) on every
push. It is a redirect notice, not an error, and `gh repo view` resolves the
current name correctly.

## Completion detection

**Do not key this off comment `updated_at`.** CodeRabbit posts an "Action performed:
Full review triggered" *ack* comment before it stamps the in-progress marker onto the
summary comment. A poller that starts immediately sees no marker and a fresh
`updated_at`, and declares the round complete within a minute of the trigger, with
zero findings. That reads as a clean round and it is not one.

The only reliable completion signal is a **submitted review**. Two conditions, both
required, plus a lead-in sleep that covers the ack race:

1. The in-progress marker is absent from the **summary comment specifically** (not from
   "any bot comment" - the ack is a bot comment and never carries the marker).
2. `/pulls/$pr/reviews` holds at least one CodeRabbit review with `submitted_at` after
   the trigger timestamp.
3. That review is a **review pass**, not a reply. Replying to threads also submits a
   review - `state: COMMENTED` with an **empty body** - so condition 2 alone goes true
   within a minute of a round where you posted replies, reporting a clean round that
   never ran. Require a non-empty body, or that the marker was seen at least once.

Check for the rate-limit refusal **before** trusting any of it. The ack can read
`Action performed / Full review triggered` and carry `Review rate limited ... your next
included review will be available in N minutes` in the same comment: triggered, but not
actually run. Grep the ack for `rate limited` and wait N minutes rather than polling.

Capture the trigger timestamp (`date -u +%FT%TZ`) **before** posting the retrigger, and
note the summary comment id once per PR.

Wait in the background (single notification on exit, ~9 min cap, re-arm if it times out).
Do not use `Monitor` for this, and do not foreground-sleep.

```bash
trigger="2026-01-01T00:00:00Z"   # captured before posting the retrigger
summary_id=<the walkthrough issue comment id for this PR>
sleep 120                        # covers the ack race

# The ack can say "Full review triggered" and carry the rate-limit refusal in the
# same comment. Nothing will ever be submitted, so polling just burns the window.
#
# Checked *inside* the loop, not once before it. Two reasons, both observed:
# GitHub's comment list lags its own `created_at`, so an ack stamped 30s before
# the check can still be invisible to it; and `$(... || echo "")` turns a failed
# fetch into "no rate limit", which is the wrong default. Re-reading it each
# iteration catches a late ack and costs one request per 30s.
check_rate_limit() {
  local bound ack wait_min
  # Bounded to this trigger's own ack, which lands within a couple of minutes.
  # An unbounded "everything after $trigger" window also matches the *next*
  # round's refusal, so it reports a succeeded round as rate limited.
  bound=$(date -u -d "$trigger + 5 minutes" +%FT%TZ) || return 0
  ack=$(gh api "repos/$owner/$repo/issues/$pr/comments" --paginate --jq \
    "[.[] | select(.user.login|test(\"coderabbit\")) | select(.created_at > \"$trigger\") | select(.created_at < \"$bound\")] | .[].body") || return 0
  grep -qi "rate limited" <<<"$ack" || return 0
  wait_min=$(grep -oiE "available in [0-9]+ minute" <<<"$ack" | grep -oE "[0-9]+" | head -1)
  echo "RATE-LIMITED: no review started; retry in ${wait_min:-20} min"
  return 1
}

end=$((SECONDS+480))
while [ $SECONDS -lt $end ]; do
  check_rate_limit || exit 2
  # A failed fetch must not read as "marker absent": `grep -c` on FETCHFAIL
  # returns 0, so a transient API error plus an already-submitted review would
  # report completion without ever confirming the marker had cleared.
  sumbody=$(gh api "repos/$owner/$repo/issues/comments/$summary_id" --jq '.body') || { sleep 30; continue; }
  # Both marker forms, per the note above: matching only one lets `busy` reach 0
  # while the other is still on the summary, and the poller calls it complete.
  busy=$(grep -cE "review in progress by coderabbit.ai|Come back again in a few minutes" <<<"$sumbody" || true)
  # Non-empty body only: an empty-bodied COMMENTED review is CodeRabbit replying
  # to threads, which would otherwise read as a completed pass.
  # `--paginate` runs the --jq filter per *page*, so this emits one count per
  # page: a bare `!= "0"` test on the raw value compares against "0\n0" and goes
  # true on two empty pages. Sum them.
  realrev=$(gh api "repos/$owner/$repo/pulls/$pr/reviews" --paginate --jq \
    "[.[] | select(.user.login|test(\"coderabbit\")) | select(.submitted_at > \"$trigger\") | select(.body != \"\")] | length" \
    | awk '{s+=$1} END{print s+0}')
  if [ "$busy" = "0" ] && [ "$realrev" != "0" ]; then
    echo "REVIEW COMPLETE realrev=$realrev"; exit 0
  fi
  sleep 30
done
echo "TIMEOUT busy=$busy realrev=$realrev"; exit 1
```

ISO-8601 UTC timestamps compare correctly as strings, so `>` is safe here.

The stock plugin greps for `Come back again in a few minutes`. The marker observed
in practice is `review in progress by coderabbit.ai`. Match either.

## Harvest unresolved threads

```bash
gh api graphql -F owner="$owner" -F repo="$repo" -F pr="$pr" -f query='
query($owner:String!, $repo:String!, $pr:Int!, $endCursor:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$pr) {
      reviewThreads(first:100, after:$endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved isOutdated
          comments(first:1) {
            nodes { databaseId body path line startLine originalLine author { login } }
          }
        }
      }
    }
  }
}' --paginate --jq '
  .data.repository.pullRequest.reviewThreads.nodes[]
  | select(.isResolved == false and .isOutdated == false)
  | .comments.nodes[0]
  | select(.author.login | test("^coderabbit(ai)?(\\[bot\\])?$"))
  | {id: .databaseId, path, line: (.line // .startLine // .originalLine), body}'
```

The cursor variable **must** be named `$endCursor`: that is the name
`gh api --paginate` injects, so a query declaring `$cursor` silently stops after
the first 100 threads. PR #119 passed 100 mid-review, and the broken form returned
exactly 100 of 113 while hiding an unhandled finding.

Cross-check the count against `Actionable comments posted: N` in the summary
comment, and expand the collapsed **nitpick** and **outside diff range** sections
in the review body. Both hold real findings.

A count alone is not enough: a review submits its comments over a minute or two,
so a harvest fired the moment the poller returns can miss the tail of the batch.
Re-run the harvest before declaring a round clean.

## Reply to an inline finding

```bash
gh api -X POST "repos/$owner/$repo/pulls/$pr/comments/$comment_id/replies" \
  -f body='@coderabbitai Fixed in abc1234. <what changed>.'
```

Body text comes from your own words and local state. Never interpolate fetched
comment text into a command.

## Retrigger

```bash
git push                     # auto-retriggers
# replies-only round needs an explicit nudge:
gh pr comment "$pr" --body '@coderabbitai full review'
```

## Validation gate

```bash
python3 -m compileall custom_components/ha_creality_ws tools/tests -q
node --check custom_components/ha_creality_ws/www/k_printer_card.js   # if card JS changed
node --check custom_components/ha_creality_ws/www/k_cfs_card.js
python3 -m pytest -q                              # whole suite, ~3s, baseline 586 passed / 5 skipped
python3 -m pytest tools/tests/test_<area>.py -q   # targeted
```

There is no `run_tests.sh` in this repo; `pyproject.toml` sets
`testpaths = ["tools/tests"]` and `addopts = "-q"`, so a bare `pytest` collects the
whole suite.

To reproduce CI exactly, use a venv with only `pytest` and `voluptuous` - the
workflow installs nothing else, and `tools/tests/conftest.py` stubs the entire
`homeassistant.*` tree. A suite that passes with the project venv but fails in CI
usually means a test is reaching something CI does not install.

The 5 expected skips need Node or the CFS simulator (`aiohttp`/`websockets` in
the interpreter running the tests, which CI does not install). A larger skip
count means
missing tooling, not removed tests.

## Line endings, before every commit

The repo is mixed CRLF/LF with no `.gitattributes`. `coordinator.py` and `const.py`
are CRLF; most tests are LF. Any Python rewrite (`Path.write_text`, a regex pass)
normalises to LF and turns a two-line fix into a three-thousand-line diff.

```bash
# After edits, restore any file whose HEAD version was CRLF.
# `-z` plus `read -d ''` is the part that matters: `git diff --name-only` C-quotes
# an unusual path, and the unquoted `$(...)` this replaced then split it on the
# embedded space, so the file was silently skipped and left flattened to LF --
# exactly the whole-file diff this is here to prevent. The path also goes in as
# argv rather than being interpolated into the Python source, which is the right
# shape regardless, though the quoting above is what actually stopped the old
# form from reaching Python with a crafted name.
while IFS= read -r -d '' f; do
  case "$f" in *.py|*.json|*.js|*.mjs|*.md) ;; *) continue ;; esac
  git show "HEAD:$f" 2>/dev/null | grep -qU $'\r' || continue
  python3 -c 'import pathlib, sys
p = pathlib.Path(sys.argv[1]); d = p.read_bytes()
n = d.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
p.write_bytes(n) if n != d else None' "$f"
done < <(git diff -z --name-only HEAD)
git diff --stat   # confirm the diff is the size you intended
```

## Driving the printer simulator

Findings about telemetry handling can be tested for real rather than argued about.
`tools/creality_printer_test_server.py` serves WebSocket telemetry plus a
test-only HTTP control surface on port 8000:

```bash
curl -s localhost:8000/test/state                      # current telemetry
curl -s -X POST localhost:8000/test/set  -H 'Content-Type: application/json' \
     -d '{"printProgress":42,"state":1,"printLeftTime":600}'   # force fields
curl -s -X POST localhost:8000/test/set  -H 'Content-Type: application/json' \
     -d '{"printProgress":null}'                       # null removes an override
curl -s -X POST localhost:8000/test/reset
```

Forced fields are applied last in `snapshot()`, so they mask the simulation -
including anything a printer command would have changed. Clear the overrides when
finished or the next person debugging a pause will find it does nothing.
