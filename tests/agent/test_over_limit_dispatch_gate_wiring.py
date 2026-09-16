"""Wiring tests for over-limit-dispatch-refusal at its v2026.9.x call site.

The predicate tests in test_over_limit_dispatch_refusal.py pin the DECISION; these pin that the
preflight gate actually CONSULTS it after compaction falls through (re-ported at the v2026.9.21
resync, when upstream moved the pre-API guard chain into agent/turn_preflight_gate.py).
"""
from __future__ import annotations

from types import SimpleNamespace

import agent.turn_preflight_gate as gate


class _Budget:
    def __init__(self):
        self.refunds = 0

    def refund(self):
        self.refunds += 1


def _agent(window):
    return SimpleNamespace(
        context_compressor=SimpleNamespace(
            context_length=window, threshold_tokens=0,
            should_defer_preflight_to_real_usage=lambda _t: False,
        ),
        iteration_budget=_Budget(), _api_call_count=0, session_id="s",
        _emit_diagnostic_status=lambda *_a, **_k: None,
    )


def _run(monkeypatch, window, tokens):
    # Compaction is not under test: make it fall through unchanged.
    monkeypatch.setattr(gate, "run_preflight_compression", lambda agent, v, **kw: v)
    import agent.conversation_loop as cl
    monkeypatch.setattr(cl, "_ollama_context_limit_error", lambda *_a: None)
    a = _agent(window)
    msgs = []
    v = gate.run_preflight_gate(
        a, request_pressure_tokens=tokens, _moa_prepared_request=None,
        pending_moa_prepared_request=None, messages=msgs, system_message=None, user_message="u",
        active_system_prompt=None, conversation_history=[], api_call_count=1,
        compression_attempts=0, max_compression_attempts=3, effective_task_id="t",
        final_response=None, failed=False, _turn_exit_reason=None,
        _compression_timeout_exhausted=False, _preflight_compression_blocked=False,
        _provider_overflow_recovery_pending=False, _last_preflight_pressure=None,
    )
    return v, a, msgs


def test_over_limit_request_is_refused_at_the_gate(monkeypatch):
    v, a, msgs = _run(monkeypatch, window=262_144, tokens=365_063)
    assert v.action == "break"
    assert v.failed is True and v._turn_exit_reason == "over_limit_dispatch_refused"
    assert v.api_call_count == 0 and a.iteration_budget.refunds == 1
    assert msgs and "exceeded the model window" in msgs[-1]["content"]


def test_within_window_request_still_dispatches(monkeypatch):
    # anti-neutering: a gate that refuses everything must not pass
    v, _a, msgs = _run(monkeypatch, window=262_144, tokens=100_000)
    assert v.action == "fallthrough" and not msgs
