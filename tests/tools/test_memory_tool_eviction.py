"""Tests for the silent eviction (Tier 1 → Tier 3) feature in MemoryStore.

These tests cover:
  1. Silent eviction: when add() would exceed 75% of limit, the oldest entry
     is evicted to substrate rather than surfacing an error.
  2. Cogdoc is written to the overflow directory with correct frontmatter.
  3. Tier 2 index pointer is appended.
  4. Eviction fails gracefully (no user-visible error on substrate write failure).
  5. Very large entry (> limit itself) falls through to hard error rather than
     looping forever.
  6. scan_tier2_index_for_hot_entries() returns tagged URIs correctly.
"""

import datetime
import os
import textwrap
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tools.memory_tool import (
    MemoryStore,
    ENTRY_DELIMITER,
    get_substrate_overflow_dir,
    get_tier2_index_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_store(tmp_path, memory_limit=200, user_limit=100):
    """Build a MemoryStore whose memory dir is inside tmp_path."""
    with patch("tools.memory_tool.get_memory_dir", return_value=tmp_path):
        store = MemoryStore(memory_char_limit=memory_limit, user_char_limit=user_limit)
    return store


def fill_store_to_pct(store, target, pct, tmp_path):
    """Fill a MemoryStore target to approximately pct of its limit."""
    limit = store._char_limit(target)
    target_chars = int(limit * pct)
    # Use short entries of ~20 chars each
    chunk = "A" * 18  # 18 chars + delimiters
    added = 0
    i = 0
    with patch("tools.memory_tool.get_memory_dir", return_value=tmp_path):
        while added < target_chars:
            entry = f"{chunk}{i:04d}"  # unique
            result = store.add(target, entry)
            if not result.get("success"):
                break
            added += len(entry) + len(ENTRY_DELIMITER)
            i += 1


# ---------------------------------------------------------------------------
# Test: overflow dir resolves / fallback works
# ---------------------------------------------------------------------------

class TestSubstratePathHelpers:
    def test_overflow_dir_returns_path(self, tmp_path):
        """get_substrate_overflow_dir returns a Path; primary or fallback."""
        with patch("tools.memory_tool.Path.home", return_value=tmp_path):
            d = get_substrate_overflow_dir()
        assert isinstance(d, Path)
        assert d.exists()


# ---------------------------------------------------------------------------
# Test: _write_cogdoc_to_substrate
# ---------------------------------------------------------------------------

class TestWriteCogdocToSubstrate:
    def test_creates_cogdoc_with_frontmatter(self, tmp_path):
        overflow_dir = tmp_path / "hermes-overflow"
        tier2_index = tmp_path / "hermes-memory-index.cog.md"
        # seed a minimal index so append path runs
        tier2_index.write_text("| header | uri | date | tags |\n|---|---|---|---|\n", encoding="utf-8")

        with (
            patch("tools.memory_tool.get_substrate_overflow_dir", return_value=overflow_dir),
            patch("tools.memory_tool.get_tier2_index_path", return_value=tier2_index),
        ):
            uri = MemoryStore._write_cogdoc_to_substrate("User prefers dark mode.\nSecond line.", "memory")

        # At least one cogdoc was created in the overflow dir
        cogdocs = list(overflow_dir.glob("*.cog.md"))
        assert len(cogdocs) == 1
        text = cogdocs[0].read_text(encoding="utf-8")
        assert "id:" in text
        assert "memory-overflow" in text
        assert "User prefers dark mode." in text
        assert "hermes-evicted" in text

    def test_appends_pointer_to_tier2_index(self, tmp_path):
        overflow_dir = tmp_path / "hermes-overflow"
        tier2_index = tmp_path / "hermes-memory-index.cog.md"
        tier2_index.write_text("| summary | uri | date | tags |\n|---|---|---|---|\n", encoding="utf-8")

        with (
            patch("tools.memory_tool.get_substrate_overflow_dir", return_value=overflow_dir),
            patch("tools.memory_tool.get_tier2_index_path", return_value=tier2_index),
        ):
            MemoryStore._write_cogdoc_to_substrate("Some memory content", "user")

        index_text = tier2_index.read_text(encoding="utf-8")
        # Should have at least one new table row appended
        lines = [l for l in index_text.splitlines() if l.startswith("|") and "hermes-evicted" in l]
        assert len(lines) >= 1
        assert "user" in lines[0]

    def test_no_crash_if_tier2_index_missing(self, tmp_path):
        overflow_dir = tmp_path / "hermes-overflow"
        missing_index = tmp_path / "does-not-exist.cog.md"

        with (
            patch("tools.memory_tool.get_substrate_overflow_dir", return_value=overflow_dir),
            patch("tools.memory_tool.get_tier2_index_path", return_value=missing_index),
        ):
            # Should not raise
            uri = MemoryStore._write_cogdoc_to_substrate("content", "memory")
        assert uri  # returned something


# ---------------------------------------------------------------------------
# Test: _evict_oldest_to_substrate
# ---------------------------------------------------------------------------

class TestEvictOldestToSubstrate:
    def test_removes_oldest_entry(self, tmp_path):
        store = make_store(tmp_path, memory_limit=500)
        overflow_dir = tmp_path / "hermes-overflow"
        tier2_index = tmp_path / "hermes-memory-index.cog.md"
        tier2_index.write_text("| s | u | d | t |\n|---|---|---|---|\n", encoding="utf-8")

        with patch("tools.memory_tool.get_memory_dir", return_value=tmp_path):
            store.memory_entries = ["oldest entry", "newer entry", "newest entry"]

        with (
            patch("tools.memory_tool.get_substrate_overflow_dir", return_value=overflow_dir),
            patch("tools.memory_tool.get_tier2_index_path", return_value=tier2_index),
            patch("tools.memory_tool.get_memory_dir", return_value=tmp_path),
        ):
            ok = store._evict_oldest_to_substrate("memory")

        assert ok is True
        assert "oldest entry" not in store.memory_entries
        assert "newer entry" in store.memory_entries

    def test_returns_false_on_empty_store(self, tmp_path):
        store = make_store(tmp_path, memory_limit=500)
        store.memory_entries = []
        result = store._evict_oldest_to_substrate("memory")
        assert result is False

    def test_returns_false_when_cogdoc_write_fails(self, tmp_path):
        store = make_store(tmp_path, memory_limit=500)
        store.memory_entries = ["an entry"]

        with patch.object(MemoryStore, "_write_cogdoc_to_substrate", side_effect=OSError("disk full")):
            ok = store._evict_oldest_to_substrate("memory")

        assert ok is False
        # Entry should NOT have been removed since write failed
        assert "an entry" in store.memory_entries


# ---------------------------------------------------------------------------
# Test: add() with silent eviction
# ---------------------------------------------------------------------------

class TestAddSilentEviction:
    def test_eviction_triggered_when_above_75pct(self, tmp_path):
        """When store is >75% full, add() evicts oldest entry silently."""
        overflow_dir = tmp_path / "hermes-overflow"
        tier2_index = tmp_path / "hermes-memory-index.cog.md"
        tier2_index.write_text("| s | u | d | t |\n|---|---|---|---|\n", encoding="utf-8")

        # limit=100.  Build entries that total ~80 chars so we're >75%.
        # ENTRY_DELIMITER is 3 chars ("\n§\n"), so 3 entries of ~25 chars each
        # joined by 2 delimiters = 75 + 6 = 81 chars.
        e1 = "A" * 25  # oldest
        e2 = "B" * 25
        e3 = "C" * 25
        store = make_store(tmp_path, memory_limit=100)
        # Write to disk so _reload_target picks them up under lock
        mem_file = tmp_path / "MEMORY.md"
        from tools.memory_tool import ENTRY_DELIMITER
        mem_file.write_text(ENTRY_DELIMITER.join([e1, e2, e3]), encoding="utf-8")
        store.memory_entries = [e1, e2, e3]

        with (
            patch("tools.memory_tool.get_substrate_overflow_dir", return_value=overflow_dir),
            patch("tools.memory_tool.get_tier2_index_path", return_value=tier2_index),
            patch("tools.memory_tool.get_memory_dir", return_value=tmp_path),
        ):
            result = store.add("memory", "X" * 20)  # 20 chars, total would be 81+3+20=104 > 100

        # Should succeed (eviction freed space)
        assert result["success"] is True, result.get("error")
        # Oldest entry (e1) should be gone from Tier 1
        assert e1 not in store.memory_entries
        # New entry should be present
        assert "X" * 20 in store.memory_entries
        # Cogdoc should exist
        cogdocs = list(overflow_dir.glob("*.cog.md"))
        assert len(cogdocs) >= 1

    def test_no_eviction_when_below_75pct(self, tmp_path):
        """When store is <75% full, add() does not evict."""
        overflow_dir = tmp_path / "hermes-overflow"
        store = make_store(tmp_path, memory_limit=1000)
        store.memory_entries = ["short"]

        with (
            patch("tools.memory_tool.get_substrate_overflow_dir", return_value=overflow_dir),
            patch("tools.memory_tool.get_memory_dir", return_value=tmp_path),
        ):
            result = store.add("memory", "another short entry")

        assert result["success"] is True
        # No cogdocs created (no eviction needed)
        cogdocs = list(overflow_dir.glob("*.cog.md")) if overflow_dir.exists() else []
        assert len(cogdocs) == 0

    def test_hard_error_when_entry_too_large(self, tmp_path):
        """Entry larger than the whole limit → hard error (no infinite eviction loop)."""
        store = make_store(tmp_path, memory_limit=50)
        store.memory_entries = []

        result = store.add("memory", "X" * 60)  # 60 chars > 50 char limit

        assert result["success"] is False
        assert "exceed" in result["error"].lower() or "chars" in result["error"].lower()

    def test_hard_error_returned_when_eviction_fails(self, tmp_path):
        """If eviction itself fails, add() returns the normal hard error."""
        # limit=100.  3 entries of 25 chars = 81 chars > 75%.
        # New entry would push total over 100.
        e1 = "E" * 25
        e2 = "F" * 25
        e3 = "G" * 25
        store = make_store(tmp_path, memory_limit=100)
        mem_file = tmp_path / "MEMORY.md"
        from tools.memory_tool import ENTRY_DELIMITER
        mem_file.write_text(ENTRY_DELIMITER.join([e1, e2, e3]), encoding="utf-8")
        store.memory_entries = [e1, e2, e3]

        with (
            patch("tools.memory_tool.get_memory_dir", return_value=tmp_path),
            patch.object(MemoryStore, "_evict_oldest_to_substrate", return_value=False),
        ):
            result = store.add("memory", "H" * 20)

        # Should fall back to hard error, not crash
        assert result["success"] is False
        assert "exceed" in result["error"].lower() or "chars" in result["error"].lower()

    def test_empty_content_still_rejected(self, tmp_path):
        store = make_store(tmp_path, memory_limit=100)
        result = store.add("memory", "")
        assert result["success"] is False
        assert "empty" in result["error"].lower()

    def test_eviction_writes_cogdoc_with_evicted_content(self, tmp_path):
        """The evicted cogdoc must contain the evicted entry's content."""
        overflow_dir = tmp_path / "hermes-overflow"
        tier2_index = tmp_path / "hermes-memory-index.cog.md"
        tier2_index.write_text("| s | u | d | t |\n|---|---|---|---|\n", encoding="utf-8")

        # limit=100, fill >75% with entries that include a unique marker in the first entry
        unique_marker = "UNIQUE-EVICTION-CONTENT-XYZ"
        e1 = unique_marker  # oldest — will be evicted
        e2 = "B" * 25
        e3 = "C" * 25
        store = make_store(tmp_path, memory_limit=100)
        mem_file = tmp_path / "MEMORY.md"
        from tools.memory_tool import ENTRY_DELIMITER
        mem_file.write_text(ENTRY_DELIMITER.join([e1, e2, e3]), encoding="utf-8")
        store.memory_entries = [e1, e2, e3]

        with (
            patch("tools.memory_tool.get_substrate_overflow_dir", return_value=overflow_dir),
            patch("tools.memory_tool.get_tier2_index_path", return_value=tier2_index),
            patch("tools.memory_tool.get_memory_dir", return_value=tmp_path),
        ):
            store.add("memory", "new-entry-to-trigger-eviction-999")

        cogdocs = list(overflow_dir.glob("*.cog.md"))
        assert any(unique_marker in c.read_text(encoding="utf-8") for c in cogdocs), \
            "Evicted content should appear in a cogdoc"


# ---------------------------------------------------------------------------
# Test: scan_tier2_index_for_hot_entries
# ---------------------------------------------------------------------------

class TestScanTier2IndexForHot:
    def test_returns_hot_uris(self, tmp_path):
        index = tmp_path / "index.cog.md"
        index.write_text(textwrap.dedent("""\
            ---
            id: hermes-memory-index
            ---
            ## Index

            | summary | uri | date | tags |
            |---|---|---|---|
            | entry about Darkstar | cog://mem/semantic/architecture/darkstar.cog.md | 2026-05-25 | darkstar,hot |
            | some old entry | cog://mem/working/old.cog.md | 2026-04-01 | cold,archived |
            | another hot entry | cog://mem/working/hot2.cog.md | 2026-05-25 | hot,identity |
        """), encoding="utf-8")

        store = MemoryStore()
        with patch("tools.memory_tool.get_tier2_index_path", return_value=index):
            hot = store.scan_tier2_index_for_hot_entries()

        assert "cog://mem/semantic/architecture/darkstar.cog.md" in hot
        assert "cog://mem/working/hot2.cog.md" in hot
        assert "cog://mem/working/old.cog.md" not in hot

    def test_returns_empty_when_index_missing(self, tmp_path):
        store = MemoryStore()
        missing = tmp_path / "no-such-index.cog.md"
        with patch("tools.memory_tool.get_tier2_index_path", return_value=missing):
            hot = store.scan_tier2_index_for_hot_entries()
        assert hot == []

    def test_no_crash_on_malformed_index(self, tmp_path):
        index = tmp_path / "broken.cog.md"
        index.write_text("not a table at all\n\nrandom content\n", encoding="utf-8")
        store = MemoryStore()
        with patch("tools.memory_tool.get_tier2_index_path", return_value=index):
            hot = store.scan_tier2_index_for_hot_entries()
        assert isinstance(hot, list)
