"""Forward `goofish message watch` JSONL events to n8n.

This MVP process does not generate replies and does not send messages.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

import requests


def post_event(url: str, event: dict[str, Any]) -> None:
    response = requests.post(url, json=event, timeout=10)
    response.raise_for_status()


def main() -> int:
    webhook_url = os.environ.get("N8N_WEBHOOK_URL")
    if not webhook_url:
        print("N8N_WEBHOOK_URL is required", file=sys.stderr)
        return 2

    while True:
        proc = subprocess.Popen(
            ["goofish", "message", "watch"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line.startswith("{"):
                    print(line, flush=True)
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if event.get("event") != "message":
                    continue

                try:
                    post_event(webhook_url, event)
                except Exception as exc:  # noqa: BLE001
                    print(f"failed to post event to n8n: {exc}", file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            proc.terminate()
            return 0
        except Exception as exc:  # noqa: BLE001
            print(f"watcher loop error: {exc}", file=sys.stderr, flush=True)
        finally:
            try:
                proc.terminate()
            except Exception:
                pass

        time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
