The resync is only partly done: branch `resync-v2026.9.21` exists and the rebase ran to completion, but the Ornith F1–F5 series and today's fix 1a2e14c806 are not in it yet. I ran out of tool iterations before re-porting them, so the branch is not ready to go live. I also did not push it, write RESYNC-NOTES.md, or run the post-rebase tests. No gateway or serve process was touched, and `runtime/current` was not repointed.

**Which half I did**
- **Mirror refresh: done.** `git fetch origin main:main` fast-forwarded `main` to 5c6b56a866; `origin/main...main` reads `0 0`. The live checkout's working tree was untouched.
- **Rebase: done but incomplete.** It was done in the new worktree `/Users/slowbro/.hermes/hermes-agent-resync`, not the checkout. It runs `1a2e14c806 → v2026.9.21` (d337b736aa). Branch HEAD is e4fe3f5fbf. Details in (c).

**(a) Divergence (HEAD against the v2026.9.21 tag)**
- HEAD is behind 16,284 and ahead 43.
- The merge-base is e624e9fde5, dated 2026-08-18.
- The `merge-tree` dry run gave 22 conflicting files.

| file | OURS+ | UP-COMMITS | HUNKS | OURS+×HUNKS |
|---|---|---|---|---|
| agent/conversation_loop.py | 332 | 131 | 21 | 6972 |
| gateway/run.py | 273 | 348 | 14 | 3822 |
| tests/agent/test_anthropic_adapter.py | 141 | 19 | 18 | 2538 |
| cron/scheduler.py | 219 | 165 | 10 | 2190 |
| agent/anthropic_adapter.py | 141 | 45 | 12 | 1692 |
| agent/tool_dispatch_helpers.py | 143 | 8 | 11 | 1573 |
| agent/background_review.py | 252 | 49 | 3 | 756 |
| tools/memory_tool.py | 175 | 19 | 4 | 700 |
| agent/agent_init.py | 156 | 129 | 3 | 468 |
| run_agent.py | 73 | 134 | 6 | 438 |

The remaining 12 files all score under 200.

Most of the real cost came from upstream splitting large modules into new ones:
- `MemoryStore` moved to `tools/memory_tool_store.py`.
- `gateway/run.py` was split into mixins such as `run_adapters`, `run_shutdown`, `run_startup` and `run_turn`.
- The Claude Code credential functions moved to `agent/anthropic_credentials.py`.
- The pre-API guard chain moved to `turn_preflight_gate.py`.
- `run_conversation` moved to `turn_facade.py`.

**(b) Per-patch disposition**
- **DROP, with upstream code read:**
  - `oauth-sanitizer-anchoring`: the probe is CONFORMANT on upstream, and upstream's `_OAUTH_SLUG_PATTERN` covers more than PR #48868's regex.
  - `tts-concurrent-output-collision`: upstream now uses `%Y%m%d_%H%M%S_%f`.
  - `launchd-restart-detection`: already absorbed at an earlier resync.
- **DROP pending Chaz's confirmation:**
  - `sessiondb-write-none-guard`: upstream now reopens a connection nulled by `close()` (the #54706 approach), and I took upstream's version. The probe reads NON-CONFORMANT on upstream, but that comes from an ImportError where the probe reached into the live checkout's `hermes_bootstrap`. That is a probe provenance bug, not a real defect verdict.
  - `cron-teardown-orphan-race`: I took upstream's version. Upstream now defers teardown to the running worker via `add_done_callback`. It lacks our `HERMES_CRON_TEARDOWN_HARDCAP` timer and the early `kill_all`, so this is a behavioural decision for Chaz: accept upstream, or rework ours on top.
- **REWORKED onto upstream's new structure, each verified by tests plus a neuter control that turns them red:**
  - `progressive-memory-eviction` (16/16 plus 78 memory tests pass).
  - `restart-ack-teardown-race`: re-ported onto the new shutdown mixin. I added 2 decision-layer tests, because the old tests only covered the adapter.
  - `oauth-credential-read-only`: pool and adapter halves. The adapter half now lives in `anthropic_credentials.py`.
  - `oauth-gate2-identity-relocation`: layered on top of upstream's `_apply_claude_code_identity`. Both preflight hard-gate markers are present.
  - `idle-triggered-background-review`: kept alongside upstream's new `defer: auto` idle queue, which only covers the managed llama-server (13/13 pass).
  - `fix-gateway-run-import-lock-plugin-discovery` (PR #1): upstream still runs plugin discovery at import time. The port bridges built-in keys at module scope and plugin keys in `_start_impl`. A decision-layer check passes on the resync tree and fails on upstream.
  - `over-limit-dispatch-refusal`: wired into `turn_preflight_gate`, with new wiring tests.
  - `compaction-abort-escalation`: wired into `run_turn._hmwa_prepare_turn`.
- **CARRIED as-is:** `steer-slash-alias`, `myrgic-overlay-header`, the audio-guard test commit, `mod3-streaming-provider` and the PATCHES.md history.
- **NOT YET PORTED (blocking):**
  - The whole `ornith-derail-tier1-fixes` series: F1 (skipped), F2–F5.
  - `ornith-model-class-gate` (2 commits).
  - The `turn-repetition-guard-rename`.
  - Today's fix **1a2e14c806**.
  - Upstream has none of these; the sweep reports NOT-APPLICABLE, which means CARRY. The target shape is the net diff `git diff v2026.8.18 1a2e14c806` over the Ornith-touched files (~1,613 lines added across 11 files).
  - The `agent_init.py` part can mostly be lifted verbatim, including today's `_mitigation_section` read.
  - `conversation_loop`, `tool_dispatch_helpers`, `tool_executor` and `turn_context` must be re-anchored onto upstream's split modules.
  - `test_run_agent` now lives under `tests/agent/`.

**PR #1 decision:** I replayed `local` and the PR #1 branch as one linear series rather than merging PR #1 into `local` first. PR #1 is a strict descendant of `local` (7 commits plus today's fix). A single rebase keeps one ordered patch set and never rewrites `local` or `fork/local`.

**(c) Branch and worktree state**
- Branch `resync-v2026.9.21` is at e4fe3f5fbf, worktree `/Users/slowbro/.hermes/hermes-agent-resync`. It is **not pushed** to `fork`.
- To finish, re-port the Ornith series and 1a2e14c806 on top of this branch.
- **Worktree quirk:** upstream ships two contributor-email files differing only in case, which cannot coexist on APFS. I used a per-worktree sparse-checkout to exclude `contributors/emails/`.
- **Safety and setup:**
  - Backup tags `pre-resync-2026-09-23-local` and `pre-resync-2026-09-23-fix-gateway-run-import-lock-plugin-discovery` exist.
  - rerere is enabled, with autoupdate on.
  - The original rebase todo is saved at `/tmp/rs-todo.orig`.
- **Throwaway worktrees still present:** `/tmp/up-v2026.9.21` and `/tmp/rs-pre` (at 1a2e14c806). Remove them with `git worktree remove --force` once no longer needed.

**(d) Test failure-set diff:** not done.
- The pre-baseline run was started in the background: `/tmp/rs-runtests.sh /tmp/rs-pre /tmp/rs-base-pre`, writing `/tmp/rs-base-pre.fail`.
- The post run has not been done. Run `bash /tmp/rs-runtests.sh /Users/slowbro/.hermes/hermes-agent-resync /tmp/rs-post`, then `comm -13` and `comm -23` on the two `.fail` files.
- One contract conflict is already known: upstream's new `test_concurrent_claude_code_refresh_recovers_via_credentials_file` asserts the POST path that `oauth-credential-read-only` exists to remove.
- The old keychain xfail was dropped in favour of upstream's test file. That test also needs re-xfailing or a rewrite.

**(e) Conformance verdict matrix (partial)**

| probe | v2026.9.21 (upstream role) | pre-resync HEAD | resync branch |
|---|---|---|---|
| oauth-sanitizer-anchoring | CONFORMANT → DROP | CONFORMANT | CONFORMANT |
| oauth-credential-read-only* | NON-CONFORMANT → CARRY | CONFORMANT | CONFORMANT |
| oauth-gate2-identity-relocation | NON-CONFORMANT → CARRY | NON-CONFORMANT (6/7) | NON-CONFORMANT (6/7) |
| sessiondb-write-none-guard | NON-CONFORMANT† | CONFORMANT | not run |
| frame-reanchor / tool-inventory-pinning / turn-repetition-guard | NOT-APPLICABLE → CARRY | CONFORMANT | not ported yet |
| evicted-context-rebuildable / relation-vocabulary-conformance | NOT-APPLICABLE | NOT-APPLICABLE | not run |

- \* **I edited a Cog-workspace probe:** `/Users/slowbro/workspaces/cog/.cog/bin/tools/conformance/oauth-credential-read-only.py`. It now binds to `agent/anthropic_credentials.py` when present, isolates `HERMES_HOME`, and stubs upstream's new dispatcher methods. The original is backed up at `/tmp/oauth-credential-read-only.py.orig`. The edited probe discriminates correctly: FAIL on upstream (4 cases), PASS on the old and new trees, and the real credential file is unchanged.
- † The upstream sessiondb verdict comes from the probe's import leaking into the live checkout, so it is a probe bug and the verdict is void.
- **Gate 2 at 6/7:** the one failing case is a fixture-integrity "could not inspect" (upstream now strips the orphan `tool_result`). It fails identically on the pre-resync tree, so it is a probe fixture problem, not a regression.

**(f) Go-live procedure — NOT executed**
- **Source:** PATCHES.md "Runtime pinning". It is marked "reconstructed, UNTESTED" and no builder script exists.
- **Which gateways restart:** all four gateways, `ai.hermes.gateway-{cog,darkstar,theseus,vega}`. There are four, not three: theseus (pid 2778) also runs from `runtime/current`. Restarting `cog` ends this conversation.
- **Checkout-venv processes:** the desktop `serve` processes 73352 and 84483, and the dashboard 2793, run from the checkout's venv, not the pin, so the flip does not affect them.
- **Gate:** only proceed once the Ornith series and 1a2e14c806 are ported and the test set-diff is clean.

```
cd /Users/slowbro/.hermes/hermes-agent-resync
SHA=$(git rev-parse HEAD); SHORT=$(git rev-parse --short=7 HEAD)
NEW=/Users/slowbro/.hermes/runtime/v2026.9.21-local.$SHORT
mkdir -p $NEW/src && git archive $SHA | tar -x -C $NEW/src
python3.11 -m venv $NEW/venv && $NEW/venv/bin/pip install -e $NEW/src
cp -R /Users/slowbro/.hermes/runtime/current/wheels $NEW/wheels && $NEW/venv/bin/pip install $NEW/wheels/hermes_cogos-*.whl
grep -q '_MCP_TOOL_PREFIX = "mcp__"' $NEW/src/agent/anthropic_adapter.py && grep -q _relocate_identity_into_first_user $NEW/src/agent/anthropic_adapter.py   # preflight hard gates (exit 78 otherwise)
# write $NEW/validation-report.json (same keys as current pin)
ln -sfn $NEW /Users/slowbro/.hermes/runtime/current.tmp && mv -f /Users/slowbro/.hermes/runtime/current.tmp /Users/slowbro/.hermes/runtime/current
for j in darkstar vega theseus cog; do launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway-$j; done   # cog LAST: kills this conversation
```

- **Rollback:** `ln -sfn /Users/slowbro/.hermes/runtime/v2026.8.18-local.2911b53 /Users/slowbro/.hermes/runtime/current`, then the same kickstarts.

**(g) Risks and unresolved items**
1. The Ornith series and today's fix are not yet carried. Until they are, the branch would silently drop F1–F5 and the fix Chaz wanted to survive.
2. Accepting upstream's cron teardown drops our hard-cap timer. That is a behavioural decision for Chaz.
3. The post-rebase test set-diff has not been run.
4. RESYNC-NOTES.md was not written. These notes are the resumption record for now.
5. The go-live procedure is untested.
6. The probe edit sits in the Cog workspace and is uncommitted. The sessiondb probe's import-provenance leak needs fixing before its verdict can be trusted.
7. Four gateways restart at go-live, not three (theseus was missing from the brief).

---

## Session 2 (2026-09-23, subagent): Ornith series ported

**Summary:** the Ornith series and 1a2e14c806 are now carried on `resync-v2026.9.21`. All three Ornith probes read CONFORMANT on the resync tree and NOT-APPLICABLE on upstream v2026.9.21. tool-inventory-pinning **CASE 5 passes**. No gateway, serve or dashboard process was touched. `runtime/current` was not repointed, and the live checkout was not edited.

### (h) Port status per slug
| slug | commit(s) | re-anchored onto |
|---|---|---|
| `ornith-derail-tier1-fixes` (F1-F5) | 0585ce06f5, wiring tests 01ec4757d6 | F1: `turn_tool_round.run_tool_round` + new `check_assistant_repetition()`; reset in `turn_context._PER_TURN_RESET_STATE`. F3: `turn_request_assembly.assemble_api_request` (after surrogate sanitize); builder stays `conversation_loop._build_tool_inventory_pin`. F4 + #5: `tool_dispatch_helpers.make_tool_result_message`; kwargs wired into `tool_executor._commit_tool_result` (upstream's single commit point for concurrent + sequential). F2: `turn_truncation._continue_text`; `_length_truncation_prefill` added to `turn_final_response._EPHEMERAL_SCAFFOLDING_FLAGS`; `_LENGTH_CONTINUATION_TAIL_PREFIX` recognised in `context_compressor`. agent_init: new `_apply_ornith_mitigations()` called after `_apply_display_config`. |
| `ornith-model-class-gate` (2 commits) | d46c6055c6 | `_small_model_mitigations_default` verbatim; AUTO sentinel `None` in DEFAULT_CONFIG; fallbacks fail closed. |
| 1a2e14c806 `_mitigation_section` | ef5498dc8f (code), 7a260984a3 (PATCHES.md) | verbatim |
| `turn-repetition-guard-rename` | d7816d8ec6 | The guard is created directly at `agent/turn_repetition_guard.py`, so upstream's `agent/repetition_guard.py` is untouched. It also carries `tests/plugins/test_skill_relevance_gate.py`, which rode along in eab7e6011d (6 passed). |
| `oauth-credential-read-only` test contract | c8c9213eb7 | Three tests are marked strict-xfail: `test_credential_pool_anthropic_refresh_race.py::test_concurrent_claude_code_refresh_recovers_via_credentials_file` (new upstream); `test_anthropic_keychain.py::TestRefreshOAuthTokenAdoptsFreshCredential::test_falls_back_to_network_refresh_when_no_fresh_credential` (re-xfail of de02c19051); and `::test_concurrent_refreshes_use_one_shared_credentials_lock` (new upstream, the same POST/write contract; this third one was not in the brief). |

### (i) Verification
- **Probe verdict matrix** (driver `.resync/rs-probe-matrix.sh`, exit codes UNPIPED):

| probe | upstream v2026.9.21 | pre-resync 1a2e14c806 | resync @0585ce06f5 (F1-F5, no gate) | resync @d46c6055c6 (gate, no read fix) | resync HEAD |
|---|---|---|---|---|---|
| frame-reanchor | NOT-APPLICABLE (3) | CONFORMANT | NON-CONFORMANT (fail-open fallback) | CONFORMANT | CONFORMANT (0) |
| tool-inventory-pinning | NOT-APPLICABLE (3) | CONFORMANT | NON-CONFORMANT (3 cases) | NON-CONFORMANT, **CASE 5 only** | CONFORMANT (0), **CASE 5 pass** |
| turn-repetition-guard | NOT-APPLICABLE (3) | CONFORMANT | CONFORMANT | CONFORMANT | CONFORMANT (0) |

- **Neuter controls:**
  - Disabling `_is_near_duplicate` turns the turn-repetition-guard probe red (3 cases); restoring it turns the probe green.
  - The probes do not check wiring, so `tests/agent/test_ornith_wiring.py` (7 tests, through `run_conversation`) was added. `.resync/rs-neuter-ornith.sh` makes three mutations, and each turns exactly its matching test red: removing the F1 halt, flipping the F3 fallback to True, and dropping the F4 kwargs.
- **Real config read:** `load_config_readonly()` on profile cog with a localhost:6931 endpoint resolves pin and re-anchor to False/False.
- **Slug test files:**
  - `test_turn_repetition_guard.py` + `test_tool_dispatch_helpers.py` + `tests/agent/test_run_agent.py`: 362 passed.
  - `test_ornith_wiring.py`: 7 passed.
  - `test_skill_relevance_gate.py`: 6 passed.
- **Note:** this worker's own tool results carried `[Available tools: ...]` footers throughout the session. That is the live pin (`v2026.8.18-local.2911b53`) exhibiting the exact read-path bug 1a2e14c806 fixes. It stops only at go-live.

### (j) Test failure-set diff: IN PROGRESS at the time of writing
- **Pre run: DONE.** `/tmp/rs-pre` via `/tmp/rs-runtests.sh`: 765 failed, 18,740 passed, 141 skipped, 3 xfailed, 2 errors, in 1:02:04. `/tmp/rs-base-pre.fail` holds **778 named entries**. That large baseline is itself environment noise to triage; the named set is the reference, not the count.
- **Post run: RUNNING** in the background (pid 86977, started about 11:07). It was at 18% after 21 min, so expect roughly 2h in total. Check it with `cat /tmp/rs-post.exit` once that file exists. The runner is `.resync/rs-runtests-post.sh`. It is the same suite with v2026.9.21's moved paths: `tests/run_agent` merged into `tests/agent` (170 of 183 files moved), and `tests/test_hermes_state.py` moved to `tests/hermes_state/test_hermes_state.py`.
  - The original runner exits 4 on the resync tree because `tests/run_agent` is gone. Do not use it for POST.
  - The post run writes `/tmp/rs-post.fail`.
- **Upstream reference run** (`/tmp/rs-up`): it died with a **segfault** (exit 139) in a `title_generator` → `_emit_warning` background thread. That is upstream code, not a carry. It is not needed for the diff, and it can be rerun if a post-only failure needs attribution.
- **Resume:**
  ```
  comm -13 /tmp/rs-base-pre.fail /tmp/rs-post.fail   # new failures
  comm -23 /tmp/rs-base-pre.fail /tmp/rs-post.fail   # fixed / gone
  ```
  - First normalise the moved paths: `sed 's#^tests/run_agent/#tests/agent/#; s#^tests/test_hermes_state.py#tests/hermes_state/test_hermes_state.py#'` on the pre set.
  - Many "fixed" entries will be tests upstream deleted or renamed (the wave-1 prune). Name them, don't count them.

### (k) Still open (NOT decided here; Chaz's call)
- (a) `cron-teardown-orphan-race`: upstream's `add_done_callback` deferral vs our `HERMES_CRON_TEARDOWN_HARDCAP` timer + early `kill_all`.
- (b) `sessiondb-write-none-guard` drop: its probe leaks imports into the live checkout's `hermes_bootstrap`, so its upstream verdict is void until the probe is fixed.
- The go-live procedure in (f) is still untested. The four gateways restart; cog restarts LAST.
