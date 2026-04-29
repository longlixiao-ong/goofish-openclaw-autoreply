"""Offline checks for inbound webhook normalization in n8n workflow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WORKFLOW_PATH = Path("n8n/workflows/goofish-inbound.example.json")


def is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def pick_value(*values: Any) -> Any:
    for value in values:
        if is_meaningful(value):
            return value
    return None


def parse_json_object(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def parse_truthy_dry_run(value: Any) -> bool:
    if value is True or value == 1:
        return True
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"true", "1"}
    return False


def to_optional_int(value: Any) -> int | None:
    if not is_meaningful(value):
        return None
    try:
        num = int(float(value))
    except (TypeError, ValueError):
        return None
    return num


def to_optional_string(value: Any) -> str:
    if not is_meaningful(value):
        return ""
    return str(value).strip()


def normalize_payload(root: dict[str, Any]) -> dict[str, Any]:
    payload = root
    payload_source = "root"
    body = root.get("body")
    if isinstance(body, dict):
        payload = body
        payload_source = "body_object"
    elif isinstance(body, str):
        parsed = parse_json_object(body)
        if parsed is not None:
            payload = parsed
            payload_source = "body_json_string"
        else:
            payload_source = "body_string_unparsed"

    cid = to_optional_string(pick_value(payload.get("cid"), root.get("cid")))
    send_user_id = to_optional_string(
        pick_value(
            payload.get("send_user_id"),
            payload.get("sendUserId"),
            root.get("send_user_id"),
            root.get("sendUserId"),
        )
    )
    send_message = to_optional_string(
        pick_value(
            payload.get("send_message"),
            payload.get("sendMessage"),
            root.get("send_message"),
            root.get("sendMessage"),
        )
    )

    content_type_raw = pick_value(
        payload.get("content_type"),
        payload.get("contentType"),
        root.get("content_type"),
        root.get("contentType"),
    )
    content_type = to_optional_int(content_type_raw)
    if content_type is None:
        content_type = to_optional_string(content_type_raw)

    dry_run_raw = pick_value(
        payload.get("dry_run"),
        payload.get("dryRun"),
        root.get("dry_run"),
        root.get("dryRun"),
    )
    dry_run = parse_truthy_dry_run(dry_run_raw)

    cooldown_seconds = to_optional_int(
        pick_value(
            payload.get("cooldown_seconds"),
            payload.get("cooldownSeconds"),
            root.get("cooldown_seconds"),
            root.get("cooldownSeconds"),
        )
    )
    max_reply_chars = to_optional_int(
        pick_value(
            payload.get("max_reply_chars"),
            payload.get("maxReplyChars"),
            root.get("max_reply_chars"),
            root.get("maxReplyChars"),
        )
    )
    item_id = to_optional_string(
        pick_value(payload.get("item_id"), payload.get("itemId"), root.get("item_id"), root.get("itemId"))
    )

    return {
        "cid": cid,
        "send_user_id": send_user_id,
        "send_message": send_message,
        "content_type": content_type,
        "dry_run": dry_run,
        "cooldown_seconds": cooldown_seconds,
        "max_reply_chars": max_reply_chars,
        "item_id": item_id,
        "original_payload": payload,
        "webhook_meta": {
            "payload_source": payload_source,
            "headers": root.get("headers") if isinstance(root.get("headers"), dict) else {},
            "query": root.get("query") if isinstance(root.get("query"), dict) else {},
            "params": root.get("params") if isinstance(root.get("params"), dict) else {},
        },
    }


def check_workflow_structure() -> list[str]:
    issues: list[str] = []
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    nodes = {node["name"]: node for node in workflow.get("nodes", [])}
    conns = workflow.get("connections", {})

    if "入站归一化" not in nodes:
        issues.append("missing node: 入站归一化")

    webhook_next = (
        conns.get("Webhook", {})
        .get("main", [[{}]])[0][0]
        .get("node", "")
    )
    if webhook_next != "入站归一化":
        issues.append(f"Webhook is not wired to 入站归一化 (actual={webhook_next})")

    normalize_next = (
        conns.get("入站归一化", {})
        .get("main", [[{}]])[0][0]
        .get("node", "")
    )
    if normalize_next != "保留原始入站消息":
        issues.append(f"入站归一化 is not wired to 保留原始入站消息 (actual={normalize_next})")

    if "入站归一化" in nodes:
        code = str(nodes["入站归一化"].get("parameters", {}).get("jsCode", ""))
        for snippet in ["root.body", "dry_run", "original_payload", "webhook_meta"]:
            if snippet not in code:
                issues.append(f"入站归一化 jsCode missing snippet: {snippet}")

    return issues


def run_normalization_cases() -> list[str]:
    issues: list[str] = []

    case1 = normalize_payload(
        {
            "cid": "c1",
            "send_user_id": "u1",
            "send_message": "m1",
            "dry_run": True,
        }
    )
    if not (case1["cid"] == "c1" and case1["send_user_id"] == "u1" and case1["send_message"] == "m1"):
        issues.append("case1(top-level): core fields mismatch")

    case2 = normalize_payload(
        {
            "headers": {"h": "v"},
            "query": {"q": "1"},
            "params": {"id": "p"},
            "body": {
                "cid": "c2",
                "send_user_id": "u2",
                "send_message": "m2",
                "dry_run": "true",
            },
            "cid": "root-c2",
        }
    )
    if not (
        case2["cid"] == "c2"
        and case2["send_user_id"] == "u2"
        and case2["send_message"] == "m2"
        and case2["dry_run"] is True
        and case2["webhook_meta"]["payload_source"] == "body_object"
    ):
        issues.append("case2(body object): normalize failed")

    case3 = normalize_payload(
        {
            "body": json.dumps(
                {
                    "cid": "c3",
                    "send_user_id": "u3",
                    "send_message": "m3",
                    "dry_run": 1,
                },
                ensure_ascii=False,
            )
        }
    )
    if not (
        case3["cid"] == "c3"
        and case3["send_user_id"] == "u3"
        and case3["send_message"] == "m3"
        and case3["dry_run"] is True
        and case3["webhook_meta"]["payload_source"] == "body_json_string"
    ):
        issues.append("case3(body json string): normalize failed")

    case4 = normalize_payload({"body": {"cid": "", "send_user_id": "", "send_message": "", "dry_run": "1"}, "cid": "c4", "send_user_id": "u4", "send_message": "m4"})
    if not (
        case4["cid"] == "c4"
        and case4["send_user_id"] == "u4"
        and case4["send_message"] == "m4"
        and case4["dry_run"] is True
    ):
        issues.append("case4(dry_run true and empty wrapper fields fallback): normalize failed")

    return issues


def main() -> int:
    issues: list[str] = []
    issues.extend(check_workflow_structure())
    issues.extend(run_normalization_cases())

    report = {
        "ok": len(issues) == 0,
        "workflow_path": str(WORKFLOW_PATH),
        "issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
