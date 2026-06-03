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
- **Status:** STASHED (`stash@{0}`) — not yet on `local`.
- **Files:** `tools/memory_tool.py`, `tests/tools/test_memory_tool.py` (+209)
- **What:** Silent Tier 1→Tier 3 eviction at >75% capacity; evicted entries → CogOS cogdocs; Tier 2 pointer index.
- **Risk:** HIGH — core file, likely upstream churn.
- **Path to safety:** Upstream PR, or move to a plugin hook.

### PATCH-002: Untracked test files
- **Status:** UNTRACKED.
- **Files:** `tests/plugins/test_skill_relevance_gate.py`, `tests/tools/test_memory_tool_eviction.py`
- **Risk:** LOW. **Path to safety:** commit on `local`, or move to `~/.hermes/plugins/skill_relevance_gate/tests/`.

### PATCH-003: launchd `/restart` detection (`gateway/run.py`)
- **Status:** LIVE & VERIFIED on `local` (commit `f54c2854e`; gateway runs from `local` as of 2026-06-03). Confirmed end-to-end: Telegram `/restart` exits 75 → launchd relaunches automatically. PENDING upstream merge of **PR #33393**.
- **Files:** `gateway/run.py` (+2/-1)
- **What:** Adds `_under_launchd = bool(os.environ.get("XPC_SERVICE_NAME"))` to `_handle_restart_command` so macOS `/restart` uses `via_service=True` (exit 75) and launchd's `KeepAlive{SuccessfulExit=false}` relaunches it. Without it, `/restart` exits 0 and the gateway stays down.
- **Upstream:** PR https://github.com/NousResearch/hermes-agent/pull/33393 (OPEN, unmerged); issues #37388 (P1), #29180, #38053.
- **Risk:** LOW (2-line) but HIGH-churn file.
- **Reconciler action:** when PR #33393 merges → drop on next rebase. If closed-unmerged → re-home (other launchd-restart PRs: #37094, #37508).

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

## Files to Watch (upstream changes here may break a patch or plugin)

| File | Why | Affected |
|------|-----|----------|
| `tools/memory_tool.py` | PATCH-001 | PATCH-001 |
| `gateway/run.py` | `_handle_restart_command` launchd detect; startup/shutdown hooks | PATCH-003, EXT-002, EXT-003 |
| `AGENTS.md` | First lines (overlay anchor) | PATCH-004 |
| `tools/tts_tool.py` | TTS output path naming | PATCH-006 |
| `agent/anthropic_adapter.py` | Monkeypatched by oauth_billing_gate | EXT-001 |
| `agent/credential_pool.py` | `_refresh_entry` claude_code read-only branch | PATCH-007, EXT-010 |
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
| 2026-06-03 | Added | PATCH-004 | AGENTS.md Myrgic overlay header |
| 2026-06-03 | Added | PATCH-006 | TTS concurrent output path collision (microsecond timestamp). PATCH-005 (voice chunk pipelining) attempted same session, scrapped — did not fix the gap. |
