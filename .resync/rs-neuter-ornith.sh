#!/bin/bash
# Neuter controls for tests/agent/test_ornith_wiring.py — each mutation must turn >=1 test red.
cd /Users/slowbro/.hermes/hermes-agent-resync || exit 2
PY=/Users/slowbro/.hermes/hermes-agent/venv/bin/python
run() { PYTHONPATH=$PWD HERMES_TEST_NO_AUDIO=1 $PY -m pytest tests/agent/test_ornith_wiring.py -q -p no:cacheprovider -o addopts="" > /tmp/neuter.log 2>&1; echo "  exit=$? $(grep -E '[0-9]+ (passed|failed)' /tmp/neuter.log | tail -1)"; grep '^FAILED' /tmp/neuter.log | sed 's/ - .*//; s/^/    /'; }
mutate() { # file, python-literal old, new
  cp "$1" /tmp/neuter.bak
  $PY - "$1" "$2" "$3" <<'EOF'
import sys; p,o,n=sys.argv[1:]; s=open(p).read(); assert s.count(o)==1,(o,s.count(o)); open(p,"w").write(s.replace(o,n))
EOF
}
restore() { cp /tmp/neuter.bak "$1"; }

echo "N1 F1: halt check disabled in turn_tool_round"
mutate agent/turn_tool_round.py '    if _rep_decision.should_halt:' '    if False and _rep_decision.should_halt:'; run; restore agent/turn_tool_round.py
echo "N2 F3: pin fallback flipped back to fail-open"
mutate agent/turn_request_assembly.py 'getattr(agent, "_tool_inventory_pinning_enabled", False)' 'getattr(agent, "_tool_inventory_pinning_enabled", True)'; run; restore agent/turn_request_assembly.py
echo "N3 F4: re-anchor kwargs dropped from _commit_tool_result"
mutate agent/tool_executor.py '        **_tool_result_message_kwargs(agent, messages),
    )' '    )'; run; restore agent/tool_executor.py
echo "RESTORED"
run
git status --short agent/
