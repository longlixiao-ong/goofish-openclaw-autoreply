"""Offline checks for production inbound workflow topology and routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


WORKFLOW_PATH = Path("n8n/workflows/goofish-inbound.example.json")

IF_NODE = "IF 应发送"
DECIDE_NODE = "goofish-bridge /autoreply/decide"
SEND_NODE = "goofish-bridge /send"
NOT_SEND_NODE = "不发送结束"

# Calibrated to exported workflow runtime behavior in this repo.
IF_TRUE_OUTPUT_INDEX = 1
IF_FALSE_OUTPUT_INDEX = 0


def parse_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        return lowered in {"true", "1", "yes", "y"}
    return False


def evaluate_send_if(payload: dict[str, Any]) -> bool:
    return parse_truthy(payload.get("send")) and not parse_truthy(payload.get("dry_run"))


def simulate_if_next_node(payload: dict[str, Any], workflow: dict[str, Any]) -> str:
    outputs = workflow.get("connections", {}).get(IF_NODE, {}).get("main", [])
    if not isinstance(outputs, list) or len(outputs) <= max(IF_TRUE_OUTPUT_INDEX, IF_FALSE_OUTPUT_INDEX):
        return ""
    output_index = IF_TRUE_OUTPUT_INDEX if evaluate_send_if(payload) else IF_FALSE_OUTPUT_INDEX
    branch = outputs[output_index]
    if not isinstance(branch, list) or not branch:
        return ""
    target = branch[0]
    if not isinstance(target, dict):
        return ""
    return str(target.get("node", ""))


def get_node(workflow: dict[str, Any], name: str) -> dict[str, Any] | None:
    for node in workflow.get("nodes", []):
        if node.get("name") == name:
            return node
    return None


def check_structure(workflow: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    conns = workflow.get("connections", {})

    webhook_next = conns.get("Webhook", {}).get("main", [[{}]])[0][0].get("node", "")
    if webhook_next != DECIDE_NODE:
        issues.append(f"Webhook should connect to {DECIDE_NODE} (actual={webhook_next})")

    decide_next = conns.get(DECIDE_NODE, {}).get("main", [[{}]])[0][0].get("node", "")
    if decide_next != IF_NODE:
        issues.append(f"{DECIDE_NODE} should connect to {IF_NODE} (actual={decide_next})")

    if_outputs = conns.get(IF_NODE, {}).get("main", [])
    if not isinstance(if_outputs, list) or len(if_outputs) < 2:
        issues.append(f"{IF_NODE} should have 2 outputs")
    else:
        true_branch = if_outputs[IF_TRUE_OUTPUT_INDEX] if len(if_outputs) > IF_TRUE_OUTPUT_INDEX else []
        false_branch = if_outputs[IF_FALSE_OUTPUT_INDEX] if len(if_outputs) > IF_FALSE_OUTPUT_INDEX else []
        true_next = true_branch[0].get("node", "") if true_branch and isinstance(true_branch[0], dict) else ""
        false_next = false_branch[0].get("node", "") if false_branch and isinstance(false_branch[0], dict) else ""
        if true_next != SEND_NODE:
            issues.append(
                f"{IF_NODE} true output(index={IF_TRUE_OUTPUT_INDEX}) should go {SEND_NODE} (actual={true_next})"
            )
        if false_next != NOT_SEND_NODE:
            issues.append(
                f"{IF_NODE} false output(index={IF_FALSE_OUTPUT_INDEX}) should go {NOT_SEND_NODE} (actual={false_next})"
            )

    decide_node = get_node(workflow, DECIDE_NODE)
    if decide_node is None:
        issues.append(f"missing node: {DECIDE_NODE}")
    else:
        params = decide_node.get("parameters", {})
        if params.get("url") != "http://goofish-bridge:8787/autoreply/decide":
            issues.append("decide node URL mismatch")
        body = str(params.get("jsonBody", ""))
        if "{{$json}}" not in body:
            issues.append("decide node should forward raw webhook payload as JSON body")
        headers = json.dumps(params.get("headerParameters", {}), ensure_ascii=False)
        if "X-Bridge-Token" not in headers:
            issues.append("decide node missing X-Bridge-Token header")

    send_node = get_node(workflow, SEND_NODE)
    if send_node is None:
        issues.append(f"missing node: {SEND_NODE}")
    else:
        params = send_node.get("parameters", {})
        if params.get("url") != "http://goofish-bridge:8787/send":
            issues.append("send node URL mismatch")
        headers = json.dumps(params.get("headerParameters", {}), ensure_ascii=False)
        if "X-Bridge-Token" not in headers:
            issues.append("send node missing X-Bridge-Token header")

    # n8n must be light orchestration only.
    disallowed = {"去重", "会话冷却", "转人工门控分类", "归一化 OpenClaw 回复", "OpenClaw Chat Completions 请求"}
    existing_names = {str(node.get("name", "")) for node in workflow.get("nodes", [])}
    leaked = sorted(disallowed.intersection(existing_names))
    if leaked:
        issues.append(f"workflow should not keep heavy business nodes: {', '.join(leaked)}")

    return issues


def run_routing_cases(workflow: dict[str, Any]) -> list[str]:
    issues: list[str] = []

    dry_cases = [
        {"send": True, "dry_run": True},
        {"send": True, "dry_run": "true"},
        {"send": True, "dry_run": 1},
        {"send": True, "dry_run": "1"},
    ]

    for idx, payload in enumerate(dry_cases, start=1):
        next_node = simulate_if_next_node(payload, workflow)
        if next_node != NOT_SEND_NODE:
            issues.append(f"dry-run case{idx} should route to {NOT_SEND_NODE} (actual={next_node})")

    duplicate_case = {"send": False, "dry_run": False}
    if simulate_if_next_node(duplicate_case, workflow) != NOT_SEND_NODE:
        issues.append("send=false case should route to 不发送结束")

    send_case = {"send": True, "dry_run": False}
    if simulate_if_next_node(send_case, workflow) != SEND_NODE:
        issues.append("send=true and dry_run=false case should route to goofish-bridge /send")

    return issues


def main() -> int:
    workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    issues: list[str] = []
    issues.extend(check_structure(workflow))
    issues.extend(run_routing_cases(workflow))

    report = {
        "ok": len(issues) == 0,
        "workflow_path": str(WORKFLOW_PATH),
        "issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
