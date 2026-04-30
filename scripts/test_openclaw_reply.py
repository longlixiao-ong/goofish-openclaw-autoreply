"""Probe OpenClaw Gateway /v1/chat/completions contract without calling /send."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test OpenClaw Gateway chat-completions contract")
    parser.add_argument(
        "--url",
        default=os.environ.get("OPENCLAW_CHAT_COMPLETIONS_URL", "").strip(),
        help="OpenClaw Gateway /v1/chat/completions URL",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip(),
        help="OpenClaw Gateway Bearer token",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("OPENCLAW_MODEL", "openclaw/default").strip() or "openclaw/default",
        help="OpenAI-compatible route/model field exposed by OpenClaw Gateway",
    )
    parser.add_argument("--cid", default="test-cid", help="Conversation id")
    parser.add_argument("--toid", default="test-toid", help="Buyer id")
    parser.add_argument("--message", default="还在吗", help="Buyer message for test")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument("--self-check", action="store_true", help="Run offline checks only, no HTTP calls")
    return parser.parse_args()


def to_optional_string(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def pick_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return ""


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


def strip_markdown_json_fence(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    json_prefix = re.match(r"^json\s*\n([\s\S]*)$", text, re.IGNORECASE)
    if json_prefix and json_prefix.group(1):
        return json_prefix.group(1).strip()
    full_fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if full_fence and full_fence.group(1):
        return full_fence.group(1).strip()
    first_fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if first_fence and first_fence.group(1):
        return first_fence.group(1).strip()
    return text


def parse_json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = strip_markdown_json_fence(value)
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict):
            return parsed
    return None


def pick_risk(value: Any) -> str:
    lowered = to_optional_string(value).lower()
    if lowered in {"low", "medium", "high"}:
        return lowered
    return "medium"


def detect_external_contact(text: str) -> tuple[bool, str]:
    checks = [
        ("微信", r"微信"),
        ("QQ", r"qq"),
        ("支付宝", r"支付宝"),
        ("银行卡", r"银行卡"),
        ("转账", r"转账"),
        ("线下", r"线下"),
        ("手机号", r"手机号"),
        ("vx", r"\bvx\b"),
        ("wechat", r"wechat"),
    ]
    for label, pattern in checks:
        if re.search(pattern, text, re.IGNORECASE):
            return True, label
    return False, ""


def detect_abnormal_text(text: str) -> tuple[bool, str]:
    if not text.strip():
        return True, "empty_text"
    checks = [
        ("reasoning_leak", r"<\s*/?\s*think\s*>"),
        ("reasoning_leak", r"\breasoning\b"),
        ("reasoning_leak", r"\banalysis\b"),
        ("reasoning_leak", r"思考过程|推理过程|链路推理|内部推理"),
        ("error_leak", r"traceback"),
        ("error_leak", r"stack\s*trace"),
        ("error_leak", r"\bexception\b"),
        ("error_leak", r"\b(?:undefined|null|nan)\b"),
    ]
    for label, pattern in checks:
        if re.search(pattern, text, re.IGNORECASE):
            return True, label
    return False, ""


def build_openai_chat_request(args: argparse.Namespace) -> dict[str, Any]:
    user_payload = {
        "cid": args.cid,
        "send_user_id": args.toid,
        "buyer_message": args.message,
        "content_type": 1,
        "item_context": {
            "available": True,
            "source": "test_fixture",
            "item_count": 1,
            "items": [
                {
                    "item_id": "1234567890",
                    "title": "测试商品-二手耳机",
                    "price": "99",
                    "status": "selling",
                    "status_label": "在售",
                }
            ],
        },
        "conversation_state": {
            "dry_run": True,
            "autoreply_enabled": True,
            "auto_send_enabled": True,
            "dedup_key": "self-check-key",
            "cooldown_seconds": 0,
            "remaining_seconds": 0,
        },
        "customer_service_policy": {
            "mode": "openclaw_master_control",
            "default_action": "openclaw_decides_reply_strategy",
            "handoff_keywords_enabled": True,
            "max_reply_chars_must_respect": True,
            "send_via_bridge_only": True,
        },
        "bridge_guardrails": {
            "bridge_role": "security_gateway_only",
            "must_fail_closed_when": [
                "handoff_true",
                "should_send_false",
                "empty_reply",
                "invalid_json",
                "html_or_error_page",
                "abnormal_or_reasoning_leak",
                "external_contact_detected",
                "openclaw_request_failed",
            ],
            "final_send_endpoint": "/send",
        },
        "dry_run": True,
        "max_reply_chars": 80,
    }
    return {
        "url": args.url.strip(),
        "headers": {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {args.token.strip()}",
        },
        "payload": {
            "model": args.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是OpenClaw客服Agent。只允许输出JSON对象，且必须包含 "
                        "reply、should_send、handoff、reason、risk。"
                    ),
                },
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "user": args.cid or args.toid,
        },
    }


def http_post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url=url, method="POST", headers=headers, data=data)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            parsed = parse_json_object(raw_body)
            return {
                "ok": True,
                "http_status": int(response.status),
                "elapsed_ms": elapsed_ms,
                "body": parsed if parsed is not None else raw_body,
            }
    except urllib.error.HTTPError as exc:
        raw_body = ""
        if exc.fp is not None:
            raw_body = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        parsed = parse_json_object(raw_body)
        return {
            "ok": False,
            "http_status": int(exc.code),
            "elapsed_ms": elapsed_ms,
            "body": parsed if parsed is not None else raw_body,
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


def normalize_openclaw_response(raw: Any) -> dict[str, Any]:
    envelope = parse_json_object(raw)
    if envelope is None:
        return {
            "reply": "",
            "reply_source": "none",
            "should_send": False,
            "handoff": True,
            "reason": "",
            "risk": "high",
            "error": "openclaw_response_not_json",
            "openclaw_output": None,
            "raw_object": {"raw": raw},
        }

    choices = envelope.get("choices")
    if not isinstance(choices, list) or not choices:
        return {
            "reply": "",
            "reply_source": "none",
            "should_send": False,
            "handoff": True,
            "reason": "",
            "risk": "high",
            "error": "openclaw_missing_choices",
            "openclaw_output": None,
            "raw_object": envelope,
        }

    first_choice = choices[0] if isinstance(choices[0], dict) else {}
    message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
    content_raw = pick_text(message.get("content"))
    if not content_raw:
        return {
            "reply": "",
            "reply_source": "none",
            "should_send": False,
            "handoff": True,
            "reason": "",
            "risk": "high",
            "error": "openclaw_missing_content",
            "openclaw_output": None,
            "raw_object": envelope,
        }

    lowered = content_raw.lower()
    if lowered.startswith("<!doctype html") or lowered.startswith("<html"):
        return {
            "reply": "",
            "reply_source": "choices[0].message.content",
            "should_send": False,
            "handoff": True,
            "reason": "",
            "risk": "high",
            "error": "openclaw_html_response",
            "openclaw_output": None,
            "raw_object": envelope,
        }

    output = parse_json_object(content_raw)
    if output is None:
        return {
            "reply": "",
            "reply_source": "choices[0].message.content",
            "should_send": False,
            "handoff": True,
            "reason": "",
            "risk": "high",
            "error": "openclaw_content_non_json",
            "openclaw_output": None,
            "raw_object": envelope,
        }

    handoff = parse_bool(output.get("handoff"))
    should_send = parse_bool(output.get("should_send"))
    if should_send is None:
        should_send = parse_bool(output.get("shouldSend"))
    if should_send is None:
        should_send = parse_bool(output.get("send"))
    if handoff is None:
        handoff = False
    if should_send is None:
        should_send = not handoff

    return {
        "reply": pick_text(output.get("reply")),
        "reply_source": "choices[0].message.content",
        "should_send": should_send,
        "handoff": handoff,
        "reason": pick_text(output.get("reason")) or pick_text(output.get("handoff_reason")),
        "risk": pick_risk(output.get("risk")),
        "error": "",
        "openclaw_output": output,
        "raw_object": envelope,
    }


def evaluate_fail_closed(response_ok: bool, normalized: dict[str, Any]) -> dict[str, Any]:
    reply_text = pick_text(normalized.get("reply"))
    handoff = parse_bool(normalized.get("handoff")) is True
    should_send_value = parse_bool(normalized.get("should_send"))
    should_send = True if should_send_value is None else should_send_value
    error_text = pick_text(normalized.get("error"))

    if not response_ok:
        return {"send": False, "reason": "http_error"}
    if error_text:
        return {"send": False, "reason": "runtime_error"}
    if handoff:
        return {"send": False, "reason": "handoff_true"}
    if should_send is False:
        return {"send": False, "reason": "should_send_false"}
    if not reply_text:
        return {"send": False, "reason": "empty_reply"}
    has_external_contact, _ = detect_external_contact(reply_text)
    if has_external_contact:
        return {"send": False, "reason": "reply_external_contact"}
    abnormal, _ = detect_abnormal_text(reply_text)
    if abnormal:
        return {"send": False, "reason": "reply_abnormal"}
    return {"send": True, "reason": "ready"}


def run_self_check() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    args = argparse.Namespace(
        url="http://host.docker.internal:18789/v1/chat/completions",
        token="abc",
        model="openclaw/default",
        cid="c",
        toid="u",
        message="还在吗",
        timeout=5.0,
        self_check=True,
    )
    req = build_openai_chat_request(args)
    checks.append(
        {
            "name": "openai_chat_request_shape",
            "ok": (
                req["payload"].get("model") == "openclaw/default"
                and isinstance(req["payload"].get("messages"), list)
                and len(req["payload"]["messages"]) == 2
                and req["headers"].get("Authorization") == "Bearer abc"
            ),
        }
    )

    choices_response = {
        "choices": [
            {
                "message": {
                    "content": "{\"reply\":\"在的，喜欢可拍\",\"should_send\":true,\"handoff\":false,\"reason\":\"normal\",\"risk\":\"low\"}"
                }
            }
        ]
    }
    normalized_choices = normalize_openclaw_response(choices_response)
    checks.append(
        {
            "name": "choices_content_json_parse",
            "ok": (
                normalized_choices.get("reply") == "在的，喜欢可拍"
                and normalized_choices.get("should_send") is True
                and normalized_choices.get("handoff") is False
                and normalized_choices.get("risk") == "low"
            ),
        }
    )

    fenced_response = {
        "choices": [
            {
                "message": {
                    "content": "```json\n{\"reply\":\"在的\",\"should_send\":true,\"handoff\":false,\"reason\":\"mock\",\"risk\":\"medium\"}\n```"
                }
            }
        ]
    }
    normalized_fenced = normalize_openclaw_response(fenced_response)
    checks.append(
        {
            "name": "choices_fenced_json_parse",
            "ok": (
                normalized_fenced.get("reply") == "在的"
                and normalized_fenced.get("should_send") is True
                and normalized_fenced.get("handoff") is False
            ),
        }
    )

    non_json = normalize_openclaw_response({"choices": [{"message": {"content": "在的，喜欢可拍"}}]})
    non_json_decision = evaluate_fail_closed(True, non_json)
    checks.append({"name": "non_json_fail_closed", "ok": non_json_decision["send"] is False})

    html_normalized = normalize_openclaw_response({"choices": [{"message": {"content": "<html>forbidden</html>"}}]})
    html_decision = evaluate_fail_closed(True, html_normalized)
    checks.append({"name": "html_fail_closed", "ok": html_decision["send"] is False})

    abnormal_normalized = normalize_openclaw_response(
        {"choices": [{"message": {"content": "{\"reply\":\"undefined\",\"should_send\":true,\"handoff\":false,\"reason\":\"x\"}"}}]}
    )
    abnormal_decision = evaluate_fail_closed(True, abnormal_normalized)
    checks.append({"name": "abnormal_fail_closed", "ok": abnormal_decision["send"] is False})

    return {"ok": all(check["ok"] for check in checks), "checks": checks}


def main() -> int:
    args = parse_args()
    if args.self_check:
        report = run_self_check()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    request_config = build_openai_chat_request(args)
    url = to_optional_string(request_config.get("url"))
    token = to_optional_string(args.token)
    if not url:
        print(
            json.dumps(
                {"ok": False, "error": "missing OpenClaw URL; set OPENCLAW_CHAT_COMPLETIONS_URL or pass --url"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if not token:
        print(
            json.dumps(
                {"ok": False, "error": "missing OpenClaw token; set OPENCLAW_GATEWAY_TOKEN or pass --token"},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2

    response = http_post_json(
        url=url,
        headers=request_config["headers"],
        payload=request_config["payload"],
        timeout=args.timeout,
    )
    normalized = normalize_openclaw_response(response.get("body"))
    fail_closed_decision = evaluate_fail_closed(bool(response.get("ok")), normalized)

    report = {
        "ok": bool(response.get("ok")) and response.get("http_status") == 200 and fail_closed_decision["send"],
        "url": url,
        "timeout": args.timeout,
        "send_called": False,
        "request_headers": request_config["headers"],
        "request_payload": request_config["payload"],
        "response_ok": response.get("ok", False),
        "http_status": response.get("http_status"),
        "elapsed_ms": response.get("elapsed_ms"),
        "response_body": response.get("body"),
        "normalized": normalized,
        "fail_closed_decision": fail_closed_decision,
    }
    if not response.get("ok"):
        report["transport_error"] = response.get("error", "")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
