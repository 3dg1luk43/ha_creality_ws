"""Tests for KClient reconnect / _force_connect logic in ws_client.py.

These tests import the *real* KClient (not the conftest stub) and patch
``websockets.connect`` to control whether a connection attempt is made.
"""
from __future__ import annotations

import asyncio
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
            # Give it enough time to hit the connect call (< 0.3 s)
            await asyncio.sleep(0.2)
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
            await asyncio.sleep(0.2)
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
            await asyncio.sleep(0.2)
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
            await asyncio.sleep(0.2)
            await client.stop()

        assert len(call_counter) >= 1, (
            "websockets.connect should have been called at least once "
            "during a normal loop iteration when power is ON"
        )

    asyncio.run(run())


def teardown_module(_module):
    restore_stubs(__name__)
