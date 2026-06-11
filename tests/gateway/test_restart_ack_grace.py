"""Tests for the grace window in ``cancel_background_tasks``.

The /restart handler queues its ack reply and ``request_restart`` begins
teardown ~50ms later.  Teardown's ``cancel_background_tasks`` used to
cancel the in-flight ``_process_message_background`` task mid-send, so
the "Restarting gateway" ack was reliably lost whenever no active agents
forced a longer drain (the send needs a full HTTPS round trip).  The
planned-restart path now passes ``grace_seconds`` so in-flight tasks can
finish naturally before cancellation.
"""
import asyncio

import pytest

from tests.gateway.restart_test_helpers import RestartTestAdapter


def _track(adapter, coro):
    task = asyncio.get_event_loop().create_task(coro)
    adapter._background_tasks.add(task)
    task.add_done_callback(adapter._background_tasks.discard)
    return task


@pytest.mark.asyncio
async def test_grace_lets_inflight_send_finish():
    """A task that completes inside the grace window is not cancelled."""
    adapter = RestartTestAdapter()
    delivered = asyncio.Event()

    async def fake_send():
        await asyncio.sleep(0.05)  # simulated network round trip
        delivered.set()

    task = _track(adapter, fake_send())
    await adapter.cancel_background_tasks(grace_seconds=2.0)

    assert delivered.is_set()
    assert not task.cancelled()


@pytest.mark.asyncio
async def test_no_grace_cancels_inflight_send():
    """Without a grace window the in-flight task is cancelled (old behavior)."""
    adapter = RestartTestAdapter()
    delivered = asyncio.Event()

    async def fake_send():
        await asyncio.sleep(0.5)
        delivered.set()

    task = _track(adapter, fake_send())
    await adapter.cancel_background_tasks()

    assert not delivered.is_set()
    assert task.cancelled()


@pytest.mark.asyncio
async def test_grace_expiry_still_cancels():
    """A task that outlives the grace window is cancelled; the call stays bounded."""
    adapter = RestartTestAdapter()

    async def slow_task():
        await asyncio.sleep(30)

    task = _track(adapter, slow_task())
    await asyncio.wait_for(
        adapter.cancel_background_tasks(grace_seconds=0.1), timeout=6.0
    )

    assert task.cancelled()


# --- decision layer (restart-ack-teardown-race, re-ported onto the run_shutdown mixin at the
# v2026.9.21 resync): the grace is only useful if the SHUTDOWN path actually passes it on a
# planned restart. Probe the layer that decides, not just the adapter that acts.

async def _teardown_graces(restart_requested: bool):
    from types import SimpleNamespace
    from gateway.run_shutdown import GatewayShutdownMixin

    seen = []

    class _Runner(GatewayShutdownMixin):
        pass

    r = _Runner.__new__(_Runner)
    r._restart_requested = restart_requested
    r._restart_detached = False
    r.adapters = {"p1": object()}
    r._profile_adapters = {"prof": {"p2": object()}}

    async def _finalize(_agents):
        return None

    async def _teardown(adapter, platform, *, profile=None, grace_seconds=0.0):
        seen.append(grace_seconds)

    r._finalize_shutdown_agents = _finalize
    r._bounded_adapter_teardown = _teardown
    ctx = SimpleNamespace(active_agents={}, elapsed=lambda: 0.0)
    await GatewayShutdownMixin._stop_finalize_agents_and_adapters(r, ctx)
    return seen


@pytest.mark.asyncio
async def test_planned_restart_passes_grace_to_every_adapter_teardown():
    assert await _teardown_graces(True) == [2.0, 2.0]


@pytest.mark.asyncio
async def test_plain_shutdown_passes_zero_grace():
    assert await _teardown_graces(False) == [0.0, 0.0]
