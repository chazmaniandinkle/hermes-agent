"""
Tests for the skill_relevance_gate plugin (v2 — Hermes-native auxiliary task).

Covers:
  1. test_aux_task_registered         — register() calls register_auxiliary_task('skill_relevance')
  2. test_relevance_gate_promotes_skills — mock call_llm returns a skill name;
       its SKILL.md body is prepended to context_prompt
  3. test_relevance_gate_fail_open_on_timeout — call_llm raises TimeoutError;
       original _run_agent is still called with context_prompt unchanged
  4. test_relevance_gate_fail_open_on_bad_json — call_llm returns 'not json';
       call-through unchanged
  5. test_relevance_gate_skips_unknown_skill_names — call_llm returns a mix of
       real and nonexistent skill names; only real skill is promoted
  6. test_relevance_gate_zero_promotions_when_no_match — call_llm returns [];
       context_prompt is not prepended to
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Path setup — add plugin dir so `import skill_relevance_gate` resolves
# ---------------------------------------------------------------------------

_PLUGIN_DIR = Path.home() / ".hermes" / "plugins" / "skill_relevance_gate"
if str(_PLUGIN_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR.parent))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_fake_response(content: str) -> MagicMock:
    """Build a fake call_llm response with .choices[0].message.content."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


def _fresh_gate():
    """Return a freshly reloaded copy of the gate module."""
    if "skill_relevance_gate" in sys.modules:
        return importlib.reload(sys.modules["skill_relevance_gate"])
    return importlib.import_module("skill_relevance_gate")


# ---------------------------------------------------------------------------
# Test 1 — auxiliary task registration
# ---------------------------------------------------------------------------


def test_aux_task_registered():
    """After register() is called, the plugin-auxiliary-task registry contains 'skill_relevance'."""
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    manager = PluginManager()
    manager._discovered = True  # skip auto-scan

    manifest = PluginManifest(name="skill_relevance_gate")
    ctx = PluginContext(manifest, manager)

    gate_mod = _fresh_gate()

    # Patch GatewayRunner import so the patch step is a no-op during registration
    fake_runner_mod = MagicMock()
    fake_runner = MagicMock()
    # Remove the patched flag so _patch_gateway_runner actually tries (but fakes out cleanly)
    fake_runner_mod.GatewayRunner = fake_runner
    with patch.dict("sys.modules", {"gateway.run": fake_runner_mod}):
        gate_mod = _fresh_gate()
        gate_mod.register(ctx)

    assert "skill_relevance" in manager._aux_tasks
    entry = manager._aux_tasks["skill_relevance"]
    assert entry["key"] == "skill_relevance"
    assert entry["display_name"] == "Skill relevance"
    assert entry["defaults"]["timeout"] == 5
    assert entry["defaults"]["provider"] == "auto"


# ---------------------------------------------------------------------------
# Test 2 — gate promotes matching skills
# ---------------------------------------------------------------------------


def test_relevance_gate_promotes_skills(monkeypatch):
    """When call_llm returns ['github-pr-workflow'], that skill's body is prepended."""
    gate_mod = _fresh_gate()

    skill_body = "---\nname: github-pr-workflow\ndescription: PR workflow skill\n---\n# Content"
    fake_catalog = [{"name": "github-pr-workflow", "description": "PR workflow skill"}]
    fake_response = _make_fake_response(json.dumps(["github-pr-workflow"]))

    monkeypatch.setattr(gate_mod, "_build_skill_catalog", lambda: fake_catalog)
    monkeypatch.setattr(
        gate_mod, "_load_skill_body",
        lambda name: skill_body if name == "github-pr-workflow" else None,
    )

    with patch("agent.auxiliary_client.call_llm", return_value=fake_response):
        result = gate_mod._promote_skills_for_message("open a PR", "original-context")

    assert skill_body in result
    assert result.startswith(skill_body)
    assert "original-context" in result


# ---------------------------------------------------------------------------
# Test 3 — fail-open on timeout
# ---------------------------------------------------------------------------


def test_relevance_gate_fail_open_on_timeout(monkeypatch):
    """When call_llm raises TimeoutError, context_prompt is returned unchanged."""
    gate_mod = _fresh_gate()

    monkeypatch.setattr(
        gate_mod, "_build_skill_catalog",
        lambda: [{"name": "x", "description": "d"}],
    )

    with patch("agent.auxiliary_client.call_llm", side_effect=TimeoutError("timeout")):
        result = gate_mod._promote_skills_for_message("hello", "original-context")

    assert result == "original-context"


# ---------------------------------------------------------------------------
# Test 4 — fail-open on bad JSON
# ---------------------------------------------------------------------------


def test_relevance_gate_fail_open_on_bad_json(monkeypatch):
    """When call_llm returns 'not json', context_prompt is returned unchanged."""
    gate_mod = _fresh_gate()

    monkeypatch.setattr(
        gate_mod, "_build_skill_catalog",
        lambda: [{"name": "x", "description": "d"}],
    )

    with patch("agent.auxiliary_client.call_llm", return_value=_make_fake_response("not json")):
        result = gate_mod._promote_skills_for_message("hello", "original-context")

    assert result == "original-context"


# ---------------------------------------------------------------------------
# Test 5 — skips unknown skill names
# ---------------------------------------------------------------------------


def test_relevance_gate_skips_unknown_skill_names(monkeypatch):
    """When model returns ['real-skill', 'nonexistent-skill'], only real-skill is promoted."""
    gate_mod = _fresh_gate()

    real_body = "---\nname: real-skill\n---\n# Real skill content"
    fake_catalog = [{"name": "real-skill", "description": "real"}]

    monkeypatch.setattr(gate_mod, "_build_skill_catalog", lambda: fake_catalog)
    monkeypatch.setattr(
        gate_mod, "_load_skill_body",
        lambda name: real_body if name == "real-skill" else None,
    )

    with patch(
        "agent.auxiliary_client.call_llm",
        return_value=_make_fake_response(json.dumps(["real-skill", "nonexistent-skill"])),
    ):
        result = gate_mod._promote_skills_for_message("hello", "ctx")

    assert real_body in result
    # nonexistent-skill body is absent (silently skipped)
    assert "nonexistent" not in result


# ---------------------------------------------------------------------------
# Test 6 — zero promotions when no match
# ---------------------------------------------------------------------------


def test_relevance_gate_zero_promotions_when_no_match(monkeypatch):
    """When call_llm returns [], context_prompt is returned unchanged."""
    gate_mod = _fresh_gate()

    monkeypatch.setattr(
        gate_mod, "_build_skill_catalog",
        lambda: [{"name": "x", "description": "d"}],
    )

    with patch("agent.auxiliary_client.call_llm", return_value=_make_fake_response("[]")):
        result = gate_mod._promote_skills_for_message("hello", "original-context")

    assert result == "original-context"
