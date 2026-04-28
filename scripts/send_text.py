"""Send a text message through `goofish message send` and print JSON result."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from typing import Any


SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)\b(cookie|api[_-]?key|authorization|token)\b\s*[:=]\s*([^\s,;]+)"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"(?i)([?&](?:token|key|api_key|apikey|signature)=)[^&\s]+"), r"\1<redacted>"),
]


def redact_sensitive(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send text message via goofish-cli")
    parser.add_argument("--cid", required=True, help="Conversation ID")
    parser.add_argument("--toid", required=True, help="Receiver user ID")
    parser.add_argument("--text", required=True, help="Message text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    text = args.text.strip()
    if not text:
        print_json({"ok": False, "error": "text is empty"})
        return 2

    timeout_seconds = int(os.environ.get("GOOFISH_SEND_TIMEOUT_SECONDS", "30"))
    cmd = [
        "goofish",
        "message",
        "send",
        "--cid",
        args.cid,
        "--toid",
        args.toid,
        "--text",
        text,
    ]

    try:
        completed = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        print_json({"ok": False, "error": "goofish command not found"})
        return 127
    except subprocess.TimeoutExpired:
        print_json({"ok": False, "error": "goofish message send timed out"})
        return 124

    result = {
        "ok": completed.returncode == 0,
        "cid": args.cid,
        "toid": args.toid,
        "exit_code": completed.returncode,
        "stdout": redact_sensitive((completed.stdout or "").strip()),
        "stderr": redact_sensitive((completed.stderr or "").strip()),
    }
    print_json(result)
    if completed.returncode != 0:
        return completed.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
