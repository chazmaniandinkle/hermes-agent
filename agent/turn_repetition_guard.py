"""Assistant-turn repetition guard (Ornith derail case study, fix F1).

A local seat under retrieval/perturbation pressure sometimes re-converges to
its last known-good opening and repeats it verbatim across several turns
("Good. Let me do a quick diagnostic sweep..." x3 in one derailed session).
The kernel's own agent loop already halts on repeated identical tool calls
(the no-progress guardrail); the Hermes lane had no equivalent for repeated
*text*. This module mirrors that semantics for assistant narration/final
text, and folds identical repeated tool calls into the same streak so a
model that alternates between "repeat the sentence" and "repeat the tool
call" doesn't dodge the guard.

Deliberately stateless and side-effect free, in the same spirit as
``agent.tool_guardrails``: every decision is derived by re-reading the tail
of the durable ``messages`` transcript, not from an in-memory counter on the
agent instance. Gateway sessions rebuild the ``AIAgent`` fresh per turn, so
an instance-level streak counter would silently reset every turn and miss
exactly the cross-turn repetition this guard exists to catch.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from agent.tool_guardrails import canonical_tool_args

# One corrective line, injected on the first duplicate. Per the calibration
# principle (every token steers a small model), this stays a single plain
# sentence -- no boilerplate, nothing that itself becomes a new repeated-
# token attractor.
REPETITION_WARNING_LINE = (
    "[System: Your last two replies were identical. Do not repeat; advance or stop.]"
)


@dataclass(frozen=True)
class RepetitionGuardConfig:
    """Thresholds for the assistant repetition guard.

    ``warn_after``/``halt_after`` count *occurrences* of the same output
    (1 = the original, not yet a duplicate). Defaults mirror the kernel's
    no-progress guard: warn on the first duplicate (occurrence 2), halt on
    the second consecutive duplicate (occurrence 3) -- limit 3, same as the
    kernel's beat-001 example (6 identical calls killed well past its
    limit-3 guard).
    """

    enabled: bool = True
    warn_after: int = 2
    halt_after: int = 3
    near_duplicate_threshold: float = 0.92

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RepetitionGuardConfig":
        if not isinstance(data, Mapping):
            return cls()
        defaults = cls()
        return cls(
            enabled=_as_bool(data.get("enabled"), defaults.enabled),
            warn_after=_positive_int(data.get("warn_after"), defaults.warn_after),
            halt_after=_positive_int(data.get("halt_after"), defaults.halt_after),
            near_duplicate_threshold=_as_float(
                data.get("near_duplicate_threshold"), defaults.near_duplicate_threshold
            ),
        )


@dataclass(frozen=True)
class RepetitionDecision:
    """Decision returned by the repetition guard for a candidate turn."""

    action: str = "allow"  # allow | warn | halt
    occurrences: int = 1
    kind: str = ""  # "text" | "tool_call" | ""
    message: str = ""

    @property
    def should_halt(self) -> bool:
        return self.action == "halt"

    @property
    def should_warn(self) -> bool:
        return self.action == "warn"


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _positive_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 1 else default


def _as_float(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if 0.0 < parsed <= 1.0 else default


def _normalize_text(text: str) -> str:
    """Collapse whitespace/case/punctuation noise so near-identical
    replies (trailing period, re-wrapped line breaks) still compare equal."""
    lowered = (text or "").strip().lower()
    lowered = re.sub(r"\s+", " ", lowered)
    lowered = re.sub(r"[.!?,:;]+$", "", lowered)
    return lowered


def _is_near_duplicate(a: str, b: str, threshold: float) -> bool:
    na, nb = _normalize_text(a), _normalize_text(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # SequenceMatcher is O(n*m) worst case; assistant turns are bounded by
    # normal reply length so this is cheap in practice.
    return difflib.SequenceMatcher(None, na, nb).ratio() >= threshold


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "".join(parts)
    return ""


def _prior_assistant_texts(messages: Sequence[Mapping[str, Any]], limit: int) -> list[str]:
    """Most-recent-first non-empty assistant text turns, skipping tool-call
    turns that carried no narration text."""
    out: list[str] = []
    for msg in reversed(messages):
        if not isinstance(msg, Mapping) or msg.get("role") != "assistant":
            continue
        text = _extract_text(msg.get("content")).strip()
        if not text:
            continue
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _prior_tool_call_signatures(
    messages: Sequence[Mapping[str, Any]], limit: int
) -> list[frozenset[tuple[str, str]]]:
    """Most-recent-first canonical (name, args-json) signature sets for
    assistant turns that carried tool calls."""
    out: list[frozenset[tuple[str, str]]] = []
    for msg in reversed(messages):
        if not isinstance(msg, Mapping) or msg.get("role") != "assistant":
            continue
        calls = msg.get("tool_calls")
        if not calls:
            continue
        sig: set[tuple[str, str]] = set()
        for call in calls:
            fn = call.get("function") if isinstance(call, Mapping) else None
            if not isinstance(fn, Mapping):
                continue
            name = fn.get("name", "")
            args = fn.get("arguments", "")
            try:
                import json as _json

                parsed = _json.loads(args) if isinstance(args, str) else args
                canonical = canonical_tool_args(parsed if isinstance(parsed, Mapping) else {})
            except Exception:
                canonical = str(args)
            sig.add((name, canonical))
        if sig:
            out.append(frozenset(sig))
        if len(out) >= limit:
            break
    return out


def detect_text_repetition(
    messages: Sequence[Mapping[str, Any]],
    new_text: str,
    config: Optional[RepetitionGuardConfig] = None,
) -> RepetitionDecision:
    """Compare ``new_text`` (the assistant text about to be emitted this
    turn) against the immediately preceding assistant text turn(s) already
    in ``messages``. Returns occurrence count = 1 + the length of the
    consecutive-duplicate run immediately preceding it."""
    cfg = config or RepetitionGuardConfig()
    if not cfg.enabled or not (new_text or "").strip():
        return RepetitionDecision(kind="text")

    prior = _prior_assistant_texts(messages, limit=cfg.halt_after)
    occurrences = 1
    for prior_text in prior:
        if _is_near_duplicate(new_text, prior_text, cfg.near_duplicate_threshold):
            occurrences += 1
        else:
            break

    return _decision_for(occurrences, cfg, kind="text")


def detect_tool_call_repetition(
    messages: Sequence[Mapping[str, Any]],
    new_tool_calls: Sequence[Mapping[str, Any]],
    config: Optional[RepetitionGuardConfig] = None,
) -> RepetitionDecision:
    """Same streak logic as ``detect_text_repetition`` but for the exact
    (name, canonical-args) set of tool calls in the candidate turn -- the
    "identical repeated tool calls also count" mirror of the kernel's
    no-progress guard."""
    cfg = config or RepetitionGuardConfig()
    if not cfg.enabled or not new_tool_calls:
        return RepetitionDecision(kind="tool_call")

    new_sig: set[tuple[str, str]] = set()
    for call in new_tool_calls:
        fn = call.get("function") if isinstance(call, Mapping) else None
        if not isinstance(fn, Mapping):
            continue
        name = fn.get("name", "")
        args = fn.get("arguments", "")
        try:
            import json as _json

            parsed = _json.loads(args) if isinstance(args, str) else args
            canonical = canonical_tool_args(parsed if isinstance(parsed, Mapping) else {})
        except Exception:
            canonical = str(args)
        new_sig.add((name, canonical))
    new_sig_frozen = frozenset(new_sig)
    if not new_sig_frozen:
        return RepetitionDecision(kind="tool_call")

    prior = _prior_tool_call_signatures(messages, limit=cfg.halt_after)
    occurrences = 1
    for prior_sig in prior:
        if prior_sig == new_sig_frozen:
            occurrences += 1
        else:
            break

    return _decision_for(occurrences, cfg, kind="tool_call")


def _decision_for(occurrences: int, cfg: RepetitionGuardConfig, *, kind: str) -> RepetitionDecision:
    if occurrences >= cfg.halt_after:
        return RepetitionDecision(
            action="halt",
            occurrences=occurrences,
            kind=kind,
            message=(
                f"Stopped this turn: the same {('reply' if kind == 'text' else 'tool call')} "
                f"repeated {occurrences} times in a row. Advance the task or report the "
                "blocker instead of repeating."
            ),
        )
    if occurrences >= cfg.warn_after:
        return RepetitionDecision(
            action="warn",
            occurrences=occurrences,
            kind=kind,
            message=REPETITION_WARNING_LINE,
        )
    return RepetitionDecision(occurrences=occurrences, kind=kind)


__all__ = [
    "RepetitionGuardConfig",
    "RepetitionDecision",
    "REPETITION_WARNING_LINE",
    "detect_text_repetition",
    "detect_tool_call_repetition",
]
