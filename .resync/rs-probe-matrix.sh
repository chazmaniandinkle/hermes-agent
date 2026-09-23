#!/bin/bash
# usage: rs-probe-matrix.sh <tree>...   — runs the three Ornith probes, UNPIPED exit codes
cd /Users/slowbro/workspaces/cog/.cog/bin/tools/conformance || exit 2
P=/Users/slowbro/.hermes/hermes-agent/venv/bin/python
for T in "$@"; do
  for p in frame-reanchor tool-inventory-pinning turn-repetition-guard; do
    $P "$p.py" "$T" > /tmp/rs-probe.out 2>&1
    rc=$?
    v=$(grep VERDICT /tmp/rs-probe.out)
    n=$(grep -c '  pass' /tmp/rs-probe.out)
    echo "$T | $p | exit=$rc | $v | ${n} pass"
    grep -E '  FAIL' /tmp/rs-probe.out
    if [ "$p" = tool-inventory-pinning ]; then grep -E 'explicit agent|agent_init reads' /tmp/rs-probe.out | sed 's/^/    CASE5: /'; fi
  done
done
