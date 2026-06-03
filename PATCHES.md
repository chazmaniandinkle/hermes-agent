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
- **Status:** ON `local` (commit `f54c2854e`). PENDING upstream merge of **PR #33393**.
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

## Files to Watch (upstream changes here may break a patch or plugin)

| File | Why | Affected |
|------|-----|----------|
| `tools/memory_tool.py` | PATCH-001 | PATCH-001 |
| `gateway/run.py` | `_handle_restart_command` launchd detect; startup/shutdown hooks | PATCH-003, EXT-002, EXT-003 |
| `AGENTS.md` | First lines (overlay anchor) | PATCH-004 |
| `agent/anthropic_adapter.py` | Monkeypatched by oauth_billing_gate | EXT-001 |
| `gateway/platforms/discord/adapter.py` | Discord voice | EXT-003 |
| `hermes_cli/plugins.py` | Plugin loading | all EXT-* |

## User-Space Extensions (safe — outside this repo, survive any reset)

EXT-001 `oauth_billing_gate` · EXT-002 `mod3_session` · EXT-003 `mod3_voice` · EXT-004 `bw_bridge` · EXT-005 `skill_relevance_gate` · EXT-006 `hermes-achievements` · EXT-007 `auto-voice-reply` hook · EXT-008 `bw-eclipse-key` hook · EXT-009 `mod3-voice-bootstrap` hook.
Full detail: `~/.hermes/journals/hermes-patch-registry.md`.

---

## History

| Date | Action | Patch | Notes |
|------|--------|-------|-------|
| 2026-06-03 | Created in-repo registry | All | Canonical patch list moved into repo on `local` |
| 2026-06-03 | Cherry-picked | PATCH-003 | launchd `/restart` fix from open PR #33393 (`f54c2854e`) |
| 2026-06-03 | Added | PATCH-004 | AGENTS.md Myrgic overlay header |
