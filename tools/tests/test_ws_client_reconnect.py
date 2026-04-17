"""Tests for KClient reconnect / _force_connect logic in ws_client.py.

These tests import the *real* KClient (not the conftest stub) and patch
``websockets.connect`` to control whether a connection attempt is made.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Bootstrap: make sure the real ws_client module is importable without HA
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Remove the conftest stub so we load the real module
sys.modules.pop("custom_components.ha_creality_ws.ws_client", None)

# Provide minimal stubs for any HA imports the module might pull in
for mod_name in [
    "homeassistant",
    "homeassistant.helpers",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.dispatcher",
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = types.ModuleType(mod_name)

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
# Test 1 — force_connect=True, power ON  →  connect IS attempted
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
# Test 2 — force_connect=True, power OFF  →  connect is NOT attempted
# ---------------------------------------------------------------------------
def test_force_connect_skips_connection_when_power_off():
    """With _force_connect=True but power OFF, the loop must NOT attempt to connect."""

    async def run():
        """Run the test coroutine inside a fresh event loop."""
        call_counter: list[int] = []

        async def _on_msg(payload):
            """No-op message handler used for testing."""

        client = KClient("192.168.1.99", _on_msg)
        # Power is OFF
        client._check_power_status = lambda: True
        # Signal a forced reconnect
        client._force_connect = True

        fake_connect = _make_failing_connect(call_counter)

        with patch.object(ws_client_module.websockets, "connect", fake_connect):
            await client.start()
            # The loop should sleep (10 s) before retrying; stop it before that
            await asyncio.sleep(0.2)
            await client.stop()

        assert len(call_counter) == 0, (
            "websockets.connect must NOT be called when force_connect=True "
            "but the power switch reports the printer as OFF"
        )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 3 — force_connect=False, power ON  →  connect IS attempted normally
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