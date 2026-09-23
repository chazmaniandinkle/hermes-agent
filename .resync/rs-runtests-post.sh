#!/bin/bash
# usage: rs-runtests-post.sh <tree> <outprefix>
# Same suite as rs-runtests.sh, with v2026.9.21's moved paths:
#   tests/run_agent/*          -> tests/agent/*        (upstream merged the dirs)
#   tests/test_hermes_state.py -> tests/hermes_state/test_hermes_state.py
T="$1"; O="$2"
cd "$T" || exit 2
PYTHONPATH="$T" HERMES_TEST_NO_AUDIO=1 /Users/slowbro/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/agent tests/cron tests/gateway tests/hermes_cli tests/hermes_state/test_hermes_state.py tests/tools/test_memory_tool.py \
  -q -p no:cacheprovider -o addopts="" -rfE > "$O.log" 2>&1
echo "exit=$?" > "$O.exit"
grep -E '^(FAILED|ERROR) ' "$O.log" | sed -E 's/^(FAILED|ERROR) //; s/ - .*//' | sort -u > "$O.fail"
tail -1 "$O.log" >> "$O.exit"
