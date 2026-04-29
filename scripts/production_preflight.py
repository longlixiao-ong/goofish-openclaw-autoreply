"""Production preflight checks for goofish-openclaw-autoreply runtime.

This script does not call real goofish message send.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any


REQUIRED_ENV_VARS = [
    "OPENCLAW_RUNTIME_MODE",
    "OPENCLAW_CHAT_COMPLETIONS_URL",
    "OPENCLAW_MODEL",
    "OPENCLAW_GATEWAY_TOKEN",
    "BRIDGE_AUTH_TOKEN",
    "AUTOREPLY_STATE_FILE",
    "ITEMS_SNAPSHOT_PATH",
]


def pick_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return ""


def http_call(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = 8.0,
) -> dict[str, Any]:
    data = None
    request_headers = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
        request_headers.setdefault("Accept", "application/json")

    request = urllib.request.Request(url=url, method=method, headers=request_headers, data=data)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body_raw = response.read().decode("utf-8", errors="replace")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            try:
                body = json.loads(body_raw) if body_raw.strip() else {}
            except json.JSONDecodeError:
                body = body_raw
            return {
                "ok": True,
                "status": int(getattr(response, "status", 200)),
                "elapsed_ms": elapsed_ms,
                "body": body,
            }
    except urllib.error.HTTPError as exc:
        body_raw = ""
        if exc.fp is not None:
            body_raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(body_raw) if body_raw.strip() else {}
        except json.JSONDecodeError:
            body = body_raw
        return {
            "ok": False,
            "status": int(exc.code),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "body": body,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "body": "",
            "error": str(exc),
        }


def evaluate() -> tuple[bool, dict[str, Any]]:
    report: dict[str, Any] = {"checks": []}
    ok = True

    missing_vars = [name for name in REQUIRED_ENV_VARS if not pick_text(os.environ.get(name, ""))]
    env_check_ok = len(missing_vars) == 0
    if not env_check_ok:
        ok = False
    report["checks"].append(
        {
            "name": "required_env",
            "ok": env_check_ok,
            "missing": missing_vars,
        }
    )

    bridge_base = pick_text(os.environ.get("BRIDGE_BASE_URL", "http://127.0.0.1:8787")) or "http://127.0.0.1:8787"
    token = pick_text(os.environ.get("BRIDGE_AUTH_TOKEN", ""))
    auth_headers = {"X-Bridge-Token": token} if token else {}
    report["bridge_base_url"] = bridge_base

    health = http_call("GET", f"{bridge_base}/health")
    health_ok = health["ok"] and health["status"] == 200 and isinstance(health.get("body"), dict) and health["body"].get("ok") is True
    if not health_ok:
        ok = False
    report["checks"].append({"name": "bridge_health", "ok": health_ok, "detail": health})

    status = http_call("GET", f"{bridge_base}/autoreply/status", headers=auth_headers)
    status_ok = status["ok"] and status["status"] == 200 and isinstance(status.get("body"), dict) and status["body"].get("ok") is True
    if not status_ok:
        ok = False
    report["checks"].append({"name": "autoreply_status", "ok": status_ok, "detail": status})

    decide_presale_payload = {
        "cid": "preflight-cid-presale",
        "send_user_id": "preflight-user-presale",
        "send_message": "这个还在吗？今天能发吗？",
        "dry_run": True,
    }
    decide_presale = http_call(
        "POST",
        f"{bridge_base}/autoreply/decide",
        headers=auth_headers,
        payload=decide_presale_payload,
        timeout=20.0,
    )
    decide_presale_body = decide_presale.get("body") if isinstance(decide_presale.get("body"), dict) else {}
    decide_presale_ok = (
        decide_presale["status"] == 200
        and isinstance(decide_presale_body, dict)
        and decide_presale_body.get("send") is False
        and decide_presale_body.get("dry_run") is True
    )
    if not decide_presale_ok:
        ok = False
    report["checks"].append({"name": "decide_dry_run_presale", "ok": decide_presale_ok, "detail": decide_presale})

    decide_handoff_payload = {
        "cid": "preflight-cid-handoff",
        "send_user_id": "preflight-user-handoff",
        "send_message": "我要退款，走微信聊",
        "dry_run": True,
    }
    decide_handoff = http_call(
        "POST",
        f"{bridge_base}/autoreply/decide",
        headers=auth_headers,
        payload=decide_handoff_payload,
        timeout=20.0,
    )
    decide_handoff_body = decide_handoff.get("body") if isinstance(decide_handoff.get("body"), dict) else {}
    decide_handoff_ok = (
        decide_handoff["status"] == 200
        and isinstance(decide_handoff_body, dict)
        and decide_handoff_body.get("send") is False
        and decide_handoff_body.get("handoff") is True
    )
    if not decide_handoff_ok:
        ok = False
    report["checks"].append({"name": "decide_dry_run_handoff", "ok": decide_handoff_ok, "detail": decide_handoff})

    send_without_token = http_call(
        "POST",
        f"{bridge_base}/send",
        payload={"cid": "preflight-cid", "toid": "preflight-user", "text": "在的"},
    )
    if token:
        unauthorized_ok = send_without_token["status"] == 401
    else:
        unauthorized_ok = True
    if not unauthorized_ok:
        ok = False
    report["checks"].append(
        {
            "name": "send_without_token",
            "ok": unauthorized_ok,
            "detail": send_without_token,
            "note": "expects 401 only when BRIDGE_AUTH_TOKEN is configured",
        }
    )

    # No authorized /send invocation here: dry-run path must never call /send.
    dry_run_no_send_ok = (
        decide_presale_body.get("send") is False and decide_presale_body.get("dry_run") is True
    )
    if not dry_run_no_send_ok:
        ok = False
    report["checks"].append(
        {
            "name": "dry_run_never_send",
            "ok": dry_run_no_send_ok,
            "detail": {
                "send": decide_presale_body.get("send"),
                "dry_run": decide_presale_body.get("dry_run"),
                "reason": decide_presale_body.get("reason"),
            },
        }
    )

    report["ok"] = ok
    return ok, report


def main() -> int:
    ok, report = evaluate()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
