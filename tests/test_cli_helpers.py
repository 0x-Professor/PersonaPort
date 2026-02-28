from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from personaport.cli import _find_latest_export_candidate
from personaport.models import Platform


def test_find_latest_export_candidate_filters_and_sorts(tmp_path: Path) -> None:
    older = tmp_path / "chatgpt_export_old.zip"
    newer = tmp_path / "chatgpt_export_new.zip"
    ignored = tmp_path / "notes.txt"

    older.write_text("old", encoding="utf-8")
    newer.write_text("new", encoding="utf-8")
    ignored.write_text("ignore", encoding="utf-8")

    # Ensure mtime ordering is deterministic across filesystems with coarse precision.
    base_ts = datetime.now(timezone.utc).timestamp()
    os.utime(older, (base_ts - 10, base_ts - 10))
    os.utime(newer, (base_ts, base_ts))

    candidate = _find_latest_export_candidate(
        platform=Platform.CHATGPT,
        search_dirs=[tmp_path],
        not_before=datetime.now(timezone.utc) - timedelta(minutes=5),
    )

    assert candidate is not None
    assert candidate.name == "chatgpt_export_new.zip"
