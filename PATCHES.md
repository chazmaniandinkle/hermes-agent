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
- **Status:** RE-APPLIED 2026-06-10 (same night as the rebase that dropped it). The "superseded by upstream" call was **wrong**: upstream's `darwin` check at the exit-code site (`run.py:5957`) only runs when `via_service=True`, but the `/restart` handler's service detection (refactored into `gateway/slash_commands.py` `_handle_restart_command`) checks only `INVOCATION_ID` (systemd) + container markers — under launchd it takes the `detached=True, via_service=False` branch → clean exit 0 → `KeepAlive{SuccessfulExit=false}` never relaunches. **Bit live 2026-06-10 20:46 EDT:** a Telegram `/restart` left the cog gateway dead for ~25 min. Lesson reconfirmed: clean reapply / "upstream has a launchd mention" ≠ superseded — trace the actual call path.
- **Is:** `_under_service = bool(INVOCATION_ID) or XPC_SERVICE_NAME not in ("", "0")` in `gateway/slash_commands.py` (`_handle_restart_command`). The `"0"` exclusion is new vs the original PATCH-003: interactive macOS shells inherit `XPC_SERVICE_NAME=0` (truthy string), which would misroute non-service runs to exit-75-with-no-reviver. launchd jobs get the real label (verified: `XPC_SERVICE_NAME=ai.hermes.gateway-darkstar` via `ps eww`).
- **Upstream:** PR candidate (successor to the original PR #33393 cherry-pick `f54c2854e`). Pure bugfix, no Myrgic specificity — affects every macOS launchd deployment.
- **Reconciler action:** watch `gateway/slash_commands.py` `_handle_restart_command` service detection; drop when upstream merges an equivalent launchd check **in the handler branch** (not just the exit-code site).

### PATCH-004: Myrgic overlay header in `AGENTS.md`
- **Status:** ON `local` (this commit).
- **Files:** `AGENTS.md` (prepended block, delimited by `MYRGIC-LOCAL-OVERLAY:START/END`)
- **What:** Prepends a "READ FIRST" overlay declaring this a vendored checkout (not Myrgic source), pointing agents at this file + the branch model, ahead of upstream's contributor guide. Solves the "agent treats the fork as its own source" failure mode.
- **Upstream:** Never upstreamed (Myrgic-specific). Permanent local patch.
- **Risk:** LOW — additive, prepended above upstream content; rebase conflicts only if upstream rewrites the first lines of `AGENTS.md` (re-apply the delimited block).

---

### PATCH-006: TTS concurrent output path collision (`tools/tts_tool.py`)
- **Status:** LIVE on `local` (commit `51bd8cf5a`).
- **Files:** `tools/tts_tool.py` (1 line)
- **What:** `text_to_speech_tool` named output files `tts_<YYYYMMDD_HHMMSS>.<fmt>` — second-level precision. Concurrent `/api/audio/speak` requests in the same second collided on one path and raced: one unlinked the file mid-write of another → "TTS provider produced no output" errors (observed with the mod3 command provider during desktop voice playback). Fix adds `%f` (microseconds) to the strftime format so concurrent calls get unique paths.
- **Upstream:** Strong upstream PR candidate — pure bugfix, no Myrgic specificity. The collision affects any concurrent TTS caller, not specific to our setup.
- **Risk:** LOW — additive precision on a filename, no behavior change otherwise.

---

### PATCH-007: Claude Code OAuth credential is read-only (`agent/credential_pool.py`)
- **Status:** ON `local` (this commit). **Live after each gateway restart.**
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
- **Status:** ON `local` (this commit). **Live after each gateway restart.**
- **Files:** `agent/anthropic_adapter.py` (`_refresh_oauth_token` → read-only actuator delegation; `_write_claude_code_credentials` → no-op). Tests updated: `tests/agent/test_anthropic_adapter.py` (`TestRefreshOauthToken`, `TestWriteClaudeCodeCredentials`), `tests/agent/test_auxiliary_client.py`.
- **Companion:** EXT-010 actuator `~/.hermes/bin/claude-token-refresh` (shared with PATCH-007).
- **What:** Completes PATCH-007. PATCH-007 made `credential_pool._refresh_entry` read-only but left a **second, uncovered refresh path**: `resolve_anthropic_token()` → `_resolve_claude_code_token_from_credentials()` → `_refresh_oauth_token()`, reachable from agent init / usage polls / `_refresh_provider_credentials`. That path still **POSTed** the single-use refresh token (`refresh_anthropic_oauth_pure`) **and wrote** `~/.claude/.credentials.json` (`_write_claude_code_credentials`). Now `_refresh_oauth_token` delegates to the owner via the EXT-010 actuator then re-reads keychain-first (no POST, no write), and `_write_claude_code_credentials` is a hard **no-op** (belt-and-suspenders so no Hermes path can write CC's file; the remaining PATCH-007-dead call sites in `credential_pool` stay import-safe).
- **Why:** Observed live 2026-06-04 *after* the CogOS kernel WriteBack fix (myrgic/cogos#363/#364) eliminated the ~10s kernel churn: a **single** stale write of `~/.claude/.credentials.json` on a token resolve still forced a `/login` (file token ≠ keychain token, keychain valid). The kernel was the dominant (10s-loop) poisoner; this adapter path was the residual (per-event) poisoner. Same root shape as PATCH-007 and the kernel fix: Hermes/kernel must be a strict read-only mirror of a credential Claude Code owns. See [[feedback_convergent_readonly_mirror_pattern]].
- **Safety / degradation:** read-only; on a genuinely revoked token the actuator can't re-auth headlessly → returns None → caller falls back / surfaces interactive `claude /login` (no worse than before). Actuator is single-flight, fires only when stale.
- **Verified:** module compiles + imports in venv; 6 updated unit tests pass (POST/write sentinels raise `AssertionError` if touched — proves no POST, no file write); full delta vs pre-patch baseline = **0 new failures**. (Note: 14 pre-existing `TestResolveAnthropicToken`/`TestResolveWithRefresh` failures on `local` are unrelated test-isolation debt predating PATCH-008 — separate cleanup.)
- **Interaction note:** EXT-001 `oauth_billing_gate` monkeypatches this same file (outbound billing/routing) — orthogonal to these two credential functions, no shared symbol; re-check if either changes refresh behaviour.
- **Reconciler action:** watch `agent/anthropic_adapter.py` `_refresh_oauth_token` / `_write_claude_code_credentials` for upstream churn; both are self-contained and re-appliable. Drop only if upstream adds an "external-owner read-only" credential mode.

---

### PATCH-009: `/s` alias for `/steer` (`ui-tui/src/app/slash/commands/core.ts`)
- **Status:** ON `local` (this commit). **Live after `npm run build` in `ui-tui/` + TUI relaunch.**
- **Files:** `ui-tui/src/app/slash/commands/core.ts` (one line: `aliases: ['s']` on the `steer` command).
- **What:** Adds `s` as a short alias for the TUI `/steer` slash command (inject a message after the next tool call without interrupting). Uses the existing `aliases?: string[]` field on `SlashCommand` — `registry.ts` already flat-maps `[cmd.name, ...cmd.aliases]` into the command lookup, so no dispatch change needed.
- **Why:** User convenience — `/steer` is used mid-turn and a single-char alias is faster to type. Cosmetic/ergonomic, not behavioral.
- **Collision check:** no existing command or alias named `s` (verified against `core.ts`/`session.ts`). Distinct from the `/q → queue` lesson (#31983): `s` was free.
- **Verified:** `slashParity.test.ts` 3/3 pass; `npm run build` clean (2.9mb bundle); alias confirmed compiled into `dist/entry.js` (`aliases:["s"]...name:"steer"`).
- **Reconciler action:** trivially re-appliable. Upstream-worthy as a standalone convenience PR if desired; until then carry here. Drop if upstream adds the same alias.

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
| 2026-06-10 | Re-applied | PATCH-003 | "Superseded" call was wrong — upstream's launchd handling lives only at the exit-code site, never reached because the `/restart` handler (`gateway/slash_commands.py`) doesn't detect launchd. Regression bit live at 20:46 EDT (cog gateway dead after Telegram `/restart`). Re-applied in the handler with `XPC_SERVICE_NAME not in ("", "0")` (interactive shells inherit `=0`). Upstream PR candidate. |
