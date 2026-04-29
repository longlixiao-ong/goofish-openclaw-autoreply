"""Probe OpenClaw runtime contracts without sending Goofish messages.

Supports:
- custom_reply mode (/reply custom JSON contract)
- openai_chat mode (/v1/chat/completions with Bearer token)
"""

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
    parser = argparse.ArgumentParser(description="Test OpenClaw runtime contract without calling /send")
    parser.add_argument(
        "--mode",
        choices=["custom_reply", "openai_chat"],
        default=(os.environ.get("OPENCLAW_RUNTIME_MODE", "custom_reply").strip() or "custom_reply"),
        help="Runtime mode (default: env OPENCLAW_RUNTIME_MODE or custom_reply)",
    )
    parser.add_argument("--url", default="", help="Override runtime URL")
    parser.add_argument("--token", default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip(), help="Gateway token")
    parser.add_argument("--model", default=os.environ.get("OPENCLAW_MODEL", "openclaw/default").strip() or "openclaw/default")
    parser.add_argument("--cid", default="test-cid", help="Conversation id")
    parser.add_argument("--toid", default="test-toid", help="Buyer id")
    parser.add_argument("--message", default="还在吗", help="Buyer message for test")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout seconds")
    parser.add_argument("--self-check", action="store_true", help="Run offline checks only, no HTTP calls")
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


def normalize_openclaw_response(raw: Any, mode: str) -> dict[str, Any]:
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

    # Parse chat content JSON when model returns JSON string in content.
    content_object = parse_object(reply) if reply_source == "choices[0].message.content" else None
    if content_object is not None:
        objects.insert(0, content_object)
        if pick_text(content_object.get("reply")):
            reply = pick_text(content_object.get("reply"))
            reply_source = "choices[0].message.content.reply"

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
            for value in [
                ((obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) else obj.get("error")),
                obj.get("err"),
                obj.get("exception"),
                obj.get("message"),
            ]:
                text = pick_text(value)
                if text and ("status code" in text.lower() or "unauthorized" in text.lower() or "forbidden" in text.lower() or value == obj.get("err") or value == obj.get("exception") or isinstance(obj.get("error"), (str, dict))):
                    error = text
                    break

    if handoff is None:
        handoff = False
    if should_send is None:
        should_send = not handoff

    maybe_html = reply.strip().lower()
    if mode == "openai_chat" and not error and (maybe_html.startswith("<!doctype html") or maybe_html.startswith("<html")):
        error = "openclaw_html_response"

    return {
        "reply": reply,
        "reply_source": reply_source,
        "should_send": should_send,
        "handoff": handoff,
        "reason": reason,
        "error": error,
        "raw_object": response,
    }


def build_common_context(args: argparse.Namespace) -> dict[str, Any]:
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


def build_custom_reply_request(args: argparse.Namespace) -> dict[str, Any]:
    context = build_common_context(args)
    return {
        "url": args.url.strip() or os.environ.get("OPENCLAW_REPLY_URL", "").strip(),
        "headers": {"Content-Type": "application/json", "Accept": "application/json"},
        "payload": {
            "runtime_mode": "custom_reply",
            "cid": context["cid"],
            "toid": context["toid"],
            "message": context["message"],
            "risk": context["risk"],
            "risk_reason": context["risk_reason"],
            "route_reason": context["route_reason"],
            "handoff": context["handoff"],
            "handoff_reason": context["handoff_reason"],
            "dry_run": context["dry_run"],
            "item_context": context["item_context"],
            "item_context_status": context["item_context_status"],
            "item_context_reason": context["item_context_reason"],
            "customer_service_policy": context["customer_service_policy"],
        },
    }


def build_openai_chat_request(args: argparse.Namespace) -> dict[str, Any]:
    context = build_common_context(args)
    url = args.url.strip() or os.environ.get(
        "OPENCLAW_CHAT_COMPLETIONS_URL",
        "http://host.docker.internal:18789/v1/chat/completions",
    ).strip()
    token = args.token.strip()
    system_text = (
        "你是闲鱼客服助手。必须只输出JSON对象，字段至少包含 reply、should_send、handoff、reason。"
        "禁止输出思考过程、Markdown和<think>。若需要人工介入，设置 handoff=true 且 should_send=false。"
    )
    user_text = "\n".join(
        [
            f"cid={context['cid']}",
            f"send_user_id={context['toid']}",
            f"risk={context['risk']}",
            f"risk_reason={context['risk_reason']}",
            f"route_reason={context['route_reason']}",
            f"buyer_message={context['message']}",
            f"item_context_status={context['item_context_status']}",
            f"item_context_reason={context['item_context_reason']}",
            f"customer_service_policy={json.dumps(context['customer_service_policy'], ensure_ascii=False)}",
            f"item_context={json.dumps(context['item_context'], ensure_ascii=False)}",
        ]
    )
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    return {
        "url": url,
        "headers": headers,
        "payload": {
            "model": args.model,
            "messages": [
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_text},
            ],
            "user": context["cid"] or context["toid"],
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
            return {
                "ok": True,
                "http_status": int(response.status),
                "elapsed_ms": elapsed_ms,
                "body": parse_json_or_text(raw_body),
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
    return {"send": True, "reason": "ready"}


def run_self_check() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    ns_custom = argparse.Namespace(
        mode="custom_reply",
        url="http://openclaw:18789/reply",
        token="",
        model="openclaw/default",
        cid="c",
        toid="u",
        message="还在吗",
        timeout=5.0,
        self_check=True,
    )
    custom_req = build_custom_reply_request(ns_custom)
    checks.append(
        {
            "name": "custom_reply_request_shape",
            "ok": (
                isinstance(custom_req["payload"], dict)
                and custom_req["payload"].get("runtime_mode") == "custom_reply"
                and "item_context" in custom_req["payload"]
                and "customer_service_policy" in custom_req["payload"]
            ),
        }
    )

    ns_chat = argparse.Namespace(
        mode="openai_chat",
        url="http://host.docker.internal:18789/v1/chat/completions",
        token="abc",
        model="openclaw/default",
        cid="c",
        toid="u",
        message="还在吗",
        timeout=5.0,
        self_check=True,
    )
    chat_req = build_openai_chat_request(ns_chat)
    checks.append(
        {
            "name": "openai_chat_request_shape",
            "ok": (
                chat_req["payload"].get("model") == "openclaw/default"
                and isinstance(chat_req["payload"].get("messages"), list)
                and len(chat_req["payload"]["messages"]) == 2
                and chat_req["headers"].get("Authorization") == "Bearer abc"
            ),
        }
    )

    choices_response = {
        "choices": [
            {
                "message": {
                    "content": "{\"reply\":\"在的，喜欢可拍\",\"should_send\":true,\"handoff\":false,\"reason\":\"normal\"}"
                }
            }
        ]
    }
    normalized_choices = normalize_openclaw_response(choices_response, mode="openai_chat")
    checks.append(
        {
            "name": "choices_content_parse",
            "ok": (
                normalized_choices.get("reply") == "在的，喜欢可拍"
                and normalized_choices.get("should_send") is True
                and normalized_choices.get("handoff") is False
            ),
        }
    )

    fenced_response = {
        "choices": [
            {
                "message": {
                    "content": "```json\n{\"reply\":\"在的\",\"should_send\":true,\"handoff\":false,\"reason\":\"mock\"}\n```"
                }
            }
        ]
    }
    normalized_fenced = normalize_openclaw_response(fenced_response, mode="openai_chat")
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

    unauthorized_normalized = normalize_openclaw_response({"error": {"message": "401 Unauthorized"}}, mode="openai_chat")
    unauthorized_decision = evaluate_fail_closed(False, unauthorized_normalized)
    checks.append({"name": "unauthorized_fail_closed", "ok": unauthorized_decision["send"] is False})

    html_normalized = normalize_openclaw_response({"choices": [{"message": {"content": "<html>forbidden</html>"}}]}, mode="openai_chat")
    html_decision = evaluate_fail_closed(True, html_normalized)
    checks.append({"name": "html_fail_closed", "ok": html_decision["send"] is False})

    error_normalized = normalize_openclaw_response({"error": {"message": "internal server error"}}, mode="custom_reply")
    error_decision = evaluate_fail_closed(True, error_normalized)
    checks.append({"name": "error_fail_closed", "ok": error_decision["send"] is False})

    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def main() -> int:
    args = parse_args()

    if args.self_check:
        report = run_self_check()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 1

    if args.mode == "openai_chat":
        request_config = build_openai_chat_request(args)
    else:
        request_config = build_custom_reply_request(args)

    url = pick_text(request_config.get("url"))
    if not url:
        missing = "OPENCLAW_CHAT_COMPLETIONS_URL" if args.mode == "openai_chat" else "OPENCLAW_REPLY_URL"
        print(
            json.dumps(
                {"ok": False, "error": f"missing OpenClaw URL; set {missing} or pass --url"},
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
    normalized = normalize_openclaw_response(response.get("body"), mode=args.mode)
    fail_closed_decision = evaluate_fail_closed(bool(response.get("ok")), normalized)

    report = {
        "ok": bool(response.get("ok")) and fail_closed_decision["send"],
        "mode": args.mode,
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
