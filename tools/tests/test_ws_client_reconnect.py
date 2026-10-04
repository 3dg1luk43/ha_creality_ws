"""Tests for KClient reconnect / _force_connect logic in ws_client.py.

These tests import the *real* KClient (not the conftest stub) and patch
``websockets.connect`` to control whether a connection attempt is made.
"""
from __future__ import annotations

import asyncio
import logging
import pytest
import json
import importlib
import importlib.util
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

from conftest import install_stub_module, restore_stubs

# ---------------------------------------------------------------------------
# Bootstrap: make sure the real ws_client module is importable without HA
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The conftest stub at `custom_components.ha_creality_ws.ws_client` is left in
# place. The real implementation is loaded below under the separate name
# `ha_creality_ws.ws_client`, so dropping the canonical one bought nothing -- and
# it happened at *collection* time and was only undone in `teardown_module`, so
# any module collected in between imported the real client instead of the stub.

# Provide minimal stubs for any HA imports the module might pull in
for mod_name in [
    "homeassistant",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.dispatcher",
]:
    if mod_name not in sys.modules:
        # Through the helper, so `restore_stubs` below can undo it. A direct
        # `sys.modules` write is invisible to it and outlives this module.
        install_stub_module(__name__, mod_name, types.ModuleType(mod_name))

# Stub out websockets and its submodules so the real package isn't required.
# Only substitute when it is genuinely absent: the tests below patch via
# patch.object, so they work against the real package too, and installing the
# stub over a real install would break any other test that needs a working
# client (test_cfs_simulator.py talks to the simulator over a real socket).
if "websockets" not in sys.modules and importlib.util.find_spec("websockets") is None:
    _ws_stub = types.ModuleType("websockets")
    _ws_stub.connect = None  # will be patched per-test

    # websockets.exceptions
    _ws_exceptions = types.ModuleType("websockets.exceptions")

    class ConnectionClosedOK(Exception):
        """Stub for websockets.exceptions.ConnectionClosedOK."""

    class ConnectionClosed(Exception):
        """Stub for websockets.exceptions.ConnectionClosed."""

    _ws_exceptions.ConnectionClosedOK = ConnectionClosedOK
    _ws_exceptions.ConnectionClosed = ConnectionClosed
    _ws_stub.exceptions = _ws_exceptions
    install_stub_module(__name__, "websockets.exceptions", _ws_exceptions)

    # websockets.client (referenced in type annotation)
    _ws_client_sub = types.ModuleType("websockets.client")

    class ClientConnection:
        """Stub for websockets.client.ClientConnection."""

    _ws_client_sub.ClientConnection = ClientConnection
    _ws_stub.client = _ws_client_sub
    install_stub_module(__name__, "websockets.client", _ws_client_sub)

    install_stub_module(__name__, "websockets", _ws_stub)

# Now import the real module
import importlib.util

spec = importlib.util.spec_from_file_location(
    "ha_creality_ws.ws_client",
    ROOT / "custom_components" / "ha_creality_ws" / "ws_client.py",
)
ws_client_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws_client_module)  # type: ignore[union-attr]
KClient = ws_client_module.KClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_failing_connect(call_counter: list[int], exc: Exception | None = None):
    """Return a mock for ``websockets.connect`` that records calls and raises *exc*.

    Each time the returned context manager is entered, ``1`` is appended to
    *call_counter* so tests can assert how many connection attempts were made.
    If *exc* is ``None`` a minimal fake WebSocket is yielded that immediately
    closes the ``async for`` loop; otherwise *exc* is raised before yielding.
    """

    @asynccontextmanager
    async def _fake_connect(url, **kwargs):
        """Async context manager that records the call and optionally raises."""
        call_counter.append(1)
        if exc is not None:
            raise exc
        # Yield a minimal ws stub that immediately closes the async-for loop
        class _FakeWS:
            """Minimal WebSocket stub used by the failing-connect helper."""

            def __aiter__(self):
                """Return self as the async iterator."""
                return self

            async def __anext__(self):
                """Immediately signal the end of the message stream."""
                raise StopAsyncIteration

            async def close(self, *a, **k):
                """No-op close so teardown code does not raise."""

        yield _FakeWS()

    return _fake_connect


async def _until(condition, timeout: float = 5.0) -> None:
    """Wait for `condition()` to hold, or `timeout` seconds. A fixed short
    sleep failed on a loaded CI runner: resolving the host goes through the
    executor before the first connect, and can take longer than 0.2 s."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not condition() and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# Test 1 -- force_connect=True, power ON  →  connect IS attempted
# ---------------------------------------------------------------------------
def test_force_connect_attempts_connection_when_power_on():
    """With _force_connect=True and power ON, the loop must call websockets.connect."""

    async def run():
        """Run the test coroutine inside a fresh event loop."""
        call_counter: list[int] = []

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        # Power is ON
        client._check_power_status = lambda: False
        # Signal a forced reconnect
        client._force_connect = True

        fake_connect = _make_failing_connect(
            call_counter,
            exc=OSError("connection refused"),  # fail fast so loop goes to sleep
        )

        with patch.object(ws_client_module.websockets, "connect", fake_connect):
            # Start the loop; stop it shortly after so it doesn't retry forever
            await client.start()
            await _until(lambda: call_counter)
            await client.stop()

        assert len(call_counter) >= 1, (
            "websockets.connect should have been called at least once "
            "(force_connect=True + power ON must not skip the connection block)"
        )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 2 -- force_connect=True, power OFF  ->  connect IS attempted
# ---------------------------------------------------------------------------
def test_force_connect_attempts_connection_even_when_power_off():
    """A manual Reconnect bypasses the switch once: that is what the flag is
    for, and the switch may be lagging or wrong. 0.9.1 inverted this, so a
    forced attempt was the only one that checked the switch at all."""

    async def run():
        call_counter: list[int] = []

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        client._check_power_status = lambda: True
        client._force_connect = True

        fake_connect = _make_failing_connect(
            call_counter, exc=OSError("connection refused")
        )

        with patch.object(ws_client_module.websockets, "connect", fake_connect):
            await client.start()
            await _until(lambda: call_counter)
            await asyncio.sleep(0.1)  # time for a second attempt, which must not come
            await client.stop()

        assert len(call_counter) == 1

    asyncio.run(run())


def test_an_unforced_loop_polls_the_switch_instead_of_a_dark_printer():
    """#45. Without the check, the loop kept dialling a printer whose plug was
    off and backed off to 300 s, so power-on was followed by minutes of
    "unknown"."""

    async def run():
        call_counter: list[int] = []

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        client._check_power_status = lambda: True

        fake_connect = _make_failing_connect(call_counter)

        with patch.object(ws_client_module.websockets, "connect", fake_connect):
            await client.start()
            await asyncio.sleep(0.2)
            await client.stop()

        assert len(call_counter) == 0

    asyncio.run(run())


def test_the_first_attempt_after_power_returns_is_prompt():
    """While off, the loop re-reads the switch every POWER_OFF_POLL_SECS with
    the backoff reset, so the first connect follows power-on within one poll."""

    async def run():
        call_counter: list[int] = []
        power_off = [True]

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        client._check_power_status = lambda: power_off[0]

        fake_connect = _make_failing_connect(
            call_counter, exc=OSError("connection refused")
        )

        with patch.object(ws_client_module.websockets, "connect", fake_connect), \
                patch.object(ws_client_module, "POWER_OFF_POLL_SECS", 0.05):
            await client.start()
            await asyncio.sleep(0.2)
            assert call_counter == []
            power_off[0] = False
            await _until(lambda: call_counter)
            await client.stop()

        assert len(call_counter) >= 1

    asyncio.run(run())


def test_a_long_session_that_ends_in_an_error_resets_the_backoff():
    """A Wi-Fi blip after hours of uptime is not a fifth consecutive failure.
    The reset used to happen only on a clean close, so an error close retried
    after the backoff the earlier failures had built up."""

    async def run():
        sleeps: list[float] = []
        attempts: list[int] = []
        done = asyncio.Event()
        original_wait_for = asyncio.wait_for

        async def _recording_wait_for(fut, timeout):
            # Record the loop's chosen sleep, then return at once.
            sleeps.append(timeout)
            return await original_wait_for(fut, 0.001)

        class _OneFrameThenError:
            def __init__(self):
                self.sent = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self.sent:
                    self.sent = True
                    return '{"nozzleTemp": "200"}'
                raise OSError("connection reset by peer")

            async def send(self, *a, **k):
                """Periodic GETs may fire; nothing to do."""

            async def close(self, *a, **k):
                """No-op close."""

        @asynccontextmanager
        async def _fake_connect(url, **kwargs):
            attempts.append(1)
            n = len(attempts)
            if n == 5:
                done.set()
            if n == 4:
                yield _OneFrameThenError()
                return
            raise OSError("connection refused")

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)

        with patch.object(ws_client_module.websockets, "connect", _fake_connect), \
                patch.object(ws_client_module, "STABLE_CONNECT_SECS", 0.0), \
                patch.object(ws_client_module.asyncio, "wait_for", _recording_wait_for):
            await client.start()
            await original_wait_for(done.wait(), 2.0)
            await client.stop()

        # Three failures grow the backoff (~1.8, ~3.2, ~5.8 s); the sleep after
        # the healthy session must be back at the first step, not ~10 s.
        assert sleeps[2] > 5.0
        assert sleeps[3] <= (1.0 * 2.2)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 3 -- force_connect=False, power ON  →  connect IS attempted normally
# ---------------------------------------------------------------------------
def test_normal_loop_connects_when_power_on():
    """Baseline: without any force flag, a normal loop iteration attempts to connect."""

    async def run():
        """Run the test coroutine inside a fresh event loop."""
        call_counter: list[int] = []

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        # Power is ON, no force flag
        client._check_power_status = lambda: False
        client._force_connect = False

        fake_connect = _make_failing_connect(
            call_counter,
            exc=OSError("connection refused"),
        )

        with patch.object(ws_client_module.websockets, "connect", fake_connect):
            await client.start()
            await _until(lambda: call_counter)
            await client.stop()

        assert len(call_counter) >= 1, (
            "websockets.connect should have been called at least once "
            "during a normal loop iteration when power is ON"
        )

    asyncio.run(run())


def test_a_large_file_listing_is_received_not_a_disconnect():
    """R15. The printer answers the G-code listing request with every file in
    one frame, about 150 KiB per 200 files. Left at the websockets default of
    1 MiB, a printer with a large library had the connection closed with code
    1009 on each listing, and the coordinator asks three times per new file.
    Driven against a real websockets server, so the library's own limit is
    what is tested."""
    real_websockets = pytest.importorskip("websockets")
    if not hasattr(real_websockets, "serve"):
        pytest.skip("the websockets package is stubbed in this environment")

    async def run():
        listing = [{"name": f"part_{i:05d}.gcode", "size": 123456} for i in range(50000)]
        frame = json.dumps({"retGcodeFileInfo2": listing, "nozzleTemp": 210})
        assert len(frame) > 2 * 2**20  # comfortably over the old 1 MiB default

        async def handler(ws):
            await ws.send(frame)
            await ws.wait_closed()

        received: list[dict] = []

        async def _on_msg(payload):
            received.append(payload)

        async with real_websockets.serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = KClient("127.0.0.1", _on_msg)
            client._url = lambda host=None: f"ws://127.0.0.1:{port}"
            await client.start()
            for _ in range(40):
                if received:
                    break
                await asyncio.sleep(0.05)
            await client.stop()

        assert received, "the listing never arrived (closed with 1009)"
        assert len(received[0]["retGcodeFileInfo2"]) == 50000

    asyncio.run(run())


def test_resolving_the_host_never_blocks_the_event_loop():
    """R16. `socket.gethostbyname` ran on the loop at every connect attempt.
    Here the resolver takes 0.3 s; a ticker on the same loop must keep
    running while the client connects."""

    async def run():
        ticks: list[float] = []
        stop = asyncio.Event()

        async def ticker():
            while not stop.is_set():
                ticks.append(asyncio.get_running_loop().time())
                await asyncio.sleep(0.02)

        def slow_lookup(*_a, **_k):
            import time as _time

            _time.sleep(0.3)
            return [(2, 1, 6, "", ("192.0.2.7", 0))]

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        seen_urls: list[str] = []

        @asynccontextmanager
        async def _fake_connect(url, **kwargs):
            seen_urls.append(url)
            raise OSError("refused")
            yield  # pragma: no cover

        client = KClient("printer.local", _on_msg)
        tick_task = asyncio.create_task(ticker())
        with patch.object(ws_client_module.socket, "getaddrinfo", slow_lookup), \
                patch.object(ws_client_module.socket, "gethostbyname", lambda h: slow_lookup()[0][4][0]), \
                patch.object(ws_client_module.websockets, "connect", _fake_connect):
            await client.start()
            await asyncio.sleep(0.5)
            await client.stop()
        stop.set()
        await tick_task

        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        assert max(gaps) < 0.2, f"the loop stalled for {max(gaps):.2f}s"
        assert seen_urls and seen_urls[0] == "ws://192.0.2.7:9999"

    asyncio.run(run())


def test_get_url_does_not_resolve():
    """Diagnostics call it on the event loop."""

    async def _on_msg(payload):
        """No-op message handler used for testing."""

    lookups: list[str] = []

    def _lookup(host):
        lookups.append(host)
        return "192.0.2.7"

    with patch.object(ws_client_module.socket, "gethostbyname", _lookup):
        assert KClient("printer.local", _on_msg).get_url() == "ws://printer.local:9999"
    assert lookups == []


def test_the_file_listing_is_delivered_once_not_on_every_later_frame():
    """The listing reply (~150 KiB on a K1C) is one-shot. Left in the client's
    cumulative state it rode on every later frame, and the coordinator
    rescanned it once per frame on the receive path."""
    listing_key = ws_client_module.GCODE_FILE_RESPONSE
    messages = [
        json.dumps({"nozzleTemp": "210.000000"}),
        json.dumps({listing_key: [{"name": "a.gcode"}]}),
        json.dumps({"bedTemp0": "60.000000"}),
    ]

    @asynccontextmanager
    async def _connect(url, **kwargs):
        class _WS:
            def __aiter__(self):
                return self

            async def __anext__(self):
                if not messages:
                    raise StopAsyncIteration
                return messages.pop(0)

            async def send(self, *_a):
                """Heartbeats and polls go nowhere."""

            async def close(self, *a, **k):
                """No-op close."""

        yield _WS()

    async def run():
        received: list[dict] = []

        async def _on_msg(payload):
            received.append(payload)

        client = KClient("192.0.2.7", _on_msg)
        with patch.object(ws_client_module.websockets, "connect", _connect):
            await client.start()
            for _ in range(40):
                if len(received) >= 3:
                    break
                await asyncio.sleep(0.01)
            await client.stop()
        return received

    first, listing, after = asyncio.run(run())[:3]
    assert listing_key in listing
    assert listing_key not in after
    # Still cumulative otherwise: the later frame carries the earlier values.
    assert after["nozzleTemp"] == 210.0 and after["bedTemp0"] == 60.0


def _failed_attempt_warnings(caplog, power_off: bool) -> list[str]:
    """Warnings from one failed connect, the backoff already at its ceiling
    (where the "failing repeatedly" warning lives)."""

    async def run():
        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        client._check_power_status = lambda: power_off
        # Forced, so an attempt is made even with the power off: a manual
        # Reconnect, or the switch lagging behind the plug.
        client._force_connect = True
        attempts: list[int] = []
        fake_connect = _make_failing_connect(attempts, exc=OSError("connection refused"))
        with patch.object(ws_client_module.websockets, "connect", fake_connect), \
                patch.object(ws_client_module, "RETRY_MAX_BACKOFF", 1.0):
            await client.start()
            await _until(lambda: attempts)
            await asyncio.sleep(0.1)  # past the failure, into the warning
            await client.stop()

    caplog.set_level(logging.DEBUG)
    asyncio.run(run())
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_failed_attempt_with_the_power_on_warns(caplog):
    """The control for the test below: the same failure, power on."""
    assert any("failing repeatedly" in m for m in _failed_attempt_warnings(caplog, power_off=False))


def test_a_failed_attempt_with_the_power_off_says_nothing(caplog):
    """#84: "K WS connection failing repeatedly ... mDNS fallback" was logged
    21 times in an evening while the smart plug said the printer was off."""
    assert _failed_attempt_warnings(caplog, power_off=True) == []


def test_an_unreachable_printer_is_reported_once_not_every_retry(caplog):
    """#84 again: with a power switch saying on, the warning came back at
    every retry at the backoff ceiling, every five minutes for as long as the
    printer stayed unreachable, announcing an mDNS fallback that did not
    exist."""

    async def run():
        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        client._check_power_status = lambda: False
        attempts: list[int] = []
        fake_connect = _make_failing_connect(attempts, exc=OSError("connection refused"))
        with patch.object(ws_client_module.websockets, "connect", fake_connect), \
                patch.object(ws_client_module, "RETRY_MIN_BACKOFF", 0.01), \
                patch.object(ws_client_module, "RETRY_MAX_BACKOFF", 0.01):
            await client.start()
            await _until(lambda: len(attempts) >= 6)
            await client.stop()
        return attempts

    caplog.set_level(logging.DEBUG)
    attempts = asyncio.run(run())
    assert len(attempts) >= 5
    repeated = [r for r in caplog.records if "failing repeatedly" in r.getMessage()]
    assert len(repeated) == 1
    assert "mDNS" not in caplog.text


def _gets_on_connect(cfs_connect) -> list[dict]:
    """What the poller asks for in its first few passes after a connect."""

    async def run():
        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        sent: list[dict] = []

        async def _send_json(payload):
            sent.append(payload)

        client._send_json = _send_json
        client._ws = object()
        client._ws_ready.set()
        if cfs_connect is not None:
            client._state["cfsConnect"] = cfs_connect
        real_sleep = asyncio.sleep

        async def _no_wait(_seconds):
            await real_sleep(0)

        with patch.object(ws_client_module.asyncio, "sleep", _no_wait):
            task = asyncio.ensure_future(client._periodic_gets())
            for _ in range(10):
                await real_sleep(0)
            client._stop.set()
            await task
        return sent

    return asyncio.run(run())


BOXS_INFO = {"method": "get", "params": {"boxsInfo": 1}}


def test_the_cfs_is_asked_for_on_connect():
    """#99: boxsInfo only comes on request, and the first request waited out
    the full 5-minute interval after every connect, so the CFS sensors sat
    unavailable for that long each time. It regressed once already."""
    assert BOXS_INFO in _gets_on_connect(cfs_connect=None)
    assert BOXS_INFO in _gets_on_connect(cfs_connect=1)


def test_no_cfs_is_not_asked_for():
    assert BOXS_INFO not in _gets_on_connect(cfs_connect=0)


def teardown_module(_module):
    restore_stubs(__name__)
