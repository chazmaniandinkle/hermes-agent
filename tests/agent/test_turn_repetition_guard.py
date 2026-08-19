"""Tests for the assistant repetition guard (Ornith derail fix F1).

The guard is pure/stateless: every decision is derived from the tail of
the ``messages`` transcript, not an in-memory counter, because gateway
sessions rebuild the ``AIAgent`` fresh every turn (see module docstring in
agent/turn_repetition_guard.py for why an instance-level counter would silently
reset and miss the exact cross-turn repetition this guard exists to catch).
"""

from agent.turn_repetition_guard import (
    RepetitionGuardConfig,
    detect_text_repetition,
    detect_tool_call_repetition,
)


def _assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _assistant_tool_call(name: str, args: dict) -> dict:
    import json

    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
        ],
    }


# =========================================================================
# Text repetition
# =========================================================================


class TestDetectTextRepetition:
    def test_first_occurrence_is_allowed(self):
        messages = [{"role": "user", "content": "hi"}]
        decision = detect_text_repetition(messages, "Good. Let me do a quick diagnostic sweep.")
        assert decision.action == "allow"
        assert decision.occurrences == 1

    def test_first_duplicate_warns(self):
        messages = [
            {"role": "user", "content": "hi"},
            _assistant_text("Good. Let me do a quick diagnostic sweep."),
        ]
        decision = detect_text_repetition(messages, "Good. Let me do a quick diagnostic sweep.")
        assert decision.action == "warn"
        assert decision.occurrences == 2
        assert "identical" in decision.message

    def test_second_consecutive_duplicate_halts(self):
        """The exact case-study shape: emitted 3x verbatim."""
        messages = [
            {"role": "user", "content": "hi"},
            _assistant_text("Good. Let me do a quick diagnostic sweep."),
            {"role": "tool", "content": "some result"},
            _assistant_text("Good. Let me do a quick diagnostic sweep."),
        ]
        decision = detect_text_repetition(messages, "Good. Let me do a quick diagnostic sweep.")
        assert decision.action == "halt"
        assert decision.occurrences == 3

    def test_near_duplicate_with_trailing_punctuation_counts(self):
        messages = [_assistant_text("Good. Let me do a quick diagnostic sweep")]
        decision = detect_text_repetition(messages, "Good. Let me do a quick diagnostic sweep.")
        assert decision.action == "warn"

    def test_near_duplicate_with_whitespace_reflow_counts(self):
        messages = [_assistant_text("Good.  Let me   do a quick\ndiagnostic sweep.")]
        decision = detect_text_repetition(messages, "Good. Let me do a quick diagnostic sweep.")
        assert decision.action == "warn"

    def test_distinct_text_does_not_repeat(self):
        messages = [_assistant_text("Let me check the logs first.")]
        decision = detect_text_repetition(messages, "Now let's look at the config file.")
        assert decision.action == "allow"
        assert decision.occurrences == 1

    def test_non_consecutive_duplicate_does_not_carry_streak(self):
        """A distinct reply BREAKS the streak -- only CONSECUTIVE
        duplicates count, matching the kernel's no-progress semantics."""
        messages = [
            _assistant_text("Good. Let me do a quick diagnostic sweep."),
            _assistant_text("Actually, let's check the config instead."),
        ]
        decision = detect_text_repetition(messages, "Good. Let me do a quick diagnostic sweep.")
        assert decision.action == "allow"
        assert decision.occurrences == 1

    def test_empty_text_never_flags(self):
        messages = [_assistant_text("Good. Let me do a quick diagnostic sweep.")]
        decision = detect_text_repetition(messages, "")
        assert decision.action == "allow"

    def test_disabled_config_always_allows(self):
        cfg = RepetitionGuardConfig(enabled=False)
        messages = [
            _assistant_text("dup"),
            _assistant_text("dup"),
        ]
        decision = detect_text_repetition(messages, "dup", cfg)
        assert decision.action == "allow"

    def test_tool_only_turns_are_skipped_when_scanning_for_prior_text(self):
        """A tool-call-only turn (no narration) shouldn't count as a text
        duplicate just because content is empty on both sides."""
        messages = [
            _assistant_tool_call("read_file", {"path": "/a"}),
            _assistant_text("Good. Let me do a quick diagnostic sweep."),
        ]
        decision = detect_text_repetition(messages, "Good. Let me do a quick diagnostic sweep.")
        assert decision.action == "warn"
        assert decision.occurrences == 2


# =========================================================================
# Tool-call repetition -- "identical repeated tool calls also count"
# =========================================================================


class TestDetectToolCallRepetition:
    def test_first_occurrence_is_allowed(self):
        decision = detect_tool_call_repetition(
            [], [{"function": {"name": "emit_event", "arguments": "{}"}}]
        )
        assert decision.action == "allow"

    def test_identical_repeated_tool_call_halts_at_third(self):
        """Mirrors the kernel's beat-001 example: identical tool calls,
        limit 3."""
        messages = [
            _assistant_tool_call("emit_event", {"topic": "x"}),
            {"role": "tool", "content": "ok"},
            _assistant_tool_call("emit_event", {"topic": "x"}),
            {"role": "tool", "content": "ok"},
        ]
        decision = detect_tool_call_repetition(
            messages, [{"function": {"name": "emit_event", "arguments": '{"topic": "x"}'}}]
        )
        assert decision.action == "halt"
        assert decision.occurrences == 3

    def test_first_duplicate_tool_call_warns(self):
        messages = [_assistant_tool_call("emit_event", {"topic": "x"})]
        decision = detect_tool_call_repetition(
            messages, [{"function": {"name": "emit_event", "arguments": '{"topic": "x"}'}}]
        )
        assert decision.action == "warn"

    def test_different_args_does_not_repeat(self):
        messages = [_assistant_tool_call("emit_event", {"topic": "x"})]
        decision = detect_tool_call_repetition(
            messages, [{"function": {"name": "emit_event", "arguments": '{"topic": "y"}'}}]
        )
        assert decision.action == "allow"

    def test_args_key_order_does_not_matter(self):
        import json as _json

        messages = [_assistant_tool_call("search", {"a": 1, "b": 2})]
        decision = detect_tool_call_repetition(
            messages,
            [{"function": {"name": "search", "arguments": _json.dumps({"b": 2, "a": 1})}}],
        )
        assert decision.action == "warn"

    def test_empty_tool_calls_never_flags(self):
        decision = detect_tool_call_repetition([_assistant_tool_call("x", {})], [])
        assert decision.action == "allow"


class TestRepetitionGuardConfig:
    def test_from_mapping_defaults(self):
        cfg = RepetitionGuardConfig.from_mapping(None)
        assert cfg.enabled is True
        assert cfg.warn_after == 2
        assert cfg.halt_after == 3

    def test_from_mapping_overrides(self):
        cfg = RepetitionGuardConfig.from_mapping(
            {"enabled": False, "warn_after": 1, "halt_after": 2, "near_duplicate_threshold": 0.5}
        )
        assert cfg.enabled is False
        assert cfg.warn_after == 1
        assert cfg.halt_after == 2
        assert cfg.near_duplicate_threshold == 0.5

    def test_from_mapping_ignores_invalid_values(self):
        cfg = RepetitionGuardConfig.from_mapping(
            {"warn_after": "not-a-number", "near_duplicate_threshold": 5.0}
        )
        assert cfg.warn_after == 2  # falls back to default
        assert cfg.near_duplicate_threshold == 0.92  # out of (0, 1] range -> default
