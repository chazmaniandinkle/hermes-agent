"""Idle-trigger scheduling for the background self-improvement review.

Local patch PATCH-014: when auxiliary.background_review.idle_trigger_seconds
is > 0, the post-turn review fork is NOT spawned at turn end. It is scheduled
and fires only once the session has been idle that long — no active
foreground turn, no newer user message — so on a single-lane provider (one
local model instance serving every request serially) the review never queues
ahead of live user messages. Turns that complete before an idle window opens
coalesce into one review. Default 0 = stock immediately-post-turn behavior.
"""

from __future__ import annotations

import time

import agent.background_review as bg_review
import run_agent as run_agent_module
from run_agent import AIAgent
from agent.background_review import (
    IdleReviewScheduler,
    get_idle_review_scheduler,
    note_foreground_turn_end,
    note_foreground_turn_start,
)


def _bare_agent() -> AIAgent:
    agent = object.__new__(AIAgent)
    agent.model = "fake-model"
    agent.platform = "telegram"
    agent.provider = "openai"
    agent.base_url = ""
    agent.api_key = ""
    agent.api_mode = ""
    agent.session_id = "test-session"
    agent._parent_session_id = ""
    agent._credential_pool = None
    agent._memory_store = object()
    agent._memory_enabled = True
    agent._user_profile_enabled = False
    agent._cached_system_prompt = "test-cached-system-prompt"
    import datetime as _dt
    agent.session_start = _dt.datetime(2026, 1, 1, 12, 0, 0)
    agent._MEMORY_REVIEW_PROMPT = "review memory"
    agent._SKILL_REVIEW_PROMPT = "review skills"
    agent._COMBINED_REVIEW_PROMPT = "review both"
    agent.background_review_callback = None
    agent.status_callback = None
    agent._safe_print = lambda *_args, **_kwargs: None
    return agent


class ImmediateThread:
    def __init__(self, *, target, daemon=None, name=None):
        self._target = target

    def start(self):
        self._target()


class FakeTimer:
    """Manually-fired stand-in for threading.Timer.

    The scheduler arms timers via ``threading.Timer`` looked up at call time,
    so monkeypatching ``bg_review.threading.Timer`` (the same idiom the
    sibling tests use for ``run_agent.threading.Thread``) captures every arm.
    Tests fire by calling ``timer.fire()``.
    """

    instances: list = []

    def __init__(self, interval, function):
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False
        self.fired = False
        self.daemon = False
        self.name = ""
        FakeTimer.instances.append(self)

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        """Deliver the timer callback, as the real Timer thread would."""
        self.fired = True
        self.function()


def _install_fakes(monkeypatch, idle_seconds, coalesce=True):
    """Route timer construction to FakeTimer and pin the idle config."""
    FakeTimer.instances = []
    monkeypatch.setattr(bg_review.threading, "Timer", FakeTimer)
    monkeypatch.setattr(
        bg_review, "_idle_trigger_config", lambda: (idle_seconds, coalesce)
    )


class SpyOnSpawnNow:
    """Records _spawn_background_review_now calls without running a review."""

    def __init__(self, agent):
        self.calls: list = []
        agent._spawn_background_review_now = self._record

    def _record(self, messages_snapshot, review_memory=False, review_skills=False):
        self.calls.append(
            {
                "snapshot": messages_snapshot,
                "review_memory": review_memory,
                "review_skills": review_skills,
            }
        )


def _live_timer():
    """The most recently armed FakeTimer still able to fire (or None)."""
    live = [
        t
        for t in FakeTimer.instances
        if t.started and not t.cancelled and not t.fired
    ]
    return live[-1] if live else None


# ---------------------------------------------------------------------------
# Default (idle_trigger_seconds == 0): behavior unchanged
# ---------------------------------------------------------------------------


def test_default_zero_spawns_immediately(monkeypatch):
    """With the default config the review runs at turn end, exactly as before,
    and no scheduler state is attached to the agent."""
    events = []

    class FakeReviewAgent:
        def __init__(self, **kwargs):
            events.append("init")
            self._session_messages = []

        def run_conversation(self, **kwargs):
            events.append("run_conversation")

        def shutdown_memory_provider(self):
            events.append("shutdown_memory_provider")

        def close(self):
            events.append("close")

    monkeypatch.setattr(run_agent_module, "AIAgent", FakeReviewAgent)
    monkeypatch.setattr(run_agent_module.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(bg_review, "_idle_trigger_config", lambda: (0.0, True))

    agent = _bare_agent()
    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_memory=True,
    )

    assert events == [
        "init",
        "run_conversation",
        "shutdown_memory_provider",
        "close",
    ]
    assert getattr(agent, "_bg_review_scheduler", None) is None


def test_idle_config_parsing(monkeypatch):
    """_idle_trigger_config reads the auxiliary.background_review keys and
    degrades to (0, True) on anything unset or invalid."""
    import hermes_cli.config as config_module

    def _with(cfg):
        monkeypatch.setattr(config_module, "load_config", lambda: cfg)
        return bg_review._idle_trigger_config()

    # Missing entirely -> stock behavior.
    assert _with({}) == (0.0, True)
    # Enabled, coalesce defaulted.
    assert _with(
        {"auxiliary": {"background_review": {"idle_trigger_seconds": 120}}}
    ) == (120.0, True)
    # Enabled, coalesce explicitly off.
    assert _with(
        {
            "auxiliary": {
                "background_review": {
                    "idle_trigger_seconds": 90,
                    "coalesce": False,
                }
            }
        }
    ) == (90.0, False)
    # Invalid value -> stock behavior, no raise.
    assert _with(
        {"auxiliary": {"background_review": {"idle_trigger_seconds": "nope"}}}
    ) == (0.0, True)
    # Negative -> stock behavior.
    assert _with(
        {"auxiliary": {"background_review": {"idle_trigger_seconds": -5}}}
    ) == (0.0, True)


def test_default_config_carries_idle_keys():
    """The DEFAULT_CONFIG block ships idle_trigger_seconds=0 / coalesce=True
    so existing configs deep-merge to the stock behavior."""
    from hermes_cli.config import DEFAULT_CONFIG

    task = DEFAULT_CONFIG["auxiliary"]["background_review"]
    assert task["idle_trigger_seconds"] == 0
    assert task["coalesce"] is True


# ---------------------------------------------------------------------------
# Deferral + debounce
# ---------------------------------------------------------------------------


def test_idle_trigger_defers_spawn_at_turn_end(monkeypatch):
    """With idle_trigger_seconds > 0, turn end schedules — it must NOT start
    review inference."""
    _install_fakes(monkeypatch, 120.0)

    agent = _bare_agent()
    spy = SpyOnSpawnNow(agent)

    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_memory=True,
    )

    assert spy.calls == [], "review inference must not start at turn end"
    timer = _live_timer()
    assert timer is not None, "a debounce timer must be armed"
    assert timer.interval == 120.0
    assert timer.daemon is True
    scheduler = agent._bg_review_scheduler
    assert isinstance(scheduler, IdleReviewScheduler)
    assert len(scheduler._pending) == 1


def test_fire_defers_while_foreground_turn_active(monkeypatch):
    """A fire that lands while a foreground turn is running re-arms a full
    idle window instead of spawning."""
    _install_fakes(monkeypatch, 120.0)

    agent = _bare_agent()
    spy = SpyOnSpawnNow(agent)

    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_memory=True,
    )
    # New user message arrives: turn starts and is still running at fire time.
    note_foreground_turn_start(agent)

    timer = _live_timer()
    timer.fire()  # fire mid-turn

    assert spy.calls == []
    re_armed = _live_timer()
    assert re_armed is not None and re_armed is not timer
    assert re_armed.interval == 120.0

    # Turn ends; idle clock restarts from now, so the next fire re-arms
    # for the remaining window instead of spawning.
    note_foreground_turn_end(agent)
    re_armed.fire()
    assert spy.calls == []
    assert _live_timer() is not None


def test_new_message_resets_debounce_then_fires_after_idle(monkeypatch):
    """The debounce resets on new activity; once a full idle window has
    genuinely elapsed, the review fires."""
    _install_fakes(monkeypatch, 120.0)

    agent = _bare_agent()
    spy = SpyOnSpawnNow(agent)

    snapshot = [{"role": "user", "content": "hello"}]
    AIAgent._spawn_background_review(
        agent, messages_snapshot=snapshot, review_memory=True
    )
    scheduler = agent._bg_review_scheduler

    # A quick turn happened during the wait (start + end stamps).
    note_foreground_turn_start(agent)
    note_foreground_turn_end(agent)

    timer = _live_timer()
    timer.fire()  # activity too recent -> re-arm remainder, no spawn
    assert spy.calls == []
    assert _live_timer() is not None

    # Simulate the idle window fully elapsing.
    scheduler._last_activity = time.monotonic() - 130.0
    _live_timer().fire()

    assert len(spy.calls) == 1
    assert spy.calls[0]["snapshot"] is snapshot
    assert spy.calls[0]["review_memory"] is True
    assert spy.calls[0]["review_skills"] is False
    # Nothing left pending, no timer re-armed.
    assert len(scheduler._pending) == 0
    assert _live_timer() is None


# ---------------------------------------------------------------------------
# Coalescing
# ---------------------------------------------------------------------------


def test_coalesce_one_review_covers_n_turns(monkeypatch):
    """Three turns before an idle window -> ONE review, built from the newest
    (cumulative) snapshot, with trigger flags OR-merged."""
    _install_fakes(monkeypatch, 120.0, coalesce=True)

    agent = _bare_agent()
    spy = SpyOnSpawnNow(agent)

    snap1 = [{"role": "user", "content": "turn 1"}]
    snap2 = snap1 + [{"role": "assistant", "content": "r1"},
                     {"role": "user", "content": "turn 2"}]
    snap3 = snap2 + [{"role": "assistant", "content": "r2"},
                     {"role": "user", "content": "turn 3"}]

    AIAgent._spawn_background_review(agent, messages_snapshot=snap1, review_memory=True)
    AIAgent._spawn_background_review(agent, messages_snapshot=snap2, review_skills=True)
    AIAgent._spawn_background_review(agent, messages_snapshot=snap3)

    scheduler = agent._bg_review_scheduler
    assert len(scheduler._pending) == 1, "coalesce keeps ONE pending entry"
    assert scheduler._pending[0]["turns"] == 3

    scheduler._last_activity = time.monotonic() - 130.0
    _live_timer().fire()

    assert len(spy.calls) == 1, "exactly one review for the three turns"
    assert spy.calls[0]["snapshot"] is snap3, "newest snapshot covers all turns"
    assert spy.calls[0]["review_memory"] is True
    assert spy.calls[0]["review_skills"] is True
    assert _live_timer() is None, "no queued per-turn reviews left behind"


def test_no_coalesce_drains_one_per_idle_window(monkeypatch):
    """coalesce=false keeps per-turn entries and drains them one idle window
    at a time (never a burst)."""
    _install_fakes(monkeypatch, 120.0, coalesce=False)

    agent = _bare_agent()
    spy = SpyOnSpawnNow(agent)

    snap1 = [{"role": "user", "content": "turn 1"}]
    snap2 = [{"role": "user", "content": "turn 2"}]
    AIAgent._spawn_background_review(agent, messages_snapshot=snap1, review_memory=True)
    AIAgent._spawn_background_review(agent, messages_snapshot=snap2, review_skills=True)

    scheduler = agent._bg_review_scheduler
    assert len(scheduler._pending) == 2

    scheduler._last_activity = time.monotonic() - 130.0
    _live_timer().fire()
    assert len(spy.calls) == 1
    assert spy.calls[0]["snapshot"] is snap1
    assert _live_timer() is not None, "second entry waits for its own window"

    scheduler._last_activity = time.monotonic() - 130.0
    _live_timer().fire()
    assert len(spy.calls) == 2
    assert spy.calls[1]["snapshot"] is snap2
    assert _live_timer() is None


# ---------------------------------------------------------------------------
# Shutdown semantics (PATCH-012-compatible teardown)
# ---------------------------------------------------------------------------


def test_shutdown_drops_scheduled_review_cleanly(monkeypatch, caplog):
    """A scheduled-but-unfired review at shutdown is dropped: timer cancelled,
    pending cleared, an info log line (not an error), idempotent."""
    import logging

    _install_fakes(monkeypatch, 120.0)

    agent = _bare_agent()
    spy = SpyOnSpawnNow(agent)

    AIAgent._spawn_background_review(
        agent,
        messages_snapshot=[{"role": "user", "content": "hello"}],
        review_memory=True,
    )
    scheduler = agent._bg_review_scheduler
    timer = _live_timer()

    with caplog.at_level(logging.INFO, logger="agent.background_review"):
        scheduler.shutdown()
        scheduler.shutdown()  # idempotent — must not raise or double-log

    assert timer.cancelled is True
    assert len(scheduler._pending) == 0
    drop_lines = [
        r for r in caplog.records if "dropping scheduled background review" in r.message
    ]
    assert len(drop_lines) == 1
    assert all(r.levelno <= logging.INFO for r in drop_lines)

    # A late fire (timer raced the cancel) is a no-op.
    timer.fire()
    assert spy.calls == []

    # Post-shutdown schedule() drops instead of arming a new timer.
    before = len(FakeTimer.instances)
    scheduler.schedule([{"role": "user", "content": "late"}], review_memory=True)
    assert len(FakeTimer.instances) == before
    assert len(scheduler._pending) == 0


def test_agent_close_shuts_down_scheduler(monkeypatch):
    """AIAgent.close() cancels the debounce timer (idempotent teardown)."""
    calls = []

    class StubScheduler:
        def shutdown(self, reason="agent close"):
            calls.append(reason)

    agent = _bare_agent()
    agent._bg_review_scheduler = StubScheduler()

    AIAgent.close(agent)

    assert calls == ["agent close"]


def test_release_clients_shuts_down_scheduler():
    """Soft cache-eviction also drops a pending review (the agent object is
    discarded; the rebuilt agent re-schedules on future turns)."""
    calls = []

    class StubScheduler:
        def shutdown(self, reason="agent close"):
            calls.append(reason)

    agent = _bare_agent()
    agent._bg_review_scheduler = StubScheduler()

    AIAgent.release_clients(agent)

    assert calls == ["agent cache eviction"]


# ---------------------------------------------------------------------------
# Turn hooks are passive
# ---------------------------------------------------------------------------


def test_turn_hooks_are_noops_without_scheduler():
    """The per-turn activity stamps must never raise on agents that have no
    scheduler (idle trigger disabled, review forks, bare test agents)."""
    agent = _bare_agent()
    note_foreground_turn_start(agent)
    note_foreground_turn_end(agent)
    assert getattr(agent, "_bg_review_scheduler", None) is None


def test_get_scheduler_respects_disabled_config(monkeypatch):
    """get_idle_review_scheduler returns None (immediate mode) when the idle
    trigger is off, and reuses one cached instance when on."""
    monkeypatch.setattr(bg_review, "_idle_trigger_config", lambda: (0.0, True))
    agent = _bare_agent()
    assert get_idle_review_scheduler(agent) is None

    monkeypatch.setattr(bg_review, "_idle_trigger_config", lambda: (60.0, True))
    first = get_idle_review_scheduler(agent)
    second = get_idle_review_scheduler(agent)
    assert first is second
    assert first._idle_seconds == 60.0
