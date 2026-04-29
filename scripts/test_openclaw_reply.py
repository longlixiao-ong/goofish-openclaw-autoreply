"""Probe OpenClaw /reply contract with item_context payload.

This script only calls OPENCLAW_REPLY_URL and never calls /send.
It does not trigger real Goofish message sending.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test OpenClaw /reply contract without sending Goofish messages")
    parser.add_argument(
        "--url",
        default=os.environ.get("OPENCLAW_REPLY_URL", "").strip(),
        help="OpenClaw /reply URL (default: env OPENCLAW_REPLY_URL)",
    )
    parser.add_argument("--cid", default="test-cid", help="Conversation id")
    parser.add_argument("--toid", default="test-toid", help="Buyer id")
    parser.add_argument("--message", default="还在吗", help="Buyer message for test")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    return parser.parse_args()


def parse_json_or_text(text: str) -> Any:
    raw = text.strip()
    if not raw:
        return ""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def parse_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y"}:
            return True
        if lowered in {"false", "0", "no", "n"}:
            return False
    return None


def pick_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return ""


def parse_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def collect_objects(response: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    def add(value: Any) -> None:
        obj = parse_object(value)
        if obj is not None:
            objects.append(obj)

    add(response)
    top = parse_object(response)
    if top is None:
        return objects

    add(top.get("data"))
    add(top.get("result"))
    add(top.get("output"))
    add(top.get("response"))
    add(top.get("payload"))
    data = parse_object(top.get("data"))
    if data is not None:
        add(data.get("result"))
        add(data.get("output"))
    output = parse_object(top.get("output"))
    if output is not None:
        add(output.get("result"))
    return objects


def normalize_openclaw_response(raw: Any) -> dict[str, Any]:
    response = parse_object(raw) or {"raw": raw}
    objects = collect_objects(response)

    reply = ""
    reply_source = "none"
    for obj in objects:
        candidates = [
            ("reply", obj.get("reply")),
            ("text", obj.get("text")),
            ("message", obj.get("message")),
            ("content", obj.get("content")),
            ("final_reply", obj.get("final_reply")),
            ("answer", obj.get("answer")),
            ("choices[0].message.content", (((obj.get("choices") or [{}])[0]).get("message") or {}).get("content")),
            ("choices[0].text", ((obj.get("choices") or [{}])[0]).get("text")),
        ]
        for source, value in candidates:
            text = pick_text(value)
            if text:
                reply = text
                reply_source = source
                break
        if reply:
            break

    handoff: bool | None = None
    should_send: bool | None = None
    reason = ""
    error = ""

    for obj in objects:
        if handoff is None:
            for value in [
                obj.get("handoff"),
                obj.get("need_handoff"),
                obj.get("needs_handoff"),
                obj.get("needs_human"),
                obj.get("human_handoff"),
            ]:
                parsed = parse_bool(value)
                if parsed is not None:
                    handoff = parsed
                    break

        if should_send is None:
            for value in [obj.get("should_send"), obj.get("shouldSend"), obj.get("send")]:
                parsed = parse_bool(value)
                if parsed is not None:
                    should_send = parsed
                    break

        if not reason:
            for value in [obj.get("reason"), obj.get("handoff_reason"), obj.get("route_reason"), obj.get("block_reason")]:
                text = pick_text(value)
                if text:
                    reason = text
                    break

        if not error:
            for value in [((obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) else obj.get("error")), obj.get("err"), obj.get("exception")]:
                text = pick_text(value)
                if text:
                    error = text
                    break

    if handoff is None:
        handoff = False
    if should_send is None:
        should_send = not handoff

    return {
        "reply": reply,
        "reply_source": reply_source,
        "should_send": should_send,
        "handoff": handoff,
        "reason": reason,
        "error": error,
        "raw_object": response,
    }


def build_test_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "cid": args.cid,
        "toid": args.toid,
        "message": args.message,
        "risk": "normal",
        "risk_reason": "test_request",
        "route_reason": "default_openclaw",
        "handoff": False,
        "handoff_reason": "",
        "dry_run": True,
        "item_context": {
            "available": True,
            "source": "test_fixture",
            "item_count": 1,
            "items": [
                {
                    "item_id": "1234567890",
                    "title": "测试商品-二手耳机",
                    "price": "99",
                    "status": "在售",
                }
            ],
            "section_counts": {"在售": 1},
            "metadata": {"from": "scripts/test_openclaw_reply.py"},
        },
        "item_context_status": "available",
        "item_context_reason": "",
        "customer_service_policy": {
            "mode": "handoff_gate",
            "default_action": "allow_openclaw_autoreply",
            "handoff_only": True,
            "send_guardrails": {
                "must_block_when": ["handoff_true", "should_send_false", "empty_reply", "system_exception"],
                "send_via_bridge_only": True,
            },
        },
    }


def http_post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        data=data,
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            parsed = parse_json_or_text(raw_body)
            return {
                "ok": True,
                "http_status": int(response.status),
                "elapsed_ms": elapsed_ms,
                "body": parsed,
            }
    except urllib.error.HTTPError as exc:
        raw_body = ""
        if exc.fp is not None:
            raw_body = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": False,
            "http_status": int(exc.code),
            "elapsed_ms": elapsed_ms,
            "body": parse_json_or_text(raw_body),
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": False,
            "http_status": None,
            "elapsed_ms": elapsed_ms,
            "body": "",
            "error": str(exc),
        }


def validate_contract(normalized: dict[str, Any]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if parse_bool(normalized.get("should_send")) is None:
        issues.append("missing/invalid should_send")
    if parse_bool(normalized.get("handoff")) is None:
        issues.append("missing/invalid handoff")

    reply_text = pick_text(normalized.get("reply"))
    should_send = parse_bool(normalized.get("should_send"))
    if should_send is True and not reply_text:
        issues.append("should_send=true but reply is empty")

    return (len(issues) == 0, issues)


def main() -> int:
    args = parse_args()
    url = (args.url or "").strip()
    if not url:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "missing OpenClaw URL; set OPENCLAW_REPLY_URL or pass --url",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    payload = build_test_payload(args)
    response = http_post_json(url=url, payload=payload, timeout=args.timeout)
    normalized = normalize_openclaw_response(response.get("body"))
    compatible, issues = validate_contract(normalized)

    report = {
        "ok": bool(response.get("ok")) and compatible,
        "url": url,
        "timeout": args.timeout,
        "send_called": False,
        "request_payload": payload,
        "response_ok": response.get("ok", False),
        "http_status": response.get("http_status"),
        "elapsed_ms": response.get("elapsed_ms"),
        "response_body": response.get("body"),
        "normalized": normalized,
        "compatible": compatible,
        "issues": issues,
    }
    if not response.get("ok"):
        report["transport_error"] = response.get("error", "")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
