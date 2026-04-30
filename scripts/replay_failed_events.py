"""Inspect watcher dead-letter events in safe dry-run mode.

This tool never sends HTTP requests and never replays events automatically.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run inspect failed watcher events")
    parser.add_argument(
        "--path",
        default=(os.environ.get("WATCHER_FAILED_EVENTS_PATH", "logs/failed_events.jsonl") or "").strip(),
        help="Dead-letter JSONL path (default: WATCHER_FAILED_EVENTS_PATH or logs/failed_events.jsonl)",
    )
    parser.add_argument("--limit", type=int, default=50, help="Max rows to display")
    parser.add_argument("--tail", action="store_true", help="Show latest rows first")
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def main() -> int:
    args = parse_args()
    target = Path(args.path or "logs/failed_events.jsonl")
    rows = load_rows(target)
    if args.tail:
        rows = list(reversed(rows))

    safe_limit = max(1, int(args.limit))
    selected = rows[:safe_limit]
    report = {
        "ok": True,
        "dry_run": True,
        "replay_enabled": False,
        "path": str(target),
        "total_rows": len(rows),
        "display_count": len(selected),
        "events_to_replay": selected,
        "note": "dry-run only; no HTTP requests made, no messages sent",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
