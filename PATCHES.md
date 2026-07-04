# Myrgic Local Patch Registry

**Canonical, in-repo registry of every local modification to this vendored checkout of `NousResearch/hermes-agent`.**
Lives on the `local` branch. Read before editing any file under "Files to Watch."

> This file is the single source of truth for *what's patched*. The external journals
> `~/.hermes/journals/hermes-upstream-activity.md` and `hermes-upstream-drift.md` are
> append-only time-series logs maintained by the `hermes-upstream-reconciler` cron.

## How this repo is maintained (the pattern)

- **`main`** = pristine mirror of `origin/main`. **Never commit here.** Only `git fetch && git reset --hard origin/main` (or fast-forward).
- **`local`** = `origin/main` + the patches below + this file. **The gateway runs from `local`.**
- **Sync = rebase, not reset:** `git fetch origin && git rebase origin/main local`. A patch whose upstream PR merged drops out automatically (commit goes empty); conflicts surface exactly on patches that need updating.
- **Adding a change?** Prefer in order: **(1) upstream via PR → (2) user-space plugin/hook in `~/.hermes/` → (3) a tracked patch here** (last resort). Keep this repo as close to pristine upstream as possible.

---

## ⚙️ Gateway processes on this node — manage ALL of them

This node runs **one gateway process per agent profile**, each a separate launchd job with its own bot tokens/config, but **all sharing this single checkout** (one working tree, no worktrees → **the branch is node-global**):

| launchd job | profile | bots | config | logs |
|-------------|---------|------|--------|------|
| `ai.hermes.gateway` | default | Telegram **Hermes** (`cogos_hermes_bot`) + Discord **Whirl** | `~/.hermes/.env` | `~/.hermes/logs/` |
| `ai.hermes.gateway-cog` | `--profile cog` | Telegram **Cog** (`cogos_cog_bot`) + Discord **Hermes_bot** | `~/.hermes/profiles/cog/.env` | `~/.hermes/profiles/cog/logs/` |

- **One gateway serves ALL its channels** (Telegram + Discord + …) as adapters in a single process — *not* one gateway per channel. "Gateway running with 2 platform(s)" = both adapters in one process.
- **Code is shared and node-global.** Both jobs have `WorkingDirectory=/Users/slowbro/.hermes/hermes-agent` and run whatever single branch is checked out (`local`). A `git checkout main` / `git reset` flips **both** gateways to unpatched code on their next restart. **The "stay on `local`" discipline is node-wide, not per-profile.**
- **A code change is only live after each process restarts.** Python loads code at process start; flipping the on-disk checkout does NOT affect an already-running gateway. **After any branch change/rebase, restart EVERY gateway** (`launchctl kickstart -k gui/$(id -u)/<job>`) or you'll have one profile patched and another silently running stale code (this exact split happened 2026-06-03: default was restarted onto `local`, cog kept running boot-time `main` code until separately restarted).
- **Enumerate before acting:** `launchctl list | grep hermes` (note PID vs `-`), `ps -o pid,lstart,command -p <pids>` (start time tells you which branch's code a process loaded), and match the bot identity (`getMe`) to the bot in question. Never assume the gateway you found is the only one.

---

## At-Risk Patches (vendored-repo modifications — clobbered by `git reset`, replayed by rebase)

### PATCH-001: Progressive memory eviction (`tools/memory_tool.py`)
- **Status:** LIVE on `local` (landed 2026-06-10, `feat(memory): silent Tier 1→Tier 3 eviction…`; closes incident MEMDUMP-001 / task t_4f9f0fc7 — operator decision 2026-06-10). Reimplemented against the eviction test suite; the original draft in `stash@{0}` is now redundant — verify and drop.
- **Files:** `tools/memory_tool.py` (+175), `tests/tools/test_memory_tool.py` (legacy limit test repointed at the preserved hard-error path), `tests/tools/test_memory_tool_eviction.py` (now tracked).
- **What:** Silent Tier 1→Tier 3 eviction at >75% capacity; evicted entries → CogOS cogdocs (substrate overflow dir, fallback `~/.hermes/memories/hermes-overflow`); Tier 2 pointer index; `scan_tier2_index_for_hot_entries()` read-side helper. Entry larger than the whole limit stays a hard error.
- **Risk:** HIGH — core file, likely upstream churn.
- **Path to safety:** Upstream PR, or move to a plugin hook.

### PATCH-002: Untracked test files
- **Status:** PARTIALLY RESOLVED — `test_memory_tool_eviction.py` committed with PATCH-001 (2026-06-10). Still untracked: `tests/plugins/test_skill_relevance_gate.py`, `skills/devops/kanban-closed-loop-supervisor/`.
- **Files:** `tests/plugins/test_skill_relevance_gate.py`
- **Risk:** LOW. **Path to safety:** commit on `local`, or move to `~/.hermes/plugins/skill_relevance_gate/tests/`.

### PATCH-003: launchd `/restart` detection (now `gateway/slash_commands.py`)
- **Status:** DROPPED at the 2026-07-04 resync — **genuinely superseded this time.** Upstream `abc3662bf` ("fix(gateway): detect launchd in /restart service-manager probe (#43475)") lands the identical `XPC_SERVICE_NAME not in ("", "0")` guard directly in `gateway/slash_commands.py`, same exclusion-of-literal-`"0"` reasoning, explicitly fixing the same upstream issue PR #43888 targeted. The commit's own diff on `slash_commands.py` cherry-picked to empty; only the accompanying pin test (`tests/gateway/test_restart_service_detection.py`) also came up empty — upstream ships the identical 4 cases (launchd label / interactive-shell `XPC_SERVICE_NAME=0` / no-service-env / systemd `INVOCATION_ID`) verbatim. Verify `/restart` after cutover as a final sanity check, but this is not expected to need re-application.
- **History (do not re-flag as superseded without checking this row):** was previously marked superseded 2026-06-10 and that call was **wrong** — upstream's `darwin` check at the time only lived at the exit-code site (`run.py`), not the `/restart` handler; re-applied same night after a live 25-min gateway outage. This time the check was traced against the handler itself (`_under_service` in `slash_commands.py`) and confirmed identical, not just "upstream mentions launchd somewhere."
- **Upstream:** PR #43888 (filed 2026-06-10) — superseded by `abc3662bf` landing; close/reference on the next PR sweep.
- **Reconciler action:** none — patch fully absorbed. Watch `gateway/slash_commands.py`'s `_handle_restart_command` only if upstream later regresses the launchd check (re-derive from this entry's "Is:" clause if so: `_under_service = bool(INVOCATION_ID) or XPC_SERVICE_NAME not in ("", "0")`).

### PATCH-004: Myrgic overlay header in `AGENTS.md`
- **Status:** ON `local` (this commit).
- **Files:** `AGENTS.md` (prepended block, delimited by `MYRGIC-LOCAL-OVERLAY:START/END`)
- **What:** Prepends a "READ FIRST" overlay declaring this a vendored checkout (not Myrgic source), pointing agents at this file + the branch model, ahead of upstream's contributor guide. Solves the "agent treats the fork as its own source" failure mode.
- **Upstream:** Never upstreamed (Myrgic-specific). Permanent local patch.
- **Risk:** LOW — additive, prepended above upstream content; rebase conflicts only if upstream rewrites the first lines of `AGENTS.md` (re-apply the delimited block).

---

### PATCH-006: TTS concurrent output path collision (`tools/tts_tool.py`)
- **Status:** LIVE on `local`, replayed clean at the 2026-07-04 resync (single-line change, no conflict).
- **Files:** `tools/tts_tool.py` (1 line)
- **What:** `text_to_speech_tool` named output files `tts_<YYYYMMDD_HHMMSS>.<fmt>` — second-level precision. Concurrent `/api/audio/speak` requests in the same second collided on one path and raced: one unlinked the file mid-write of another → "TTS provider produced no output" errors (observed with the mod3 command provider during desktop voice playback). Fix adds `%f` (microseconds) to the strftime format so concurrent calls get unique paths.
- **Upstream:** **PR #43911 still OPEN** as of 2026-07-04 — upstream has NOT landed this independently (verified: no commit in the 3,093-commit gap touches this output-path naming). Ping the PR on the next reconciler sweep.
- **Risk:** LOW — additive precision on a filename, no behavior change otherwise.

---

### PATCH-007: Claude Code OAuth credential is read-only (`agent/credential_pool.py`)
- **Status:** ON `local`. **Rebased 2026-07-04** onto upstream's independently-added `openai-codex`-specific serialization branch (`_refresh_entry` split into a dispatcher + `_refresh_entry_impl`) — real conflict, resolved by placing PATCH-007's `anthropic`/`claude_code` early-return branch in the dispatcher ahead of the codex branch, so it still returns before ever reaching `_refresh_entry_impl`'s generic anthropic POST path. Verified: all 101 tests across `test_credential_pool.py` + `test_credential_pool_routing.py` + `test_credential_pool_oauth_writethrough.py` pass, 0 regressions. **Live after each gateway restart.**
- **Files:** `agent/credential_pool.py` (`_refresh_entry`, +~30 lines, additive branch at top)
- **Companion (user-space, EXT-010):** `~/.hermes/bin/claude-token-refresh` — the actuator.
- **What:** For `provider=="anthropic" and source=="claude_code"`, Hermes no longer POSTs the credential's **single-use OAuth refresh token**. Claude Code (CC) is the sole holder/writer of that token (macOS Keychain `Claude Code-credentials`); Hermes POSTing it raced CC and wrote the rotated pair only to `~/.claude/.credentials.json` — a file CC >=2.1.114 ignores — orphaning the rotation and forcing a manual `claude /login`. The drift was observed live 2026-06-03: file token `eb374e46…/exp 00:08` vs keychain `922ff595…/exp 00:49`, one refresh-cycle apart. New behaviour: **mirror** whatever CC currently holds (keychain-first, via the existing `_sync_anthropic_entry_from_credentials_file`); if even the keychain is stale, **delegate** refresh to the owner by running the EXT-010 actuator (a single-flight headless `claude -p`, ~5–9s, only fires when genuinely stale), then re-mirror. One source of truth + one writer ⇒ no race.
- **Why this is the root-cause fix:** the prior 401 bursts after every `/login` or CC token rotation came from Hermes holding/POSTing a token CC had rotated away. Hermes' reconcile stack (keychain-first read, exhausted-entry resync, 5-min 401 cooldown) recovered *reactively* but always after a burst. PATCH-007 makes recovery *structural* — Hermes can't desync because it never owns the refresh.
- **Safety / degradation:** any failure in the branch (actuator missing, subprocess error, still-stale) falls through to `_mark_exhausted` → the **existing** exhausted-entry keychain resync in `_available_entries` recovers on the next selection (no worse than pre-patch). The actuator subprocess runs under the pool lock; a refresh stalls that lock ~5–9s, but only near token expiry (~once per token lifetime), not per request. Irreducible floor: a genuinely **revoked** refresh token can't be re-auth'd headlessly → surfaces as exhausted; needs interactive `claude /login`.
- **Verified:** module compiles + imports in venv; unit test (POST sentinel raises if touched) confirms all three paths — fresh / owner-refreshed / revoked — **never POST** the refresh token; actuator fast-path (fresh) exits 0 in <1s, forced-stale path runs `claude -p` and exits 0 in ~9s.
- **Upstream:** Candidate, but Keychain-vs-file is macOS+CC-specific; the general principle (don't auto-refresh an externally-owned single-use OAuth token) may interest upstream. Until then, local.
- **Interaction note:** EXT-001 `oauth_billing_gate` monkeypatches `agent/anthropic_adapter.py` (outbound billing/routing) — orthogonal to this pool-refresh gate, no shared symbol, but re-check if either changes refresh behaviour.
- **Reconciler action:** watch `agent/credential_pool.py` `_refresh_entry` for upstream churn; the branch is self-contained and re-appliable. Drop only if upstream adds an equivalent "external-owner read-only" credential mode.

---

### PATCH-008: Claude Code OAuth resolve/write path is read-only (`agent/anthropic_adapter.py`)
- **Status:** ON `local`. **Highest-risk re-merge of the 2026-07-04 resync** — manually re-applied onto upstream's own OAuth-credential hardening (`1dde7e2f2`/`5a5396aec`, ported by Chaz), which added a race-*mitigation* "adopt already-refreshed token" fast path but still falls through to a direct POST + file write when no fresher credential is found. Kept upstream's fast path (strictly better, free); routed the fallback through the EXT-010 actuator instead of upstream's POST. `_write_claude_code_credentials` stays a hard no-op. **Live after each gateway restart.**
- **Files:** `agent/anthropic_adapter.py` (`_refresh_oauth_token` → read-only actuator delegation; `_write_claude_code_credentials` → no-op). Tests updated: `tests/agent/test_anthropic_adapter.py` (`TestRefreshOauthToken`, `TestWriteClaudeCodeCredentials`), `tests/agent/test_auxiliary_client.py` (force-refresh test rewritten to assert the read-only contract instead of the historical POST-based flow).
- **Companion:** EXT-010 actuator `~/.hermes/bin/claude-token-refresh` (shared with PATCH-007).
- **What:** Completes PATCH-007. PATCH-007 made `credential_pool._refresh_entry` read-only but left a **second, uncovered refresh path**: `resolve_anthropic_token()` → `_resolve_claude_code_token_from_credentials()` → `_refresh_oauth_token()`, reachable from agent init / usage polls / `_refresh_provider_credentials`. That path still **POSTed** the single-use refresh token (`refresh_anthropic_oauth_pure`) **and wrote** `~/.claude/.credentials.json` (`_write_claude_code_credentials`). Now `_refresh_oauth_token` delegates to the owner via the EXT-010 actuator then re-reads keychain-first (no POST, no write), and `_write_claude_code_credentials` is a hard **no-op** (belt-and-suspenders so no Hermes path can write CC's file; the remaining PATCH-007-dead call sites in `credential_pool` stay import-safe).
- **Why:** Observed live 2026-06-04 *after* the CogOS kernel WriteBack fix (myrgic/cogos#363/#364) eliminated the ~10s kernel churn: a **single** stale write of `~/.claude/.credentials.json` on a token resolve still forced a `/login` (file token ≠ keychain token, keychain valid). The kernel was the dominant (10s-loop) poisoner; this adapter path was the residual (per-event) poisoner. Same root shape as PATCH-007 and the kernel fix: Hermes/kernel must be a strict read-only mirror of a credential Claude Code owns. See [[feedback_convergent_readonly_mirror_pattern]].
- **Safety / degradation:** read-only; on a genuinely revoked token the actuator can't re-auth headlessly → returns None → caller falls back / surfaces interactive `claude /login` (no worse than before). Actuator is single-flight, fires only when stale.
- **Verified (2026-07-04 rebase):** module compiles + imports in venv; 7 unit tests pass in `TestRefreshOauthToken`/`TestWriteClaudeCodeCredentials` (POST/write sentinels raise `AssertionError` if touched — proves no POST, no file write, on both the adopt-fast-path and actuator-fallback branches); full `test_anthropic_adapter.py` + `test_auxiliary_client.py` re-run = **exactly the same 14 pre-existing failures as plain `origin/main`, 0 new failures** (confirmed by diffing against a baseline run on unpatched `origin/main` — `TestResolveAnthropicToken`/`TestResolveWithRefresh`/`TestRunOauthSetupToken`, unrelated test-isolation debt, not introduced by this patch).
- **Interaction note:** EXT-001 `oauth_billing_gate` monkeypatches this same file (outbound billing/routing) — orthogonal to these two credential functions, no shared symbol; re-check if either changes refresh behaviour. Re-verified 2026-07-04: EXT-001's `_patch_adapter()`/`_patch_inbound_reversal()` both return `True` (not fail-open) against the rebased file; live functional check confirms both gates apply correctly through the patched `build_anthropic_kwargs`.
- **Reconciler action:** watch `agent/anthropic_adapter.py` `_refresh_oauth_token` / `_write_claude_code_credentials` for upstream churn; both are self-contained and re-appliable. Drop only if upstream adds an "external-owner read-only" credential mode.

---

### PATCH-009: `/s` alias for `/steer` (`ui-tui/src/app/slash/commands/core.ts`)
- **Status:** ON `local` (this commit). **Live after `npm run build` in `ui-tui/` + TUI relaunch.**
- **Files:** `ui-tui/src/app/slash/commands/core.ts` (one line: `aliases: ['s']` on the `steer` command).
- **What:** Adds `s` as a short alias for the TUI `/steer` slash command (inject a message after the next tool call without interrupting). Uses the existing `aliases?: string[]` field on `SlashCommand` — `registry.ts` already flat-maps `[cmd.name, ...cmd.aliases]` into the command lookup, so no dispatch change needed.
- **Why:** User convenience — `/steer` is used mid-turn and a single-char alias is faster to type. Cosmetic/ergonomic, not behavioral.
- **Collision check:** no existing command or alias named `s` (verified against `core.ts`/`session.ts`). Distinct from the `/q → queue` lesson (#31983): `s` was free.
- **Verified:** `slashParity.test.ts` 3/3 pass; `npm run build` clean (2.9mb bundle); alias confirmed compiled into `dist/entry.js` (`aliases:["s"]...name:"steer"`).
- **Upstream:** **PR #43912 still OPEN** as of 2026-07-04. Confirmed: upstream's `steer` command entry (`ui-tui/src/app/slash/commands/core.ts:599`) still has no `aliases` field — replayed clean, zero conflict.
- **Reconciler action:** trivially re-appliable; drops out at rebase when #43912 merges.

---

### PATCH-010: /restart ack lost to teardown race (`gateway/platforms/base.py`, `gateway/run.py`)
- **Status:** ON `local`. **Adapted at the 2026-07-04 resync** — upstream independently rewrote `cancel_background_tasks` with a bounded 5s hard-cancel + `MAX_DRAIN_ROUNDS` retry-loop (for late-arriving tasks during drain — a related but distinct problem) and refactored the call sites in `gateway/run.py` into a shared `_bounded_adapter_teardown(adapter, platform, *, profile=None)` helper (used identically for primary + secondary-profile adapters). Adaptation: `grace_seconds` param added to both `cancel_background_tasks` (auto-merged cleanly ahead of the existing drain loop) and to `_bounded_adapter_teardown` itself (manually threaded through, so both call sites get the restart-scoped grace consistently with the helper's DRY intent, not just the primary-adapters loop). **Live after each gateway restart.**
- **Files:** `gateway/platforms/base.py` (`cancel_background_tasks` gains `grace_seconds=0.0`), `gateway/run.py` (`_bounded_adapter_teardown` gains `grace_seconds=0.0` and forwards it; both call sites in `_stop_impl` pass `grace_seconds=2.0 if self._restart_requested else 0.0`), `tests/gateway/test_restart_ack_grace.py` (3 tests, unaffected by the run.py-level adaptation since they exercise `base.py` directly).
- **What:** The `/restart` handler queues its ack ("♻ Restarting gateway. If you aren't notified within 60 seconds, restart from the console…") and `request_restart` begins `stop()` 50ms later. With no active agents the drain phase is instant, and `cancel_background_tasks()` cancelled the in-flight `_process_message_background` task mid-HTTPS-send — `CancelledError` propagates silently (no failure log, no retry), so the ack never reached the chat. Observed 3/3 on 2026-06-10 (20:46, 21:38, 21:39): "Sending response (119 chars)" logged, stop 52ms later, no delivery. The fix gives in-flight tasks a bounded grace window (2s, restart path only) to finish naturally before cancellation; plain stops are unchanged.
- **Why it matters beyond cosmetics:** the ack's text is the recovery instruction for the PATCH-003 failure mode — the race ate the one message telling the operator what to do when the gateway bricks.
- **Verified (2026-07-04):** all 3 `test_restart_ack_grace.py` tests pass against the rebased `base.py` body.
- **Upstream:** **PR #43903 still OPEN** as of 2026-07-04 (cross-refs #8008 same-race-class Discord flush, #36913 bounded-shutdown compatibility, #43888 where found). Upstream's own drain-retry rewrite does not carry a `grace_seconds` concept — still a gap.
- **Risk:** LOW — additive default-off parameter; only the planned-restart path changes timing (≤2s slower teardown when a send is in flight).
- **Reconciler action:** watch `cancel_background_tasks` and `_bounded_adapter_teardown` for further churn; drop if upstream adds an equivalent flush-before-cancel.

---

### PATCH-011: `SessionDB._execute_write` None-guard on closed connection (`hermes_state.py`)
- **Status:** ON `local`. Replayed clean at the 2026-07-04 resync (auto-merged, only the PATCHES.md registry itself conflicted). Verified: full `tests/test_hermes_state.py` (303 tests) passes; guard confirmed live via direct smoke test (closed-connection write raises typed `RuntimeError`, not `AttributeError`). **Live after each gateway/cron-worker restart** (editable install — `hermes_state.py` is imported straight from this repo checkout via `__editable__.hermes_agent-0.16.0.pth`, no build/reinstall step; confirmed no separate installed copy exists to shadow it).
- **Files:** `hermes_state.py` (`SessionDB._execute_write`, ~4 lines added before the existing `BEGIN IMMEDIATE` call).
- **What:** `_execute_write` unconditionally called `self._conn.execute("BEGIN IMMEDIATE")` with no guard against `self._conn` having been set to `None` by a concurrent `close()` on the same `SessionDB` instance. Adds an explicit `if self._conn is None: raise RuntimeError(...)` immediately before that call, converting the opaque `AttributeError: 'NoneType' object has no attribute 'execute'` into a typed, self-describing `RuntimeError` that the existing broad `except Exception` callers (e.g. `run_agent.py`'s `_flush_messages_to_session_db`) already catch and log.
- **Why:** Confirmed live race (darkstar `errors.log:2652-2660`, job `hermes-upstream-reconciler`, session `cron_b02d262b332d_20260703_060318`, 2026-07-03 06:21:47): `cron/scheduler.py`'s inactivity-timeout branch tears down `_session_db.close()`/`agent.close()` in its `finally:` block while the timed-out job's own worker thread is still executing (`ThreadPoolExecutor.shutdown(cancel_futures=True)` does not stop an already-running future) — that orphaned thread's later `append_message` call hits `self._conn` as `None`, silently dropping the turn's message row. This diff does not stop the message from being dropped (that's the root-cause fix in `cron/scheduler.py`'s teardown ordering, deferred — see below); it only makes the failure a clean, diagnosable signal instead of a bare `AttributeError`. Triaged as TRIAGE.md issue 2 / `cog`'s zero-row session `20260704_065805_c478c437` is a plausible (not confirmed-identical) instance of the same defect class.
- **Risk:** LOW — additive guard on an already-exceptional path; no behavior change on the non-null-`_conn` path (the overwhelming majority of calls). Blast radius limited to `SessionDB._execute_write` callers, all of which already catch broad `Exception`.
- **Upstream:** Candidate — this is a generic SQLite-connection-lifecycle bug (closed-connection-raced-by-concurrent-writer), not Myrgic-specific. Not yet filed; file against `NousResearch/hermes-agent` per the repo's upstream-first priority order before the next rebase if this proves durable in the field.
- **Deferred (not applied, needs operator review):** a second, root-cause diff was proposed against `cron/scheduler.py` (skip the `_session_db.close()`/`agent.close()` teardown in the `finally:` block specifically when the inactivity-timeout branch fired and the future is not yet `.done()`) — the investigation explicitly flagged this as "a sketch, not a verified patch," noting it changes fd/subprocess-lifetime behavior (resources could be held open indefinitely if the orphaned future never completes) and recommended a `future.add_done_callback`-based refinement instead of a boolean skip, "out of scope for read-only investigation." Per this session's ground rules (do not improvise past a non-minimal/unsafe fix), that diff is NOT applied. Ratified and applied as **PATCH-012** (see entry below); refined from the boolean-skip sketch to the `add_done_callback` ownership-transfer approach with a hard-cap bound.
- **Reconciler action:** watch `hermes_state.py`'s `SessionDB._execute_write`/`close()` for upstream changes to connection-lifecycle handling; drop if upstream adds an equivalent guard or a lock-based fix that supersedes this.

---

### PATCH-012: cron inactivity-timeout teardown races the orphaned worker (`cron/scheduler.py`)
- **Status:** ON `local`. **Rebased 2026-07-04**: the conflict was a pure positional collision, not a functional overlap — upstream independently added a new, unrelated `_guard_job_credential_exfil` security-guard function at the exact same insertion point (right before `run_job`) where PATCH-012's `_teardown_cron_job_resources` was inserted. Resolved by keeping both as sibling module-level functions (upstream's guard first, then the teardown helper); the rest of the diff (`_cron_future` pre-bind, `process_registry.kill_all` in the timeout branch, the `finally:` call site) auto-merged clean with zero manual intervention. Verified: the standalone repro harness (`patch012-repro/`) re-run against the rebased `cron/scheduler.py` body confirms all 4 scenarios (race reproduced pre-patch, write lands post-patch, hard-cap bounds an immortal worker, failure typed at the cap) still hold; full `tests/cron/` suite (618 tests) passes. **Live after each gateway restart** (editable install — `cron/scheduler.py` imports straight from this checkout; the cron scheduler runs inside each gateway's ticker, so restart every gateway).
- **Files:** `cron/scheduler.py` (+172/−39): new module-level `_teardown_cron_job_resources` helper; `_cron_future = None` pre-bind before the job `try:`; `process_registry.kill_all(task_id=_cron_session_id)` added to the inactivity-timeout branch; `finally:` session-db/agent teardown rewired through the helper.
- **What:** After an inactivity timeout, `_run_job_impl`'s `finally:` closed `_session_db` (`SessionDB._conn = None`) and called `agent.close()` while the timed-out job's worker thread was still executing — `ThreadPoolExecutor.shutdown(wait=False, cancel_futures=True)` cannot stop a running future. `agent.close()`'s `kill_all` then unblocked the stuck tool, and the resumed worker's `_flush_messages_to_session_db` hit the closed connection, silently dropping the turn's rows. Fix: the timeout branch does the subprocess kill itself (keeps the unblock behavior); teardown ownership transfers to the future via `add_done_callback` (close runs on the worker thread after its final writes land); a daemon `Timer` (`HERMES_CRON_TEARDOWN_HARDCAP`, default 900s) bounds the deferral so a worker that never finishes cannot leak the SQLite handle/subprocesses/sockets forever. Teardown is idempotent across the two triggers; normal-path behavior is byte-identical (same body, run inline).
- **Why:** Root cause of the PATCH-011 incident class (darkstar `errors.log:2652-2660`, job `hermes-upstream-reconciler`, session `cron_b02d262b332d_20260703_060318`, 2026-07-03 06:21:47; TRIAGE issue 2). PATCH-011 typed the failure; PATCH-012 stops the message loss. Validated by standalone repro (`~/workspaces/cog/.cog/mem/working/2026-07-04-hermes-triage/fixes/patch012-repro/`): before = row dropped with the exact live `AttributeError` signature; after = row lands, teardown deferred to future completion, zero fds left on the db file; hard-cap scenario = teardown forced at the cap while the future still runs, late write fails typed.
- **Risk:** MEDIUM — teardown timing changes on the orphaned-worker path only. Accepted residual: under the hard cap a still-running worker's late write fails (typed via PATCH-011) instead of landing; deferred `end_session` means a timed-out cron session row stays open until its worker exits (more truthful, but a visibility change).
- **Upstream:** filed as NousResearch/hermes-agent#58311 (2026-07-04; branch `fix/cron-teardown-race` on the `chazmaniandinkle` fork) — see `fixes/patch012-upstream-pr.md`. Prior art cross-referenced in the PR thread: upstream #54755 (same failure signature, background-review-fork trigger) and #18004 (orphaned-worker premise). This local patch drops out at rebase when it merges.
- **Interaction note:** PATCH-011 (`hermes_state.py` `_execute_write` closed-connection guard) is the companion, not superseded: it is what makes the hard-cap residual race fail typed. Keep both.
- **Reconciler action:** watch `cron/scheduler.py` `_run_job_impl` (inactivity-timeout branch + `finally:` teardown) and `_teardown_cron_job_resources` for upstream churn; drop at rebase when the upstream PR (or an equivalent deferred-teardown fix) merges.

---

### PATCH-013: OAuth Gate 2 — relocate agent identity out of `system` field (`agent/anthropic_adapter.py`)
- **Status:** ON `local` (newly numbered at the 2026-07-04 resync; previously untracked as a discrete PATCH-ID — it rode inside `da816abe5` alongside the now-upstreamed Gate 1 tool-prefix fix).
- **Files:** `agent/anthropic_adapter.py` (`_relocate_identity_into_first_user` helper + the `is_oauth` branch of `build_anthropic_kwargs`).
- **What:** The OAuth system-content classifier routes a request to the metered overage lane when the `system` field carries a large agent identity block (memory/skill/boundaries markers), even with zero tools. Relocates that identity into the first user turn — the way Claude Code injects `CLAUDE.md` — leaving only the canonical Claude Code system string (`"You are Claude Code, Anthropic's official CLI for Claude."`) in `system`. Block-order safe: if the first user turn leads with a `tool_result` block, the identity is appended after it (a `tool_result` must stay block-0 of a tool-response turn).
- **Split history:** originally landed 2026-05-30 as one half of `da816abe5` ("complete the dual billing-gate fix on the direct Anthropic path"), alongside a `_MCP_TOOL_PREFIX` single→double-underscore fix (Gate 1). At the 2026-07-04 resync, Gate 1 was found **upstreamed independently** (`3d3786929` + `b70a4e753`, same root cause, same fix) and dropped from replay; Gate 2 (this entry) has no upstream equivalent anywhere in the 3,093-commit gap and was manually re-applied on top of upstream's current `build_anthropic_kwargs` body.
- **Verified (2026-07-04):** end-to-end functional check against the rebased body — system field drops from a 50KB+ identity block to the 57-char canonical string; the (sanitized) identity lands correctly in the first user turn; Gate 1's `mcp__` tool-name prefixing (already upstream) is undisturbed; tool_result block-order safety confirmed at the unit level (`_relocate_identity_into_first_user` called directly on a tool_result-leading message list — the full-pipeline test of this case is confounded by upstream's own unrelated `_strip_orphaned_tool_blocks` hardening rejecting a synthetic conversation with no preceding user turn, which never occurs in a real session).
- **Interaction note:** EXT-001 `oauth_billing_gate` installs its own version-independent copy of this same relocation logic and fully replaces `build_anthropic_kwargs` with a wrapper, so the fix holds even if this source-level patch is ever lost to a future reset. This source-level patch and EXT-001 are redundant-but-harmless together (EXT-001 is the durable belt-and-suspenders layer; this patch keeps the vendored source correct in its own right).
- **Upstream:** No commit in the gap touches system-content classification or agent-identity relocation. Not yet filed as a standalone PR (previously bundled with the now-moot Gate 1 half); candidate for a fresh, narrowly-scoped upstream PR.
- **Reconciler action:** watch `agent/anthropic_adapter.py`'s `build_anthropic_kwargs` `is_oauth` branch and `convert_messages_to_anthropic` (upstream's message-history normalization runs before this patch's relocation call) for churn.

---

## Files to Watch (upstream changes here may break a patch or plugin)

| File | Why | Affected |
|------|-----|----------|
| `tools/memory_tool.py` | PATCH-001 | PATCH-001 |
| `gateway/run.py` | startup/shutdown hooks; exit-code site `via_service` logic pairs with PATCH-003 | EXT-002, EXT-003, PATCH-003 |
| `gateway/slash_commands.py` | `_handle_restart_command` service-manager detection | PATCH-003 |
| `AGENTS.md` | First lines (overlay anchor) | PATCH-004 |
| `tools/tts_tool.py` | TTS output path naming | PATCH-006 |
| `agent/anthropic_adapter.py` | oauth_billing_gate wraps `build_anthropic_kwargs` + forces `_MCP_TOOL_PREFIX=""`; `_refresh_oauth_token`/`_write_claude_code_credentials` read-only | EXT-001, PATCH-008 |
| `agent/agent_runtime_helpers.py` | oauth_billing_gate wraps `repair_tool_call` (inbound `mcp__`→registered-name reversal) | EXT-001 |
| `agent/transports/anthropic.py` | oauth_billing_gate wraps `normalize_response` (inbound `mcp__` reversal) | EXT-001 |
| `agent/credential_pool.py` | `_refresh_entry` claude_code read-only branch | PATCH-007, EXT-010 |
| `ui-tui/src/app/slash/commands/core.ts` | `/steer` carries `aliases: ['s']` | PATCH-009 |
| `gateway/platforms/base.py` | `cancel_background_tasks` grace window | PATCH-010 |
| `hermes_state.py` | `SessionDB._execute_write` None-guard on closed connection | PATCH-011 |
| `cron/scheduler.py` | `_run_job_impl` timeout branch + `finally:` teardown → `_teardown_cron_job_resources`; watch for further insertion-point collisions near `run_job` (upstream's `_guard_job_credential_exfil` collided here once already) | PATCH-012 |
| `agent/anthropic_adapter.py` (again) | `build_anthropic_kwargs`'s `is_oauth` branch system-field handling; `convert_messages_to_anthropic`'s message-history normalization runs BEFORE this patch's relocation call | PATCH-013 |
| `gateway/platforms/discord/adapter.py` | Discord voice | EXT-003 |
| `hermes_cli/plugins.py` | Plugin loading | all EXT-* |

## User-Space Extensions (safe — outside this repo, survive any reset)

EXT-001 `oauth_billing_gate` · EXT-002 `mod3_session` · EXT-003 `mod3_voice` · EXT-004 `bw_bridge` · EXT-005 `skill_relevance_gate` · EXT-006 `hermes-achievements` · EXT-007 `auto-voice-reply` hook · EXT-008 `bw-eclipse-key` hook · EXT-009 `mod3-voice-bootstrap` hook · EXT-010 `claude-token-refresh` (`~/.hermes/bin/`, actuator for PATCH-007 — single-flight headless `claude -p` to refresh the CC-owned keychain token on demand).
Full detail: `~/.hermes/journals/hermes-patch-registry.md`.

---

## History

| Date | Action | Patch | Notes |
|------|--------|-------|-------|
| 2026-06-03 | Created in-repo registry | All | Canonical patch list moved into repo on `local` |
| 2026-06-03 | Cherry-picked | PATCH-003 | launchd `/restart` fix from open PR #33393 (`f54c2854e`) |
| 2026-06-03 | Added | PATCH-007 + EXT-010 | Claude Code OAuth credential read-only: stop Hermes POSTing CC's single-use refresh token (no race), delegate refresh to the owner via headless `claude -p` actuator. Root-cause fix for post-`/login` 401 bursts. |
| 2026-06-04 | Added | PATCH-008 | Completes PATCH-007: the uncovered `anthropic_adapter` resolve path (`_refresh_oauth_token`) still POSTed + wrote `~/.claude/.credentials.json`. Made it read-only (actuator delegate + keychain re-read); `_write_claude_code_credentials` → no-op. Residual file-poisoner after the CogOS kernel WriteBack fix (myrgic/cogos#363/#364). |
| 2026-06-03 | Added | PATCH-004 | AGENTS.md Myrgic overlay header |
| 2026-06-03 | Added | PATCH-006 | TTS concurrent output path collision (microsecond timestamp). PATCH-005 (voice chunk pipelining) attempted same session, scrapped — did not fix the gap. |
| 2026-06-04 | Added | PATCH-009 | `/s` alias for the TUI `/steer` slash command (`ui-tui/src/app/slash/commands/core.ts`). One-line `aliases: ['s']`. Parity tests + build verified. |
| 2026-06-10 | Landed | PATCH-001 | Eviction implemented against the test suite and committed to `local` (closes MEMDUMP-001 / t_4f9f0fc7; operator decision 2026-06-10). `stash@{0}` draft now redundant — verify and drop. |
| 2026-06-10 | Superseded | PATCH-003 | Dropped at rebase onto `origin/main` (upstream restart path handles launchd via `darwin` platform check; XPC probe + PR #33393 obsolete). Verify `/restart` after next gateway restart. |
| 2026-06-10 | Rebased | All | `local` stack rebased onto `origin/main` (was 272 behind). 11 commits survive; only conflict was PATCH-003 (dropped). Memory + auth/oauth suites green on the rebased base. |
| 2026-06-10 | Added | PATCH-010 | /restart ack was cancelled mid-send by teardown (50ms handler-to-cancel vs one HTTPS round trip); `cancel_background_tasks` gains a restart-scoped 2s grace. Found while verifying PATCH-003 (operator saw only the post-boot confirmation, never the ack). |
| 2026-06-10 | Re-applied | PATCH-003 | "Superseded" call was wrong — upstream's launchd handling lives only at the exit-code site, never reached because the `/restart` handler (`gateway/slash_commands.py`) doesn't detect launchd. Regression bit live at 20:46 EDT (cog gateway dead after Telegram `/restart`). Re-applied in the handler with `XPC_SERVICE_NAME not in ("", "0")` (interactive shells inherit `=0`). Upstream PR candidate. |
| 2026-07-04 | Added | PATCH-011 | `SessionDB._execute_write` None-guard: closed-connection race (cron inactivity-timeout teardown racing an orphaned worker thread's `append_message`) converted from opaque `AttributeError` to typed `RuntimeError`. Root-cause `cron/scheduler.py` teardown-ordering fix proposed but deferred — needs operator review (fd/subprocess-lifetime tradeoff), see PATCH-011 entry. Hermes triage 2026-07-04, TRIAGE.md issue 2. |
| 2026-07-04 | Added | PATCH-012 | Root-cause fix for the PATCH-011 race: cron job teardown deferred to worker completion via `add_done_callback`, bounded by `HERMES_CRON_TEARDOWN_HARDCAP`; subprocess kill moved into the timeout branch. Upstream PR NousResearch/hermes-agent#58311 filed same session. |
| 2026-07-04 | Rebased | All | `local` (21 commits, `97b15c48c`) rebased onto real `origin/main` tip `86a0c5553` (was 3,093 commits behind — 24 days without a sync run). Isolated clone/worktree, never touched the live `~/.hermes/hermes-agent` checkout. PATCH-003 (+ its pin test) genuinely dropped out empty (upstream `abc3662bf` landed the identical fix). `da816abe5`'s Gate 1 half (mcp_→mcp__) also dropped — upstreamed independently (`3d3786929`/`b70a4e753`); its Gate 2 half re-applied as newly-numbered **PATCH-013**. Real conflicts on PATCH-010 (`cancel_background_tasks`/`_stop_impl` — upstream rewrote the drain mechanism and refactored teardown into `_bounded_adapter_teardown`; `grace_seconds` re-threaded through the new surface), PATCH-007 (`credential_pool._refresh_entry` — upstream added an `openai-codex` serialization branch; claude_code gate reordered ahead of it), PATCH-008 (`_refresh_oauth_token` — upstream's own `1dde7e2f2` race-mitigation fast path kept, POST-fallback re-routed through the EXT-010 actuator; highest-risk hunk of the resync), and PATCH-012 (`cron/scheduler.py` — pure positional collision with upstream's new `_guard_job_credential_exfil`, resolved as sibling functions). All patches verified live in an isolated venv (`uv pip install -e ".[dev]"` in the worktree): PATCH-001 eviction (16/16), PATCH-010 grace window (3/3), PATCH-007 pool (101/101), PATCH-008 read-only contract (7/7 + 0 new failures vs the pre-existing 14-failure adapter-test baseline, confirmed identical on plain `origin/main`), PATCH-011 guard (303/303 + live smoke), PATCH-012 teardown (repro harness 4/4 scenarios + 618/618 cron tests), PATCH-013 Gate 2 (functional end-to-end + EXT-001 attach-point re-verification, all 4 points resolve and the plugin patches successfully). Full report: `~/workspaces/cog/.cog/mem/working/2026-07-04-hermes-resync/rebase-report.md`. Cutover to the production checkout gated on a separate mechanical all-green pass — not run in this session. |
