"""Wiring tests for the Ornith derail series (ornith-derail-tier1-fixes, ornith-model-class-gate)
after its 2026-09-23 re-port onto upstream's split turn modules.

The conformance probes check each mechanism in isolation; these tests go through
``AIAgent.run_conversation`` so that a lost hook point shows up here. That covers the F1
guard in turn_tool_round, the F3 pin in turn_request_assembly and the F4 re-anchor in
tool_executor.
"""

from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent
from agent.turn_repetition_guard import REPETITION_WARNING_LINE
from tests.agent.test_run_agent import _make_tool_defs, _mock_response, _mock_tool_call


@pytest.fixture()
def agent():
    with (
        patch("model_tools.get_tool_definitions", return_value=_make_tool_defs("web_search", "read_file")),
        patch("model_tools.check_toolset_requirements", return_value={}),
        patch("agent.process_bootstrap.OpenAI"),
    ):
        a = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        a.client = MagicMock()
        a._cached_system_prompt = "You are helpful."
        a._use_prompt_caching = False
        a.compression_enabled = False
        a.save_trajectories = False
        return a


def _run(agent, responses, tool_result="search result", msg="hello"):
    agent.client.chat.completions.create.side_effect = responses
    with (
        patch("model_tools.handle_function_call", return_value=tool_result),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        return agent.run_conversation(msg)


def _sent(agent, i):
    return agent.client.chat.completions.create.call_args_list[i].kwargs["messages"]


# ── F3 tool-inventory pin (turn_request_assembly) ─────────────────────────────


def test_pin_appended_to_last_wire_message_when_enabled(agent):
    agent._tool_inventory_pinning_enabled = True
    _run(agent, [_mock_response(content="done", finish_reason="stop")])
    last = _sent(agent, 0)[-1]
    assert last["content"].endswith("[Available tools: web_search, read_file]")


def test_pin_absent_when_disabled(agent):
    agent._tool_inventory_pinning_enabled = False
    _run(agent, [_mock_response(content="done", finish_reason="stop")])
    assert all("[Available tools:" not in str(m.get("content")) for m in _sent(agent, 0))


def test_pin_fails_closed_when_attribute_absent(agent):
    del agent._tool_inventory_pinning_enabled
    _run(agent, [_mock_response(content="done", finish_reason="stop")])
    assert all("[Available tools:" not in str(m.get("content")) for m in _sent(agent, 0))


def test_openrouter_claude_defaults_pin_off(agent):
    """The model-class gate, end to end through init: a hosted frontier model gets no pin."""
    agent.model = "anthropic/claude-opus-5"
    from agent.agent_init import _apply_ornith_mitigations
    _apply_ornith_mitigations(agent, {"agent": {"tool_inventory_pinning": {"enabled": None},
                                                "frame_reanchor": {"enabled": None}}})
    assert agent._tool_inventory_pinning_enabled is False
    assert agent._frame_reanchor_enabled is False


# ── F4 frame re-anchor (tool_executor._commit_tool_result) ────────────────────


def _tool_then_stop():
    return [
        _mock_response(content="", finish_reason="tool_calls",
                       tool_calls=[_mock_tool_call(name="web_search", arguments="{}", call_id="c1")]),
        _mock_response(content="answer", finish_reason="stop"),
    ]


def test_reanchor_line_on_large_tool_result_when_enabled(agent):
    agent._tool_inventory_pinning_enabled = False
    agent._frame_reanchor_enabled = True
    _run(agent, _tool_then_stop(), tool_result="x" * 9000, msg="WHAT-IS-THE-QUESTION")
    tool_msgs = [m for m in _sent(agent, 1) if m.get("role") == "tool"]
    assert tool_msgs and 'The live question: "WHAT-IS-THE-QUESTION"' in str(tool_msgs[-1]["content"])


def test_no_reanchor_when_disabled(agent):
    agent._tool_inventory_pinning_enabled = False
    agent._frame_reanchor_enabled = False
    _run(agent, _tool_then_stop(), tool_result="x" * 9000, msg="WHAT-IS-THE-QUESTION")
    assert all("The live question" not in str(m.get("content")) for m in _sent(agent, 1))


# ── F1 repetition guard (turn_tool_round) ─────────────────────────────────────


def _narrated_tool_turn(i):
    return _mock_response(
        content="Good. Let me do a quick diagnostic sweep of the logs now.",
        finish_reason="tool_calls",
        tool_calls=[_mock_tool_call(name="web_search", arguments=f'{{"q": "{i}"}}', call_id=f"c{i}")],
    )


def test_repeated_narration_warns_then_halts(agent):
    agent._tool_inventory_pinning_enabled = False
    result = _run(agent, [_narrated_tool_turn(1), _narrated_tool_turn(2), _narrated_tool_turn(3),
                          _mock_response(content="unreached", finish_reason="stop")])
    # 2nd identical narration -> corrective nudge sent to the 3rd call.
    assert any(m.get("role") == "user" and m.get("content") == REPETITION_WARNING_LINE for m in _sent(agent, 2))
    # 3rd identical narration -> turn halted before execution; 4th call never made.
    assert agent.client.chat.completions.create.call_count == 3
    assert result["completed"] is False
    assert result["error"].startswith("repetition_guard_halt")
    # The marker never reaches the wire.
    assert all("_repetition_guard_synthetic" not in m for m in _sent(agent, 2))
