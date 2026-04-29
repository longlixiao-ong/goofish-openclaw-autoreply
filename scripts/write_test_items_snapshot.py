"""Write a local fake item snapshot for dry-run verification only.

This script does not access Goofish/Xianyu, does not read cookies, and does not send messages.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    default_output = repo_root / "data" / "items_snapshot.json"
    parser = argparse.ArgumentParser(description="Write local fake items snapshot for dry-run")
    parser.add_argument(
        "--output",
        default=str(default_output),
        help="Snapshot output path (default: data/items_snapshot.json)",
    )
    return parser.parse_args()


def build_fake_snapshot() -> dict:
    item = {
        "item_id": "test-item-001",
        "title": "测试商品：iPhone 15 手机壳",
        "price": "19.9",
        "status": "selling",
        "status_label": "在售",
        "href": "https://example.invalid/item/test-item-001",
        "image_url": "",
    }
    return {
        "ok": True,
        "item_count": 1,
        "items": [item],
        "section_counts": {"selling": 1, "offline": 0, "draft": 0},
        "metadata": {
            "source": "local_test_fixture",
            "account_verified": False,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "sections": ["selling"],
            "profile_url": "",
            "headless": True,
            "account": {"user_id": "test-account"},
        },
    }


def main() -> int:
    args = parse_args()
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_fake_snapshot()
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote snapshot: {output_path}")
    print("item_count=1 item_id=test-item-001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
