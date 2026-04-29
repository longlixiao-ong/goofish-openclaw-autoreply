"""Offline checks for inbound webhook normalization in n8n workflow."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


WORKFLOW_PATH = Path("n8n/workflows/goofish-inbound.example.json")
IF_DUP_NODE = "IF 重复消息"
IF_DUP_TRUE_OUTPUT_INDEX = 1
IF_DUP_FALSE_OUTPUT_INDEX = 0
IF_DUP_TRUE_TARGET = "重复消息结束"
IF_DUP_FALSE_TARGET = "保留原始入站消息"


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
    text = strip_markdown_fences(raw)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def strip_markdown_fences(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    json_prefix = re.match(r"^json\s*\n([\s\S]*)$", text, re.IGNORECASE)
    if json_prefix:
        return json_prefix.group(1).strip()
    full_fence = re.match(r"^```(?:json)?\s*([\s\S]*?)\s*```$", text, re.IGNORECASE)
    if full_fence:
        return full_fence.group(1).strip()
    first_fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if first_fence:
        return first_fence.group(1).strip()
    return text


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


def dedup_process(
    payload: dict[str, Any],
    dedup_store: dict[str, Any],
    now_ms: int = 0,
    max_dedup_keys: int = 500,
) -> tuple[dict[str, Any], dict[str, Any]]:
    cid = str(payload.get("cid") or "").strip()
    send_user_id = str(payload.get("send_user_id") or "").strip()
    send_message = str(payload.get("send_message") or "").strip()
    dedup_key = f"{cid}::{send_user_id}::{send_message}"
    is_dry_run = parse_truthy_dry_run(payload.get("dry_run"))

    if is_dry_run:
        return (
            {
                **payload,
                "dry_run": True,
                "dedup_key": dedup_key,
                "is_duplicate": False,
                "dedup_skipped": True,
                "dedup_skip_reason": "dry_run",
            },
            dedup_store,
        )

    order = dedup_store.setdefault("order", [])
    dedup_map = dedup_store.setdefault("map", {})
    is_duplicate = bool(dedup_map.get(dedup_key))

    if not is_duplicate:
        dedup_map[dedup_key] = now_ms
        order.append(dedup_key)
        while len(order) > max_dedup_keys:
            oldest = order.pop(0)
            dedup_map.pop(oldest, None)

    if is_duplicate:
        return (
            {
                **payload,
                "dedup_key": dedup_key,
                "is_duplicate": True,
                "ok": True,
                "send": False,
                "reason": "duplicate_message",
                "message": "duplicate message skipped",
                "cid": cid,
                "send_user_id": send_user_id,
            },
            dedup_store,
        )

    return (
        {
            **payload,
            "dedup_key": dedup_key,
            "is_duplicate": False,
        },
        dedup_store,
    )


def evaluate_duplicate_if_condition(payload: dict[str, Any]) -> bool:
    is_duplicate = payload.get("is_duplicate") is True
    is_dry_run = parse_truthy_dry_run(payload.get("dry_run"))
    return is_duplicate and not is_dry_run


def simulate_if_duplicate_next_node(
    payload: dict[str, Any],
    workflow: dict[str, Any],
) -> str:
    connections = workflow.get("connections", {})
    outputs = connections.get(IF_DUP_NODE, {}).get("main", [])
    if not isinstance(outputs, list) or len(outputs) <= max(IF_DUP_TRUE_OUTPUT_INDEX, IF_DUP_FALSE_OUTPUT_INDEX):
        return ""

    condition_true = evaluate_duplicate_if_condition(payload)
    output_index = IF_DUP_TRUE_OUTPUT_INDEX if condition_true else IF_DUP_FALSE_OUTPUT_INDEX
    branch = outputs[output_index] if output_index < len(outputs) else []
    if not branch or not isinstance(branch, list):
        return ""
    first = branch[0] if branch else {}
    if not isinstance(first, dict):
        return ""
    return str(first.get("node", ""))


def should_route_duplicate_branch(payload: dict[str, Any]) -> bool:
    return evaluate_duplicate_if_condition(payload)


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
    if normalize_next != "去重":
        issues.append(f"入站归一化 is not wired to 去重 (actual={normalize_next})")

    dedup_next = (
        conns.get("去重", {})
        .get("main", [[{}]])[0][0]
        .get("node", "")
    )
    if dedup_next != "IF 重复消息":
        issues.append(f"去重 is not wired to IF 重复消息 (actual={dedup_next})")

    if "入站归一化" in nodes:
        code = str(nodes["入站归一化"].get("parameters", {}).get("jsCode", ""))
        for snippet in ["root.body", "dry_run", "original_payload", "webhook_meta"]:
            if snippet not in code:
                issues.append(f"入站归一化 jsCode missing snippet: {snippet}")

    if "去重" not in nodes:
        issues.append("missing node: 去重")
    else:
        dedup_code = str(nodes["去重"].get("parameters", {}).get("jsCode", ""))
        for snippet in ["isTruthy", "dedup_skipped", "dedup_skip_reason: 'dry_run'"]:
            if snippet not in dedup_code:
                issues.append(f"去重 jsCode missing snippet: {snippet}")

    if IF_DUP_NODE not in nodes:
        issues.append(f"missing node: {IF_DUP_NODE}")
    else:
        bool_conditions = (
            nodes[IF_DUP_NODE]
            .get("parameters", {})
            .get("conditions", {})
            .get("boolean", [])
        )
        if len(bool_conditions) != 1:
            issues.append(f"{IF_DUP_NODE} should use single-expression condition")
        merged = json.dumps(bool_conditions, ensure_ascii=False)
        if "is_duplicate" not in merged:
            issues.append(f"{IF_DUP_NODE} missing is_duplicate condition")
        if "dry_run" not in merged:
            issues.append(f"{IF_DUP_NODE} missing dry_run guard condition")

        outputs = conns.get(IF_DUP_NODE, {}).get("main", [])
        if not isinstance(outputs, list) or len(outputs) < 2:
            issues.append(f"{IF_DUP_NODE} must have 2 outputs in connections.main")
        else:
            true_branch = outputs[IF_DUP_TRUE_OUTPUT_INDEX] if len(outputs) > IF_DUP_TRUE_OUTPUT_INDEX else []
            false_branch = outputs[IF_DUP_FALSE_OUTPUT_INDEX] if len(outputs) > IF_DUP_FALSE_OUTPUT_INDEX else []

            true_next = true_branch[0].get("node", "") if true_branch and isinstance(true_branch[0], dict) else ""
            false_next = false_branch[0].get("node", "") if false_branch and isinstance(false_branch[0], dict) else ""

            if true_next != IF_DUP_TRUE_TARGET:
                issues.append(
                    f"{IF_DUP_NODE} true output(index={IF_DUP_TRUE_OUTPUT_INDEX}) should go {IF_DUP_TRUE_TARGET} (actual={true_next})"
                )
            if false_next != IF_DUP_FALSE_TARGET:
                issues.append(
                    f"{IF_DUP_NODE} false output(index={IF_DUP_FALSE_OUTPUT_INDEX}) should go {IF_DUP_FALSE_TARGET} (actual={false_next})"
                )

    restore_next = (
        conns.get("恢复原始入站消息", {})
        .get("main", [[{}]])[0][0]
        .get("node", "")
    )
    if restore_next and restore_next != "会话冷却":
        issues.append(f"恢复原始入站消息 should continue to 会话冷却 after dedup (actual={restore_next})")

    normalize_reply_code = str(nodes.get("归一化 OpenClaw 回复", {}).get("parameters", {}).get("jsCode", ""))
    if normalize_reply_code:
        for snippet in ["stripMarkdownFences", "jsonPrefix", "fullFence", "firstFence", "choices[0].message.content.object"]:
            if snippet not in normalize_reply_code:
                issues.append(f"归一化 OpenClaw 回复 missing fenced-json snippet: {snippet}")

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


def run_dedup_guard_cases() -> list[str]:
    issues: list[str] = []
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))

    for dry_value in [True, "true", 1, "1"]:
        store = {"order": ["existing"], "map": {"existing": 123}}
        before = json.loads(json.dumps(store, ensure_ascii=False))
        out, after = dedup_process(
            {
                "cid": "dry-cid",
                "send_user_id": "dry-user",
                "send_message": "dry-message",
                "dry_run": dry_value,
            },
            store,
            now_ms=999,
        )
        if out.get("is_duplicate") is not False:
            issues.append(f"dedup dry-run ({dry_value!r}) should force is_duplicate=false")
        if out.get("dedup_skipped") is not True or out.get("dedup_skip_reason") != "dry_run":
            issues.append(f"dedup dry-run ({dry_value!r}) should set dedup_skipped flags")
        if after != before:
            issues.append(f"dedup dry-run ({dry_value!r}) should not mutate dedup store")
        if should_route_duplicate_branch(out):
            issues.append(f"dedup dry-run ({dry_value!r}) should not route to duplicate branch")
        dry_next = simulate_if_duplicate_next_node(out, workflow)
        if dry_next != IF_DUP_FALSE_TARGET:
            issues.append(
                f"dedup dry-run ({dry_value!r}) next node should be {IF_DUP_FALSE_TARGET} (actual={dry_next})"
            )

    store = {"order": [], "map": {}}
    first, store = dedup_process(
        {
            "cid": "c",
            "send_user_id": "u",
            "send_message": "m",
            "dry_run": False,
        },
        store,
        now_ms=111,
    )
    second, store = dedup_process(
        {
            "cid": "c",
            "send_user_id": "u",
            "send_message": "m",
            "dry_run": False,
        },
        store,
        now_ms=222,
    )

    if first.get("is_duplicate") is not False:
        issues.append("dedup non-dry-run first message should not be duplicate")
    if second.get("is_duplicate") is not True:
        issues.append("dedup non-dry-run repeated message should be duplicate")
    if not should_route_duplicate_branch(second):
        issues.append("IF duplicate branch should route only for real duplicate non-dry-run")
    duplicate_next = simulate_if_duplicate_next_node(second, workflow)
    if duplicate_next != IF_DUP_TRUE_TARGET:
        issues.append(
            f"dedup non-dry-run duplicate next node should be {IF_DUP_TRUE_TARGET} (actual={duplicate_next})"
        )

    guarded = {
        "is_duplicate": True,
        "dry_run": "1",
    }
    if should_route_duplicate_branch(guarded):
        issues.append("IF duplicate branch must block dry_run=true/'1' even when is_duplicate=true")
    guarded_next = simulate_if_duplicate_next_node(guarded, workflow)
    if guarded_next == IF_DUP_TRUE_TARGET:
        issues.append("dry_run=true must never route to 重复消息结束")

    return issues


def run_fenced_json_cases() -> list[str]:
    issues: list[str] = []

    candidates = [
        "{\"reply\":\"在的\",\"should_send\":true,\"handoff\":false,\"reason\":\"test\"}",
        "json\n{\"reply\":\"在的\",\"should_send\":true,\"handoff\":false,\"reason\":\"test\"}\n",
        "```json\n{\"reply\":\"在的\",\"should_send\":true,\"handoff\":false,\"reason\":\"test\"}\n```",
        "```\n{\"reply\":\"在的\",\"should_send\":true,\"handoff\":false,\"reason\":\"test\"}\n```",
        "模型输出如下：\n```json\n{\"reply\":\"在的\",\"should_send\":true,\"handoff\":false,\"reason\":\"test\"}\n```\n请参考",
    ]

    for idx, candidate in enumerate(candidates, start=1):
        parsed = parse_json_object(candidate)
        if not isinstance(parsed, dict):
            issues.append(f"fenced-json case{idx} should parse into object")
            continue
        if parsed.get("reply") != "在的":
            issues.append(f"fenced-json case{idx} reply parse mismatch")
        if parsed.get("should_send") is not True:
            issues.append(f"fenced-json case{idx} should_send parse mismatch")
        if parsed.get("handoff") is not False:
            issues.append(f"fenced-json case{idx} handoff parse mismatch")
        if parsed.get("reason") != "test":
            issues.append(f"fenced-json case{idx} reason parse mismatch")

    return issues


def main() -> int:
    issues: list[str] = []
    issues.extend(check_workflow_structure())
    issues.extend(run_normalization_cases())
    issues.extend(run_dedup_guard_cases())
    issues.extend(run_fenced_json_cases())

    report = {
        "ok": len(issues) == 0,
        "workflow_path": str(WORKFLOW_PATH),
        "issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
