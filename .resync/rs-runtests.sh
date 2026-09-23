#!/bin/bash
# usage: rs-runtests.sh <tree> <outprefix>
T="$1"; O="$2"
cd "$T" || exit 2
PYTHONPATH="$T" HERMES_TEST_NO_AUDIO=1 /Users/slowbro/.hermes/hermes-agent/venv/bin/python -m pytest \
  tests/agent tests/run_agent tests/cron tests/gateway tests/hermes_cli tests/test_hermes_state.py tests/tools/test_memory_tool.py \
  -q -p no:cacheprovider -o addopts="" -rfE > "$O.log" 2>&1
echo "exit=$?" > "$O.exit"
grep -E '^(FAILED|ERROR) ' "$O.log" | sed -E 's/^(FAILED|ERROR) //; s/ - .*//' | sort -u > "$O.fail"
tail -1 "$O.log" >> "$O.exit"
