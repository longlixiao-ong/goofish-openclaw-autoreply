"""Refresh current-account item snapshot via goofish-bridge read-only API.

This script only calls read-only item refresh endpoints:
- GET  /items/selling?refresh=true
- GET  /items/snapshot (verification)

It never calls /send and never sends buyer messages.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh item snapshot via goofish-bridge")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787", help="goofish-bridge base URL")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout seconds")
    parser.add_argument("--headless", choices=["true", "false"], default="true")
    parser.add_argument("--sections", default="", help="comma-separated: selling,offline,draft")
    parser.add_argument("--max-scroll-rounds", type=int, default=8)
    parser.add_argument(
        "--via-container",
        action="store_true",
        help="Call bridge endpoints from inside docker container (avoids host networking stack issues).",
    )
    parser.add_argument("--service", default="goofish-bridge", help="Docker Compose service name for bridge")
    parser.add_argument(
        "--compose-cmd",
        default="docker-compose",
        help='Compose command, for example: "docker-compose" or "docker compose"',
    )
    return parser.parse_args()


def http_get_json(url: str, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url=url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body = ""
        if exc.fp is not None:
            body = exc.read().decode("utf-8", errors="replace")
        payload: dict[str, Any] = {"ok": False, "reason": "http_error", "message": str(exc)}
        if body.strip():
            try:
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    payload.update(parsed)
            except json.JSONDecodeError:
                payload["body"] = body
        return payload
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "request_error", "message": str(exc)}


def docker_exec_get_json(
    *,
    compose_cmd: str,
    service: str,
    url: str,
    timeout: float,
) -> dict[str, Any]:
    code = (
        "import json,urllib.request;"
        f"req=urllib.request.Request('{url}',headers={{'Accept':'application/json'}},method='GET');"
        f"res=urllib.request.urlopen(req,timeout={timeout});"
        "print(res.read().decode('utf-8','replace'))"
    )
    cmd = shlex.split(compose_cmd) + ["exec", "-T", service, "python", "-c", code]
    try:
        completed = subprocess.run(cmd, text=True, capture_output=True, timeout=max(10.0, timeout + 10.0))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "docker_exec_error", "message": str(exc)}
    if completed.returncode != 0:
        return {
            "ok": False,
            "reason": "docker_exec_failed",
            "message": (completed.stderr or completed.stdout or "").strip()[:500],
        }
    text = (completed.stdout or "").strip()
    if not text:
        return {"ok": False, "reason": "empty_response", "message": "empty response from docker exec"}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "invalid_json_response", "message": text[:500]}
    if not isinstance(parsed, dict):
        return {"ok": False, "reason": "invalid_response_type", "message": str(type(parsed))}
    return parsed


def main() -> int:
    args = parse_args()
    refresh_query = {
        "refresh": "true",
        "headless": args.headless,
        "max_scroll_rounds": str(max(1, int(args.max_scroll_rounds))),
    }
    if args.sections.strip():
        refresh_query["sections"] = args.sections.strip()

    refresh_url = (
        f"{args.base_url.rstrip('/')}/items/selling?"
        f"{urllib.parse.urlencode(refresh_query, doseq=False)}"
    )
    fetch = http_get_json
    if args.via_container:
        refresh_payload = docker_exec_get_json(
            compose_cmd=args.compose_cmd,
            service=args.service,
            url=refresh_url,
            timeout=args.timeout,
        )
    else:
        refresh_payload = fetch(refresh_url, timeout=args.timeout)

    summary: dict[str, Any] = {
        "refresh_url": refresh_url,
        "refresh_ok": bool(refresh_payload.get("ok") is True),
        "reason": refresh_payload.get("reason", ""),
        "message": refresh_payload.get("message", ""),
        "hint": refresh_payload.get("hint", ""),
        "item_count": int(refresh_payload.get("item_count", 0) or 0),
        "section_counts": refresh_payload.get("section_counts", {}),
        "snapshot_path": refresh_payload.get("snapshot_path", ""),
    }

    snapshot_ok = False
    snapshot_payload: dict[str, Any] = {}
    if summary["refresh_ok"]:
        snapshot_url = f"{args.base_url.rstrip('/')}/items/snapshot"
        if args.via_container:
            snapshot_payload = docker_exec_get_json(
                compose_cmd=args.compose_cmd,
                service=args.service,
                url=snapshot_url,
                timeout=args.timeout,
            )
        else:
            snapshot_payload = fetch(snapshot_url, timeout=args.timeout)
        snapshot_ok = bool(snapshot_payload.get("ok") is True)
        summary["snapshot_ok"] = snapshot_ok
        summary["snapshot_item_count"] = int(snapshot_payload.get("item_count", 0) or 0)
        summary["snapshot_reason"] = snapshot_payload.get("reason", "")
    else:
        summary["snapshot_ok"] = False
        summary["snapshot_item_count"] = 0
        summary["snapshot_reason"] = "refresh_failed"

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not summary["refresh_ok"]:
        return 1
    if not snapshot_ok:
        print(json.dumps(snapshot_payload, ensure_ascii=False, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
