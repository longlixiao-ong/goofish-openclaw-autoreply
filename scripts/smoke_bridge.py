"""Local smoke checker for goofish-bridge HTTP endpoints.

This script never calls /send and never triggers real Goofish message sending.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local smoke check for goofish-bridge")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787", help="Bridge base URL")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    return parser.parse_args()


def parse_json_or_text(text: str) -> Any:
    text = text.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def http_call(
    *,
    base_url: str,
    path: str,
    method: str,
    timeout: float,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    data: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = int(response.status)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            return {
                "ok": 200 <= status < 300,
                "method": method,
                "path": path,
                "url": url,
                "http_status": status,
                "elapsed_ms": elapsed_ms,
                "response": parse_json_or_text(raw),
            }
    except urllib.error.HTTPError as exc:
        raw = ""
        if exc.fp is not None:
            raw = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": False,
            "method": method,
            "path": path,
            "url": url,
            "http_status": int(exc.code),
            "elapsed_ms": elapsed_ms,
            "response": parse_json_or_text(raw),
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": False,
            "method": method,
            "path": path,
            "url": url,
            "http_status": None,
            "elapsed_ms": elapsed_ms,
            "response": "",
            "error": str(exc),
        }


def read_enabled_field(payload: Any) -> bool | None:
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("state"), dict):
        enabled = payload["state"].get("enabled")
        if isinstance(enabled, bool):
            return enabled
    enabled = payload.get("enabled")
    if isinstance(enabled, bool):
        return enabled
    return None


def main() -> int:
    args = parse_args()
    results: list[dict[str, Any]] = []

    def run_step(step_name: str, method: str, path: str) -> dict[str, Any]:
        result = http_call(
            base_url=args.base_url,
            path=path,
            method=method,
            timeout=args.timeout,
        )
        result["step"] = step_name
        results.append(result)
        return result

    health = run_step("health", "GET", "/health")
    status_before = run_step("status_before", "GET", "/autoreply/status")
    initial_enabled = read_enabled_field(status_before.get("response"))

    stop_result = run_step("stop", "POST", "/autoreply/stop")
    start_result = run_step("start", "POST", "/autoreply/start")
    status_after_start = run_step("status_after_start", "GET", "/autoreply/status")

    if initial_enabled is True:
        final_restore_action = "start"
        restore_path = "/autoreply/start"
        expected_restored_enabled = True
    elif initial_enabled is False:
        final_restore_action = "stop"
        restore_path = "/autoreply/stop"
        expected_restored_enabled = False
    else:
        final_restore_action = "stop"
        restore_path = "/autoreply/stop"
        expected_restored_enabled = False

    run_step("restore", "POST", restore_path)
    status_restored = run_step("status_restored", "GET", "/autoreply/status")
    restored_enabled = read_enabled_field(status_restored.get("response"))

    checks = [
        {
            "name": "stop_should_disable",
            "expected": False,
            "actual": read_enabled_field(stop_result.get("response")),
            "ok": read_enabled_field(stop_result.get("response")) is False,
        },
        {
            "name": "start_should_enable",
            "expected": True,
            "actual": read_enabled_field(start_result.get("response")),
            "ok": read_enabled_field(start_result.get("response")) is True,
        },
        {
            "name": "status_after_start_should_enable",
            "expected": True,
            "actual": read_enabled_field(status_after_start.get("response")),
            "ok": read_enabled_field(status_after_start.get("response")) is True,
        },
        {
            "name": "restored_status_should_match_initial_or_safe_default",
            "expected": expected_restored_enabled,
            "actual": restored_enabled,
            "ok": restored_enabled is expected_restored_enabled,
        },
    ]

    report = {
        "ok": (
            all(item["ok"] for item in results)
            and all(check["ok"] for check in checks)
            and health.get("ok", False)
        ),
        "base_url": args.base_url,
        "timeout": args.timeout,
        "send_called": False,
        "initial_enabled": initial_enabled,
        "final_restore_action": final_restore_action,
        "restored_enabled": restored_enabled,
        "steps": results,
        "checks": checks,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
