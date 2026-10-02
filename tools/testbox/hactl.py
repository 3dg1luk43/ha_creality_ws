#!/usr/bin/env python3
"""Driver for the Creality WS test box: onboarding, REST, WebSocket, the printer.

Ported from ha_washdata's test box and adapted. Everything talks to the
container on http://127.0.0.1:8322 and to the mock printer's test-control
endpoint on http://127.0.0.1:8323; nothing here can reach a real Home
Assistant or a real printer.

    ./hactl.py onboard                       mint the owner + a long-lived token
    ./hactl.py wait [--timeout S]            HA serving, and our entry set up
    ./hactl.py restart                       restart HA to reload edited code
    ./hactl.py setup [--host IP]             add the printer via the user flow
    ./hactl.py entry-id                      the integration's config entry id
    ./hactl.py options <entry> STEP JSON ... drive the options menu, one page per pair
    ./hactl.py state ENTITY                  one state (JSON)
    ./hactl.py states [PREFIX]               states, optionally filtered
    ./hactl.py call DOMAIN.SERVICE k=v ...   call a service
    ./hactl.py ws TYPE k=v ...               one WebSocket command
    ./hactl.py registry                      this integration's devices + entities
    ./hactl.py register-phone NAME --os iOS --manufacturer Apple
    ./hactl.py pushes [--since ISO]          what mobile_app posted to the "relay"
    ./hactl.py notifications [--since ISO]   what the plain notify target got
    ./hactl.py zeroconf --host IP [--hostname H]   inject a zeroconf discovery
    ./hactl.py flows                         in-progress config flows
    ./hactl.py printer-set k=v ...           force telemetry fields on the mock
    ./hactl.py printer-reset                 drop every forced field
    ./hactl.py errors [--since ISO]          ERROR/WARNING lines from the HA log

Values in `key=value` arguments are parsed as JSON when possible, so
`bedTemp0='""'` sends an empty string and `state=1` sends an int; anything
unparseable stays a string.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp

HERE = Path(__file__).resolve().parent
CONFIG_DIR = HERE / "config"
TOKEN_FILE = CONFIG_DIR / ".testbox_token"
NOTIFY_CAPTURE = CONFIG_DIR / "notify_capture.jsonl"
PUSH_CAPTURE = CONFIG_DIR / "push_capture.jsonl"
LOG_FILE = CONFIG_DIR / "home-assistant.log"
BASE_URL = os.environ.get("TESTBOX_URL", "http://127.0.0.1:8322")
PRINTER_URL = os.environ.get("TESTBOX_PRINTER_URL", "http://127.0.0.1:8323")
PRINTER_IP = "172.31.77.10"
CLIENT_ID = f"{BASE_URL}/"
DOMAIN = "ha_creality_ws"

OWNER = {"name": "Test Box", "username": "testbox", "password": "testbox-pw-0123"}


# -- plumbing -----------------------------------------------------------------
def token() -> str:
    if not TOKEN_FILE.exists():
        sys.exit(f"no token at {TOKEN_FILE} - run `./hactl.py onboard` first")
    return TOKEN_FILE.read_text().strip()


def _coerce(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return raw


def _kv(pairs: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            sys.exit(f"expected key=value, got {pair!r}")
        key, raw = pair.split("=", 1)
        out[key] = _coerce(raw)
    return out


async def rest(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    payload: Any = None,
    auth: bool = True,
    base: str = BASE_URL,
) -> Any:
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {token()}"
    async with session.request(
        method, f"{base}{path}", headers=headers, json=payload
    ) as resp:
        body = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"{method} {path} -> {resp.status}: {body[:400]}")
        return json.loads(body) if body.strip() else None


class WS:
    """Minimal authenticated WebSocket client."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._id = 0

    async def __aenter__(self) -> "WS":
        self._ws = await self._session.ws_connect(
            f"{BASE_URL}/api/websocket", heartbeat=30
        )
        hello = await self._ws.receive_json()
        assert hello["type"] == "auth_required", hello
        await self._ws.send_json({"type": "auth", "access_token": token()})
        result = await self._ws.receive_json()
        if result["type"] != "auth_ok":
            raise RuntimeError(f"websocket auth failed: {result}")
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._ws is not None:
            await self._ws.close()

    async def cmd(self, type_: str, **fields: Any) -> Any:
        assert self._ws is not None
        self._id += 1
        msg_id = self._id
        await self._ws.send_json({"id": msg_id, "type": type_, **fields})
        while True:
            msg = await self._ws.receive_json()
            if msg.get("id") != msg_id or msg.get("type") != "result":
                continue
            if not msg.get("success"):
                raise RuntimeError(f"{type_} failed: {msg.get('error')}")
            return msg.get("result")


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


async def _entry_id(session: aiohttp.ClientSession) -> str | None:
    entries = await rest(
        session, "GET", f"/api/config/config_entries/entry?domain={DOMAIN}"
    )
    return entries[0]["entry_id"] if entries else None


# -- commands -----------------------------------------------------------------
async def cmd_onboard(_args: argparse.Namespace) -> int:
    """Create the owner user and store a long-lived token (idempotent)."""
    if TOKEN_FILE.exists():
        async with aiohttp.ClientSession() as session:
            try:
                await rest(session, "GET", "/api/")
                print(f"already onboarded, token at {TOKEN_FILE}")
                return 0
            except RuntimeError:
                print("stored token is stale, re-onboarding")

    async with aiohttp.ClientSession() as session:
        step = await rest(
            session,
            "POST",
            "/api/onboarding/users",
            {
                "client_id": CLIENT_ID,
                "name": OWNER["name"],
                "username": OWNER["username"],
                "password": OWNER["password"],
                "language": "en",
            },
            auth=False,
        )
        async with session.post(
            f"{BASE_URL}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": step["auth_code"],
                "client_id": CLIENT_ID,
            },
        ) as resp:
            tokens = await resp.json()
            if "access_token" not in tokens:
                raise RuntimeError(f"token exchange failed: {tokens}")
        TOKEN_FILE.write_text(tokens["access_token"])
        async with WS(session) as ws:
            long_lived = await ws.cmd(
                "auth/long_lived_access_token",
                client_name=f"testbox-{uuid.uuid4().hex[:8]}",
                lifespan=365,
            )
        TOKEN_FILE.write_text(long_lived)
        TOKEN_FILE.chmod(0o600)
        for path in ("/api/onboarding/core_config", "/api/onboarding/analytics"):
            try:
                await rest(session, "POST", path, {})
            except RuntimeError:
                pass
    print(f"onboarded. token at {TOKEN_FILE}")
    print(f"UI: {BASE_URL}  user: {OWNER['username']}  password: {OWNER['password']}")
    return 0


async def cmd_wait(args: argparse.Namespace) -> int:
    """Wait for HTTP, then for our entry to finish setting up."""
    deadline = time.monotonic() + args.timeout
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            try:
                async with session.get(f"{BASE_URL}/manifest.json") as resp:
                    if resp.status == 200:
                        break
            except aiohttp.ClientError:
                pass
            await asyncio.sleep(2)
        else:
            print("timed out waiting for the box", file=sys.stderr)
            return 1
        if not TOKEN_FILE.exists():
            print("box is up (not onboarded yet)")
            return 0
        while time.monotonic() < deadline:
            try:
                entries = await rest(
                    session, "GET", f"/api/config/config_entries/entry?domain={DOMAIN}"
                )
            except RuntimeError:
                await asyncio.sleep(2)
                continue
            if not entries:
                print("box is up (no printer entry yet)")
                return 0
            states = {e["state"] for e in entries}
            if states <= {"loaded"}:
                print("box is up and the printer entry is loaded")
                return 0
            if states & {"setup_error", "migration_error", "failed_unload"}:
                print(f"entry failed: {states}", file=sys.stderr)
                return 1
            await asyncio.sleep(2)
    print("box is up but the entry never finished setting up", file=sys.stderr)
    return 1


async def cmd_restart(_args: argparse.Namespace) -> int:
    subprocess.run(["docker", "compose", "restart", "ha"], cwd=HERE, check=True)
    return await cmd_wait(argparse.Namespace(timeout=240))


async def cmd_setup(args: argparse.Namespace) -> int:
    """Add the printer through the real user step."""
    async with aiohttp.ClientSession() as session:
        existing = await _entry_id(session)
        if existing and not args.force:
            print(existing)
            return 0
        flow = await rest(
            session, "POST", "/api/config/config_entries/flow",
            {"handler": DOMAIN, "show_advanced_options": False},
        )
        result = await rest(
            session, "POST", f"/api/config/config_entries/flow/{flow['flow_id']}",
            {"host": args.host, "name": args.name},
        )
        if result.get("type") != "create_entry":
            _print(result)
            return 1
        print(result["result"]["entry_id"])
    return 0


async def cmd_entry_id(_args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session:
        entry = await _entry_id(session)
    if entry is None:
        return 1
    print(entry)
    return 0


async def cmd_options(args: argparse.Namespace) -> int:
    """Open the options menu and submit pages: `options <entry> power '{...}'`.

    Each STEP JSON pair picks a menu item, then submits that page. Every page
    saves on submit, so the flow is simply abandoned at the end.
    """
    pairs = args.pages
    if len(pairs) % 2:
        sys.exit("pages come in STEP JSON pairs")
    async with aiohttp.ClientSession() as session:
        for i in range(0, len(pairs), 2):
            step_id, payload = pairs[i], json.loads(pairs[i + 1])
            flow = await rest(
                session, "POST", "/api/config/config_entries/options/flow",
                {"handler": args.entry},
            )
            fid = flow["flow_id"]
            page = await rest(
                session, "POST", f"/api/config/config_entries/options/flow/{fid}",
                {"next_step_id": step_id},
            )
            result = await rest(
                session, "POST", f"/api/config/config_entries/options/flow/{fid}",
                payload,
            )
            if result.get("errors"):
                _print(result)
                return 1
            try:
                await rest(session, "DELETE", f"/api/config/config_entries/options/flow/{fid}")
            except RuntimeError:
                pass
            print(f"{step_id}: saved ({page.get('step_id')} -> {result.get('type')})")
    return 0


async def cmd_state(args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session:
        _print(await rest(session, "GET", f"/api/states/{args.entity}"))
    return 0


async def cmd_states(args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session:
        states = await rest(session, "GET", "/api/states")
    for st in sorted(states, key=lambda s: s["entity_id"]):
        if args.prefix and not st["entity_id"].startswith(args.prefix):
            continue
        print(f"{st['entity_id']:<60} {st['state']}")
    return 0


async def cmd_call(args: argparse.Namespace) -> int:
    domain, service = args.service.split(".", 1)
    async with aiohttp.ClientSession() as session:
        result = await rest(
            session, "POST", f"/api/services/{domain}/{service}", _kv(args.data)
        )
    _print(result)
    return 0


async def cmd_ws(args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session, WS(session) as ws:
        _print(await ws.cmd(args.type, **_kv(args.data)))
    return 0


async def cmd_registry(_args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session, WS(session) as ws:
        entries = await ws.cmd("config/entity_registry/list")
        devices = await ws.cmd("config/device_registry/list")
    ours = [e for e in entries if e.get("platform") == DOMAIN]
    dev_ids = {e["device_id"] for e in ours if e.get("device_id")}
    out = {
        "devices": [
            {"id": d["id"], "identifiers": d["identifiers"], "name": d["name"]}
            for d in devices
            if d["id"] in dev_ids or any(i[0] == DOMAIN for i in d["identifiers"])
        ],
        "entities": sorted(
            ({"entity_id": e["entity_id"], "unique_id": e["unique_id"],
              "device_id": e.get("device_id")} for e in ours),
            key=lambda e: e["entity_id"],
        ),
    }
    _print(out)
    return 0


async def cmd_register_phone(args: argparse.Namespace) -> int:
    """Register a fake companion app whose pushes land in push_capture.jsonl."""
    slug = args.name.lower().replace(" ", "_")
    ios = args.os.lower() in ("ios", "ipados")
    app_data: dict[str, Any] = {
        "push_token": f"token-{slug}",
        # Seen from inside the container, where mobile_app makes the POST.
        "push_url": f"http://127.0.0.1:8123/api/testbox_push/{slug}",
    }
    if ios:
        app_data["start_live_activity_token"] = f"pts-{slug}"
    body = {
        "app_id": "io.robbie.HomeAssistant" if ios else "io.homeassistant.companion.android",
        "app_name": "Home Assistant",
        "app_version": "2026.9.0",
        "device_name": args.name,
        "manufacturer": args.manufacturer,
        "model": args.model,
        "os_name": args.os,
        "os_version": "18.7" if ios else "16",
        "supports_encryption": False,
        "device_id": uuid.uuid4().hex,
        "app_data": app_data,
    }
    async with aiohttp.ClientSession() as session:
        result = await rest(session, "POST", "/api/mobile_app/registrations", body)
    _print(result)
    return 0


def _read_jsonl(path: Path, since: str | None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if since and record.get("ts", "") < since:
            continue
        out.append(record)
    return out


async def cmd_pushes(args: argparse.Namespace) -> int:
    _print(_read_jsonl(PUSH_CAPTURE, args.since))
    return 0


async def cmd_notifications(args: argparse.Namespace) -> int:
    _print(_read_jsonl(NOTIFY_CAPTURE, args.since))
    return 0


async def cmd_zeroconf(args: argparse.Namespace) -> int:
    data: dict[str, Any] = {"host": args.host}
    if args.hostname:
        data["hostname"] = args.hostname
    if args.properties:
        data["properties"] = json.loads(args.properties)
    async with aiohttp.ClientSession() as session:
        result = await rest(
            session, "POST",
            "/api/services/testbox_tools/zeroconf_discover?return_response", data,
        )
    _print(result.get("service_response", result))
    return 0


async def cmd_flows(_args: argparse.Namespace) -> int:
    """In-progress flows (REST has no GET for these; the WebSocket does)."""
    async with aiohttp.ClientSession() as session, WS(session) as ws:
        flows = await ws.cmd("config_entries/flow/progress")
    _print([f for f in flows if f.get("handler") == DOMAIN])
    return 0


async def cmd_printer_set(args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session:
        _print(await rest(session, "POST", "/test/set", _kv(args.data), auth=False, base=PRINTER_URL))
    return 0


async def cmd_printer_reset(_args: argparse.Namespace) -> int:
    async with aiohttp.ClientSession() as session:
        _print(await rest(session, "POST", "/test/reset", {}, auth=False, base=PRINTER_URL))
    return 0


async def cmd_errors(args: argparse.Namespace) -> int:
    if not LOG_FILE.exists():
        print(f"no log at {LOG_FILE}")
        return 0
    hits = []
    for line in LOG_FILE.read_text(errors="replace").splitlines():
        if args.since and line[:23] < args.since:
            continue
        if any(k in line for k in ("ERROR", "WARNING", "Traceback", "MultipleInvalid")):
            hits.append(line)
    print("\n".join(hits) if hits else "clean: no errors or warnings")
    return 1 if hits and args.strict else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("onboard").set_defaults(func=cmd_onboard)
    sub.add_parser("restart").set_defaults(func=cmd_restart)
    p = sub.add_parser("wait")
    p.add_argument("--timeout", type=float, default=180)
    p.set_defaults(func=cmd_wait)
    p = sub.add_parser("setup")
    p.add_argument("--host", default=PRINTER_IP)
    p.add_argument("--name", default="Test K1C")
    p.add_argument("--force", action="store_true", help="add another entry")
    p.set_defaults(func=cmd_setup)
    sub.add_parser("entry-id").set_defaults(func=cmd_entry_id)
    p = sub.add_parser("options")
    p.add_argument("entry")
    p.add_argument("pages", nargs="+")
    p.set_defaults(func=cmd_options)
    p = sub.add_parser("state")
    p.add_argument("entity")
    p.set_defaults(func=cmd_state)
    p = sub.add_parser("states")
    p.add_argument("prefix", nargs="?")
    p.set_defaults(func=cmd_states)
    p = sub.add_parser("call")
    p.add_argument("service")
    p.add_argument("data", nargs="*")
    p.set_defaults(func=cmd_call)
    p = sub.add_parser("ws")
    p.add_argument("type")
    p.add_argument("data", nargs="*")
    p.set_defaults(func=cmd_ws)
    sub.add_parser("registry").set_defaults(func=cmd_registry)
    p = sub.add_parser("register-phone")
    p.add_argument("name")
    p.add_argument("--os", default="iOS")
    p.add_argument("--manufacturer", default="Apple")
    p.add_argument("--model", default="iPhone15,2")
    p.set_defaults(func=cmd_register_phone)
    for name, func in (("pushes", cmd_pushes), ("notifications", cmd_notifications)):
        p = sub.add_parser(name)
        p.add_argument("--since")
        p.set_defaults(func=func)
    p = sub.add_parser("zeroconf")
    p.add_argument("--host", required=True)
    p.add_argument("--hostname")
    p.add_argument("--properties")
    p.set_defaults(func=cmd_zeroconf)
    sub.add_parser("flows").set_defaults(func=cmd_flows)
    p = sub.add_parser("printer-set")
    p.add_argument("data", nargs="+")
    p.set_defaults(func=cmd_printer_set)
    sub.add_parser("printer-reset").set_defaults(func=cmd_printer_reset)
    p = sub.add_parser("errors")
    p.add_argument("--since")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(func=cmd_errors)
    args = parser.parse_args()
    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
