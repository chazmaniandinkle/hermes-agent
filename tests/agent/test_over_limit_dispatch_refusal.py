"""Guard A — over-limit dispatch refusal (#context-blowup).

Incident: a session grew to ~367,791 tokens of context while the resolved
model window was 262,144. Hermes computed the window by GUESSING (model
metadata fell back to 256K), let the context grow past it, and STILL dispatched
the over-limit request. The provider returned EMPTY responses and every retry
re-sent the full over-limit context for zero progress.

The guard (`_should_refuse_over_limit_dispatch`) is the decision point: it
compares the fully-assembled request's estimated tokens against the resolved
model window and returns the window (refuse) or None (dispatch).

Test 1 proves the guard fires for an over-limit request.  Test 2 is the
anti-neutering companion: it proves the SAME decision returns None (normal
dispatch) when the request is WITHIN the limit — so a tree that deleted the
dispatch step entirely (always refusing) cannot pass the suite.
"""

from __future__ import annotations

from agent.conversation_loop import _should_refuse_over_limit_dispatch

# Mirrors the incident numbers from the defect report.
WINDOW = 262_144
OVER_LIMIT = 365_063
WITHIN_LIMIT = 200_000


def _compressor(context_length: int = WINDOW):
    class _Compressor:
        pass

    c = _Compressor()
    c.context_length = context_length
    return c


def _never_defer(_tokens: int) -> bool:
    """The compressor's noisy-estimate deferral — default to "trust the estimate"."""
    return False


# ---------------------------------------------------------------------------
# Test 1 — over-limit request MUST be refused (not dispatched)
# ---------------------------------------------------------------------------

def test_over_limit_request_is_refused():
    """A request whose estimate exceeds the window must be refused.

    Returns the resolved window (truthy) → the caller fails the turn instead of
    dispatching. If the guard is reverted, this returns None and the test fails.
    """
    verdict = _should_refuse_over_limit_dispatch(
        _compressor(WINDOW), OVER_LIMIT, _never_defer
    )
    assert verdict is not None
    assert verdict == WINDOW


def test_over_limit_refusal_is_round_trip():
    """The refusal verdict equals the window the caller will log/surface."""
    assert _should_refuse_over_limit_dispatch(
        _compressor(262_144), 367_791, _never_defer
    ) == 262_144


# ---------------------------------------------------------------------------
# Test 2 — anti-neutering: within-limit request MUST still dispatch
# ---------------------------------------------------------------------------
# A test asserting only "over-limit blocked" also passes against a tree that
# deleted dispatch entirely. These assert the *allowed* side of the same
# predicate, which a neutered/always-refusing tree cannot satisfy.

def test_within_limit_request_is_allowed():
    """A request entirely inside the window must NOT be blocked.

    Returns None (allowed → normal dispatch). If the guard is neutered to
    always refuse, this returns the window and the test fails — proving the
    dispatch path is not dead.
    """
    assert _should_refuse_over_limit_dispatch(
        _compressor(WINDOW), WITHIN_LIMIT, _never_defer
    ) is None


def test_request_exactly_at_window_is_allowed():
    """Estimate == window is still within the limit (allowed)."""
    assert _should_refuse_over_limit_dispatch(
        _compressor(WINDOW), WINDOW, _never_defer
    ) is None


def test_unknown_window_does_not_block():
    """When the resolved window is unknown, we cannot classify — dispatch."""
    assert _should_refuse_over_limit_dispatch(
        _compressor(context_length=0), OVER_LIMIT, _never_defer
    ) is None
    assert _should_refuse_over_limit_dispatch(
        _compressor(context_length=-1), OVER_LIMIT, _never_defer
    ) is None


def test_noisy_estimate_deferral_is_honoured():
    """If the compressor says the estimate is noisy-high relative to a real
    count that fit (should_defer_preflight_to_real_usage), trust the real count
    and do NOT hard-block on the over-counting guess."""
    assert _should_refuse_over_limit_dispatch(
        _compressor(WINDOW), OVER_LIMIT, lambda _t: True
    ) is None


def test_broken_deferral_keeps_backstop_conservative():
    """A raising defer is treated as 'cannot rule out an over-limit guess' —
    the request is still refused rather than silently allowed past the guard."""

    def _broken_defer(_tokens: int) -> bool:
        raise RuntimeError("deferral unavailable")

    assert _should_refuse_over_limit_dispatch(
        _compressor(WINDOW), OVER_LIMIT, _broken_defer
    ) == WINDOW