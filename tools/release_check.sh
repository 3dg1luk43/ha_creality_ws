#!/usr/bin/env bash
# HA Creality WS - release preflight: everything that must be true before a tag
# is cut. Ported from ha_washdata.
#
# The check nothing else does is version agreement: manifest.json's `version`
# (what HACS and Home Assistant report), the top CHANGELOG.md heading (what
# people read, and what the GitHub release body is cut from) and the tag. The
# rest is the test suite plus the parse checks a release cannot ship without.
#
#   tools/release_check.sh                 verify only (safe; used by CI)
#   tools/release_check.sh --tag v0.9.9    also require the tag to match, and the
#                                          CHANGELOG entry to be dated
#
# Exit code is the number of failed checks, so `if release_check.sh; then tag; fi`
# works. Every failure prints the command or file that fixes it.
set -uo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WANT_TAG=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      if [[ $# -lt 2 ]]; then echo "--tag needs a value (e.g. --tag v0.9.9)" >&2; exit 2; fi
      WANT_TAG="$2"; shift 2 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

FAILED=0
PY="./.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"
COMPONENT="custom_components/ha_creality_ws"

pass() { printf '  \033[32mok\033[0m    %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; [[ -n "${2:-}" ]] && printf '        -> %s\n' "$2"; FAILED=$((FAILED + 1)); }
skip() { printf '  \033[33mskip\033[0m  %s\n' "$1"; }
head_() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ── 1. version agreement ─────────────────────────────────────────────────────
head_ "Version"
VERSION_REPORT=$(WANT_TAG="$WANT_TAG" "$PY" - <<'PY'
import json, os, re, sys
mf = json.load(open("custom_components/ha_creality_ws/manifest.json"))["version"]
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(r"^##\s+\[?v?(\d[^\s\]]*)\]?\s*(?:-\s*(.+?))?\s*$", text, re.M)
cl = m.group(1) if m else None
cl_date = (m.group(2) or "").strip() if m else ""
want = (os.environ.get("WANT_TAG") or "").lstrip("v") or None
print(f"manifest={mf}")
bad = []
if cl is None:
    bad.append("no '## <version>' heading found in CHANGELOG.md")
elif mf != cl:
    bad.append(f"manifest.json says {mf} but the top CHANGELOG entry is {cl}")
if want and want != mf:
    bad.append(f"tag says {want} but manifest.json says {mf}")
# A tagged release is a dated one. "Unreleased" is the heading while work is in
# progress, and shipping it would publish a release that says it is not out.
if want and not re.match(r"\d{4}-\d{2}-\d{2}$", cl_date):
    bad.append(f"the top CHANGELOG entry is dated '{cl_date or '(nothing)'}', not YYYY-MM-DD")
# The release body is cut from this section; an empty one publishes nothing.
if m:
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.M)
    if not rest[: nxt.start() if nxt else len(rest)].strip():
        bad.append(f"the CHANGELOG entry for {cl} is empty")
for b in bad:
    print("PROBLEM:" + b)
sys.exit(1 if bad else 0)
PY
)
VERSION_RC=$?
MF_VER=$(sed -n 's/^manifest=//p' <<<"$VERSION_REPORT")
if [[ $VERSION_RC -eq 0 ]]; then
  pass "version agrees everywhere (${MF_VER})"
else
  while IFS= read -r line; do
    [[ "$line" == PROBLEM:* ]] && fail "${line#PROBLEM:}" "fix $COMPONENT/manifest.json and/or the top CHANGELOG.md heading"
  done <<<"$VERSION_REPORT"
fi

# ── 2. shipped files parse ───────────────────────────────────────────────────
# One malformed translation breaks setup for that locale; one malformed card
# file blanks the card. The test suite checks their content, this only that
# they load at all, so a release never waits on a collection error to say so.
head_ "Shipped files"
BAD_JSON=$("$PY" - <<'PY'
import json
from pathlib import Path
bad = []
for p in sorted(Path("custom_components/ha_creality_ws").rglob("*.json")):
    try:
        json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        bad.append(f"{p}: {exc}")
print("\n".join(bad))
PY
)
if [[ -z "$BAD_JSON" ]]; then pass "every shipped .json parses"
else fail "malformed JSON in the shipped component" "$BAD_JSON"; fi

if "$PY" -m compileall -q "$COMPONENT" tools >/dev/null 2>&1; then pass "python compiles"
else fail "python syntax error" "$PY -m compileall $COMPONENT tools"; fi

if command -v node >/dev/null 2>&1; then
  JS_BAD=""
  for js in "$COMPONENT"/www/*.js; do
    node --check "$js" 2>/dev/null || JS_BAD="$JS_BAD $js"
  done
  if [[ -z "$JS_BAD" ]]; then pass "card JS parses"
  else fail "card JS does not parse:$JS_BAD" "node --check$JS_BAD"; fi
else
  skip "card JS parse (node unavailable)"
fi

# ── 3. tests ─────────────────────────────────────────────────────────────────
# A failure prints its tail: a collection error takes the whole suite down, and
# "suite failed" alone cannot tell that from a real regression.
head_ "Tests"
if out=$("$PY" -m pytest 2>&1); then
  pass "test suite ($(tail -1 <<<"$out" | sed 's/^=* *//; s/ *=*$//'))"
else
  fail "test suite failed" "$PY -m pytest"
  printf '%s\n' "$out" | tail -25 | sed 's/^/        | /'
fi
command -v node >/dev/null 2>&1 || skip "card tests ran without node, so the JS harnesses were skipped"

# ── 4. release hygiene ───────────────────────────────────────────────────────
head_ "Release hygiene"
if git rev-parse --git-dir >/dev/null 2>&1; then
  DIRTY=$(git status --porcelain --untracked-files=no)
  if [[ -z "$DIRTY" ]]; then
    pass "working tree clean"
  else
    # A warning, not a failure: the script is meant to be usable mid-work.
    printf '  \033[33mwarn\033[0m  uncommitted changes to tracked files (%s file(s))\n' "$(wc -l <<<"$DIRTY")"
  fi
else
  skip "git checks (not a repository)"
fi

printf '\n'
if [[ $FAILED -eq 0 ]]; then
  printf '\033[32mRelease preflight passed.\033[0m %s\n' "${MF_VER:+version $MF_VER}"
else
  printf '\033[31mRelease preflight failed: %d check(s).\033[0m\n' "$FAILED"
fi
exit "$FAILED"
