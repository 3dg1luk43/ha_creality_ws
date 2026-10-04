from pathlib import Path
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]

init_path = ROOT / "custom_components" / "ha_creality_ws" / "__init__.py"

INTEGRATION = ROOT / "custom_components" / "ha_creality_ws"


def test_platforms_list_unique():
    """PLATFORMS must contain no duplicate entries.

    Parsed with ast rather than scanned line-by-line: the old version only
    asserted inside a branch requiring `PLATFORMS` and `[` on the same line, so
    wrapping the list across lines -- the natural result of adding a ninth
    platform -- made the loop find nothing and the test pass having checked
    nothing.
    """
    import ast

    tree = ast.parse(init_path.read_text(encoding="utf-8"))
    items = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(t, ast.Name) and t.id == "PLATFORMS" for t in targets):
            continue
        value = node.value
        assert isinstance(value, (ast.List, ast.Tuple)), "PLATFORMS must be a literal list"
        items = [
            el.value if isinstance(el, ast.Constant) else ast.unparse(el)
            for el in value.elts
        ]
        break

    assert items is not None, "PLATFORMS assignment not found in __init__.py"
    assert items, "PLATFORMS is empty"
    assert len(items) == len(set(items)), f"duplicate platform entries: {items}"


def _url_is_local(url: str) -> bool:
    """A printer, Home Assistant itself, a placeholder, or this repository."""
    import ipaddress

    rest = url.split("://", 1)[1]
    host = re.split(r"[/:?#]", rest, maxsplit=1)[0]
    if host.startswith(("{", "${")) or host in ("host", "localhost"):
        return True  # built from the configured address, or a placeholder
    if rest.startswith("github.com/3dg1luk43/ha_creality_ws"):
        return True  # documentation and issue links
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback


def test_no_unexpected_cloud_urls():
    """The integration is local only: no URL may name a host on the internet.

    Every text file is scanned, the cards included, since a CDN import in a
    card would load on every dashboard. This used to scan the top-level Python
    files only, and allowed any URL containing "http://".
    """
    suspicious = []
    for p in sorted(INTEGRATION.rglob("*")):
        if p.suffix not in (".py", ".js", ".json", ".yaml") or not p.is_file():
            continue
        text = p.read_text(encoding="utf-8")
        for url in re.findall(r"(?:https?|wss?)://[^\s\"'`)<>]+", text):
            if not _url_is_local(url):
                suspicious.append(f"{p.relative_to(ROOT)}: {url}")
    assert not suspicious, "URLs to hosts outside the network:\n" + "\n".join(suspicious)


def test_the_url_check_rejects_the_cloud():
    """The check above, shown to fail where it should."""
    for url in ("http://cloud.creality.com/api", "https://cdn.jsdelivr.net/npm/x.js",
                "wss://8.8.8.8:443/", "https://github.com/someone/else"):
        assert not _url_is_local(url), url
    for url in ("http://{host}:8080/", "ws://${host}:9999", "http://192.168.1.50:8080/?action=stream",
                "http://localhost:11984/", "https://github.com/3dg1luk43/ha_creality_ws/issues"):
        assert _url_is_local(url), url


def test_no_em_dashes_anywhere():
    """House rule: no em dash (U+2014) in the repository.

    Enforced rather than trusted because it is invisible in review -- an em
    dash and a double hyphen look near-identical in a diff, and the character
    arrives easily from pasted prose or a model's own output.

    The replacement depends on what the dash was doing. In prose it is ` -- `,
    matching the comment style used throughout. Where a card renders a dash as
    its placeholder for an unknown value it is a plain `-`, because that string
    is also compared by equality in several places and the two must agree. In
    user-visible copy neither substitution reads well, so those were reworded
    instead: reach for a semicolon or a comma rather than pasting `--` into
    something a user will read.
    """
    import subprocess

    try:
        tracked = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"],
            capture_output=True, check=True,
        ).stdout.split(b"\0")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        # The file list comes from git, so there is nothing to scan without it.
        # Reached for real: the clean-tree checks in this suite extract
        # `git archive HEAD` into a directory with no `.git`, and there this
        # failed as though the tree were full of em dashes.
        pytest.skip(f"not a git work tree, nothing to scan: {exc}")

    offenders = []
    for rel in tracked:
        if not rel:
            continue
        path = ROOT / rel.decode()
        if not path.is_file():
            continue
        try:
            body = path.read_bytes()
        except OSError:
            continue
        if b"\xe2\x80\x94" not in body:
            continue
        if b"\x00" in body:
            # Binary. The rule is about text a reviewer reads, and compressed
            # data hits these three bytes by chance -- so the bundled WebP
            # could fail this on an unrelated re-encode and report a line of
            # image data as an em dash. A NUL is the same heuristic `git diff`
            # uses to call a file binary.
            continue
        for number, line in enumerate(body.split(b"\n"), 1):
            if b"\xe2\x80\x94" in line:
                offenders.append(
                    f"{rel.decode()}:{number}: "
                    f"{line.decode('utf-8', 'replace').strip()[:80]}"
                )

    assert not offenders, "em dashes found:\n" + "\n".join(offenders)


# --------------------------------------------------------------------------- #
# Home Assistant job callables
# --------------------------------------------------------------------------- #


def test_sync_handlers_given_to_home_assistant_are_callbacks():
    """A plain sync function handed to `async_track_time_interval` or
    `async_dispatcher_connect` is run as a Home Assistant job, which means an
    executor thread. All three of these reach loop-only APIs from there --
    `hass.loop.call_soon` is not even thread-safe -- and none of them blocks,
    so `@callback` is both necessary and safe. Asserted on the source because
    the decorator is trivial to drop while moving code around.
    """
    import re as _re

    expected = {
        "custom_components/ha_creality_ws/__init__.py": "_interval_check",
        "custom_components/ha_creality_ws/sensor.py": "_on_new_entities",
        "custom_components/ha_creality_ws/number.py": "_on_new_entities",
    }
    for rel, name in expected.items():
        body = (ROOT / rel).read_text(encoding="utf-8")
        pattern = _re.compile(
            r"@callback\s*\n\s*def " + _re.escape(name) + r"\b"
        )
        assert pattern.search(body), f"{rel}: {name} must be decorated with @callback"

