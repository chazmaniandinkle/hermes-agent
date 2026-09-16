"""Guard B — compaction-failure escalation (#context-blowup).

Incident: session hygiene's compaction kept aborting (summary model timed out /
returned no output) and the code logged 'continuing without compression' before
falling through to dispatch the full, over-limit context — over and over.

Guard B escalates after N CONSECUTIVE aborts: for the first N-1 it keeps the
current warn-and-continue behaviour; at/after N it must refuse to keep
re-sending the over-limit context. The consecutive-abort count is the existing
persistent ``hygiene_failure_streak`` (already incremented on every abort and
reset to 0 after a compaction that recovered), so these tests exercise the real
state machinery the same way ``test_hygiene_failure_cooldown_ladder.py`` does.

Test 3's reset assertion is the anti-neutraliser: if the streak never reset or
the escalation never tripped, one of the assertions below fails.
"""

from __future__ import annotations

import pytest

from gateway.run import (
    _HYGIENE_ESCALATE_AFTER,
    _hygiene_cooldown_for_failure,
    _peek_hygiene_failure_streak,
    _reset_hygiene_failure_streak,
    SessionHygieneCompactionExhausted,
    hygiene_abort_escalation_reached,
    hygiene_escalation_user_message,
)
from gateway.run import GatewayRunner

KEY = "agent:main:telegram:private:123"
BASE = 300.0


def _Runner():
    """A real ``GatewayRunner`` with no ``__init__`` side effects — identical
    to the idiom in ``test_hygiene_failure_cooldown_ladder.py``: the real
    ``_session_state`` self-heals its ``_sessions`` map, so no attribute setup
    is needed and these tests exercise the production accessors, not copies."""
    return object.__new__(GatewayRunner)


# ---------------------------------------------------------------------------
# The decision (pure predicate)
# ---------------------------------------------------------------------------

def test_first_aborts_keep_current_behaviour():
    """For the first N-1 aborts escalation must NOT fire (warn-and-continue)."""
    for streak in range(1, _HYGIENE_ESCALATE_AFTER):
        assert hygiene_abort_escalation_reached(streak) is False


def test_nth_abort_escalates():
    """At/after N aborts the escalation MUST fire."""
    assert hygiene_abort_escalation_reached(_HYGIENE_ESCALATE_AFTER) is True
    assert hygiene_abort_escalation_reached(_HYGIENE_ESCALATE_AFTER + 5) is True


def test_zero_streak_never_escalates():
    assert hygiene_abort_escalation_reached(0) is False


def test_threshold_zero_disables_escalation():
    """Operators can turn escalation off via threshold <= 0."""
    assert hygiene_abort_escalation_reached(100, threshold=0) is False


# ---------------------------------------------------------------------------
# The consecutive-abort counter: increments on abort, RESETS on success
# ---------------------------------------------------------------------------

def test_streak_accrues_across_aborts_and_then_escalates():
    """Real machinery: each abort bumps the persistent streak; escalation trips
    at exactly N and not before."""
    runner = _Runner()
    for _ in range(_HYGIENE_ESCALATE_AFTER - 1):
        _hygiene_cooldown_for_failure(runner, KEY, BASE)
    streak = _peek_hygiene_failure_streak(runner, KEY)
    assert streak == _HYGIENE_ESCALATE_AFTER - 1
    assert hygiene_abort_escalation_reached(streak) is False

    # Nth abort (this is where session hygiene records another failure).
    _hygiene_cooldown_for_failure(runner, KEY, BASE)
    streak = _peek_hygiene_failure_streak(runner, KEY)
    assert streak == _HYGIENE_ESCALATE_AFTER
    assert hygiene_abort_escalation_reached(streak) is True


def test_streak_resets_after_successful_compaction():
    """THE reset assertion: after a compaction that actually recovered the
    session the streak must go back to 0, so a *later* healthy period does not
    keep the session wedged in escalation. If the reset were removed (always
    escalated) this fails; if the escalation never tripped the accrual test
    above fails — together they can't both pass against a broken tree.

    Test 3 as specified: first abort keeps current behaviour, Nth escalates,
    and a success resets the counter.
    """
    runner = _Runner()
    for _ in range(_HYGIENE_ESCALATE_AFTER):
        _hygiene_cooldown_for_failure(runner, KEY, BASE)
    assert _peek_hygiene_failure_streak(runner, KEY) == _HYGIENE_ESCALATE_AFTER
    assert hygiene_abort_escalation_reached(
        _peek_hygiene_failure_streak(runner, KEY)
    ) is True

    # The session hygiene success path resets the streak.
    _reset_hygiene_failure_streak(runner, KEY)
    assert _peek_hygiene_failure_streak(runner, KEY) == 0
    assert hygiene_abort_escalation_reached(
        _peek_hygiene_failure_streak(runner, KEY)
    ) is False

    # A subsequent fresh abort restarts from 1 (not from N).
    _hygiene_cooldown_for_failure(runner, KEY, BASE)
    assert _peek_hygiene_failure_streak(runner, KEY) == 1


def test_peek_returns_zero_for_unknown_session():
    """A session with no recorded state must read as streak 0 (never escalate)
    so a missing state store cannot wedge the session."""
    runner = _Runner()
    assert _peek_hygiene_failure_streak(runner, KEY) == 0


def test_peek_swallows_state_store_errors():
    """If the state store is broken, peek must not raise — return 0."""

    class _Broken:
        def _peek_session_state(self, _key):
            raise RuntimeError("state store down")

    assert _peek_hygiene_failure_streak(_Broken(), KEY) == 0


# ---------------------------------------------------------------------------
# The escalation exception & operator message
# ---------------------------------------------------------------------------

def test_escalation_message_names_the_threshold():
    msg = hygiene_escalation_user_message()
    assert str(_HYGIENE_ESCALATE_AFTER) in msg
    assert "/reset" in msg


def test_escalation_exception_exists_and_carries_session_context():
    """The turn-abort exception is a real, distinct Exception subclass the
    gateway wires expect to raise on escalation."""
    exc = SessionHygieneCompactionExhausted(
        f"session {KEY} compaction aborted {_HYGIENE_ESCALATE_AFTER} consecutive times"
    )
    assert isinstance(exc, Exception)
    assert str(_HYGIENE_ESCALATE_AFTER) in str(exc)
    assert str(exc) != ""