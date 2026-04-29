"""Long-running HTTP bridge for goofish-cli send + production autoreply runtime."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header
from pydantic import BaseModel
from starlette.responses import JSONResponse

from items import ItemCollectionError, collect_current_account_items, write_snapshot


LOGGER = logging.getLogger("goofish-bridge")

DEFAULT_AUTOREPLY_STATE: dict[str, Any] = {
    "enabled": False,
    "mode": "auto",
    "safe_mode": True,
    "auto_send": True,
    "cooldown_seconds": 15,
    "global_send_interval_seconds": 30,
    "max_reply_chars": 80,
}

DEFAULT_RUNTIME_STATE: dict[str, Any] = {
    "dedup_order": [],
    "dedup_map": {},
    "cooldown_store": {},
    "last_success_send_at": None,
}

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)\b(cookie|api[_-]?key|authorization|token|x-bridge-token)\b\s*[:=]\s*([^\s,;]+)"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"(?i)([?&](?:token|key|api_key|apikey|signature)=)[^&\s]+"), r"\1<redacted>"),
]

APP = FastAPI(title="goofish-bridge", version="2.0.0")

SEND_GUARD_EXIT_CODE = 4
AUTOREPLY_STATE_LOCK = threading.Lock()
RUNTIME_STATE_LOCK = threading.Lock()

EXTERNAL_CONTACT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("微信", re.compile(r"微信", re.IGNORECASE)),
    ("QQ", re.compile(r"qq", re.IGNORECASE)),
    ("支付宝", re.compile(r"支付宝", re.IGNORECASE)),
    ("银行卡", re.compile(r"银行卡", re.IGNORECASE)),
    ("转账", re.compile(r"转账", re.IGNORECASE)),
    ("私聊", re.compile(r"私聊", re.IGNORECASE)),
    ("加我", re.compile(r"加我", re.IGNORECASE)),
    ("线下", re.compile(r"线下", re.IGNORECASE)),
    ("电话", re.compile(r"电话", re.IGNORECASE)),
    ("手机号", re.compile(r"手机号", re.IGNORECASE)),
    ("vx", re.compile(r"\bvx\b", re.IGNORECASE)),
    ("v信", re.compile(r"v信", re.IGNORECASE)),
    ("wechat", re.compile(r"wechat", re.IGNORECASE)),
]

ABNORMAL_TEXT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("reasoning_leak", re.compile(r"<\s*/?\s*think\s*>", re.IGNORECASE)),
    ("reasoning_leak", re.compile(r"\breasoning\b", re.IGNORECASE)),
    ("reasoning_leak", re.compile(r"\banalysis\b", re.IGNORECASE)),
    ("reasoning_leak", re.compile(r"思考过程|推理过程|链路推理|内部推理", re.IGNORECASE)),
    ("error_leak", re.compile(r"traceback", re.IGNORECASE)),
    ("error_leak", re.compile(r"stack\s*trace", re.IGNORECASE)),
    ("error_leak", re.compile(r"\bexception\b", re.IGNORECASE)),
    ("error_leak", re.compile(r"^\s*error[:：]", re.IGNORECASE)),
    ("error_leak", re.compile(r"^\s*错误[:：]", re.IGNORECASE)),
    ("error_leak", re.compile(r"undefined|null|nan", re.IGNORECASE)),
]

PUNCT_OR_SYMBOL_ONLY_PATTERN = re.compile(r"^[\s\W_]+$", re.UNICODE)

HANDOFF_KEYWORDS: list[str] = [
    "退款",
    "售后",
    "投诉",
    "举报",
    "假货",
    "法律",
    "起诉",
    "律师",
    "辱骂",
    "威胁",
    "线下交易",
    "线下",
    "微信",
    "qq",
    "支付宝",
    "银行卡",
    "转账",
    "私聊",
    "加我",
    "地址纠纷",
    "订单异常",
    "付款异常",
    "支付异常",
    "发货纠纷",
    "绕开平台",
    "平台外交易",
    "承诺",
    "保证",
    "赔偿",
]


class SendRequest(BaseModel):
    cid: str
    toid: str
    text: str


def setup_logging() -> None:
    level_name = os.environ.get("GOOFISH_BRIDGE_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def redact_sensitive(text: str) -> str:
    sanitized = text
    for pattern, replacement in SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


def env_int(name: str, default_value: int, min_value: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default_value
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("invalid int env %s, fallback=%s", name, default_value)
        return default_value
    return value if value >= min_value else min_value


def get_max_reply_chars() -> int:
    return env_int("MAX_REPLY_CHARS", 80, min_value=1)


def to_int(value: Any, default_value: int, min_value: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default_value
    if parsed < min_value:
        return min_value
    return parsed


def to_bool(value: Any, default: bool = False) -> bool:
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
    return default


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


def to_optional_string(value: Any) -> str:
    if not is_meaningful(value):
        return ""
    return str(value).strip()


def parse_truthy(value: Any) -> bool:
    return to_bool(value, default=False)


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


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def get_autoreply_state_file() -> Path:
    raw = os.environ.get("AUTOREPLY_STATE_FILE", "/app/data/autoreply-state.json")
    return Path(raw)


def get_runtime_state_file() -> Path:
    raw = os.environ.get("AUTOREPLY_RUNTIME_STATE_FILE", "/app/data/autoreply-runtime-state.json")
    return Path(raw)


def normalize_autoreply_state(data: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(DEFAULT_AUTOREPLY_STATE)
    if isinstance(data, dict):
        state.update(data)
    return state


def normalize_runtime_state(data: dict[str, Any] | None) -> dict[str, Any]:
    runtime = dict(DEFAULT_RUNTIME_STATE)
    if isinstance(data, dict):
        runtime.update(data)

    dedup_order = runtime.get("dedup_order")
    dedup_map = runtime.get("dedup_map")
    cooldown_store = runtime.get("cooldown_store")

    runtime["dedup_order"] = dedup_order if isinstance(dedup_order, list) else []
    runtime["dedup_map"] = dedup_map if isinstance(dedup_map, dict) else {}
    runtime["cooldown_store"] = cooldown_store if isinstance(cooldown_store, dict) else {}

    last_success = runtime.get("last_success_send_at")
    if last_success is not None:
        try:
            runtime["last_success_send_at"] = float(last_success)
        except (TypeError, ValueError):
            runtime["last_success_send_at"] = None

    return runtime


def load_autoreply_state() -> dict[str, Any]:
    state_file = get_autoreply_state_file()
    with AUTOREPLY_STATE_LOCK:
        if not state_file.exists():
            return dict(DEFAULT_AUTOREPLY_STATE)
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("failed to parse autoreply state, fallback default: %s", exc)
            return dict(DEFAULT_AUTOREPLY_STATE)
        return normalize_autoreply_state(data if isinstance(data, dict) else None)


def save_autoreply_state(state: dict[str, Any]) -> None:
    state_file = get_autoreply_state_file()
    with AUTOREPLY_STATE_LOCK:
        atomic_write_json(state_file, normalize_autoreply_state(state))


def load_runtime_state() -> dict[str, Any]:
    runtime_file = get_runtime_state_file()
    with RUNTIME_STATE_LOCK:
        if not runtime_file.exists():
            return dict(DEFAULT_RUNTIME_STATE)
        try:
            data = json.loads(runtime_file.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("failed to parse runtime state, fallback default: %s", exc)
            return dict(DEFAULT_RUNTIME_STATE)
        return normalize_runtime_state(data if isinstance(data, dict) else None)


def save_runtime_state(state: dict[str, Any]) -> None:
    runtime_file = get_runtime_state_file()
    with RUNTIME_STATE_LOCK:
        atomic_write_json(runtime_file, normalize_runtime_state(state))


def mutate_runtime_state(mutator):  # type: ignore[no-untyped-def]
    runtime_file = get_runtime_state_file()
    with RUNTIME_STATE_LOCK:
        if runtime_file.exists():
            try:
                data = json.loads(runtime_file.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = {}
        else:
            data = {}

        state = normalize_runtime_state(data if isinstance(data, dict) else None)
        result = mutator(state)
        atomic_write_json(runtime_file, state)
        return result


def set_autoreply_enabled(enabled: bool) -> dict[str, Any]:
    state = load_autoreply_state()
    state["enabled"] = enabled
    save_autoreply_state(state)
    return state


def get_bridge_auth_token() -> str:
    return os.environ.get("BRIDGE_AUTH_TOKEN", "").strip()


def require_bridge_token(bridge_token: str | None) -> JSONResponse | None:
    expected = get_bridge_auth_token()
    if not expected:
        return None
    provided = (bridge_token or "").strip()
    if provided == expected:
        return None
    return JSONResponse(
        status_code=401,
        content={
            "ok": False,
            "sent": False,
            "reason": "unauthorized",
        },
    )


def build_send_error_response(
    *,
    status_code: int,
    reason: str,
    cid: str,
    toid: str,
    exit_code: int,
    stderr: str,
    stdout: str = "",
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "sent": False,
            "reason": reason,
            "cid": cid,
            "toid": toid,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        },
    )


def detect_external_contact(text: str) -> tuple[bool, str]:
    for label, pattern in EXTERNAL_CONTACT_PATTERNS:
        if pattern.search(text):
            return True, label
    return False, ""


def detect_abnormal_text(text: str) -> tuple[bool, str]:
    if not text.strip():
        return True, "empty_text"
    if PUNCT_OR_SYMBOL_ONLY_PATTERN.fullmatch(text):
        return True, "punctuation_only"
    for label, pattern in ABNORMAL_TEXT_PATTERNS:
        if pattern.search(text):
            return True, label
    return False, ""


def check_global_send_interval(interval_seconds: int) -> tuple[bool, int]:
    if interval_seconds <= 0:
        return False, 0
    now = time.time()

    def _read(state: dict[str, Any]) -> tuple[bool, int]:
        last_success_at = state.get("last_success_send_at")
        if last_success_at is None:
            return False, 0
        try:
            elapsed = now - float(last_success_at)
        except (TypeError, ValueError):
            return False, 0
        if elapsed >= interval_seconds:
            return False, 0
        retry_after = max(1, int(interval_seconds - elapsed + 0.999))
        return True, retry_after

    return mutate_runtime_state(_read)


def mark_send_success() -> None:
    now = time.time()

    def _mutate(state: dict[str, Any]) -> None:
        state["last_success_send_at"] = now
        return None

    mutate_runtime_state(_mutate)


def run_goofish_command(args: list[str], timeout_seconds: int = 30) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
    except FileNotFoundError:
        return {
            "ok": False,
            "exit_code": 127,
            "stdout": "",
            "stderr": "goofish command not found",
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": 124,
            "stdout": "",
            "stderr": "goofish command timed out",
        }

    return {
        "ok": completed.returncode == 0,
        "exit_code": completed.returncode,
        "stdout": redact_sensitive((completed.stdout or "").strip()),
        "stderr": redact_sensitive((completed.stderr or "").strip()),
    }


def summarize_goofish_auth_status() -> dict[str, Any]:
    timeout_seconds = env_int("GOOFISH_AUTH_STATUS_TIMEOUT_SECONDS", 15, min_value=1)
    result = run_goofish_command(["goofish", "auth", "status"], timeout_seconds=timeout_seconds)
    summary_source = result["stdout"] or result["stderr"]
    summary = redact_sensitive(summary_source)[:200]
    return {
        "ok": result["ok"],
        "exit_code": result["exit_code"],
        "summary": summary,
    }


def get_snapshot_path() -> Path:
    raw = os.environ.get("ITEMS_SNAPSHOT_PATH", "/app/data/items_snapshot.json")
    return Path(raw)


def load_items_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("failed to parse items snapshot at %s: %s", path, redact_sensitive(str(exc)))
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_items_snapshot(path: Path, payload: dict[str, Any]) -> None:
    write_snapshot(str(path), payload)


def load_cookie_string_from_goofish_cli() -> str:
    cookie_file = Path.home() / ".goofish-cli" / "cookies.json"
    if not cookie_file.exists():
        return ""
    try:
        data = json.loads(cookie_file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("failed to parse goofish cookie file: %s", redact_sensitive(str(exc)))
        return ""

    if isinstance(data, dict) and isinstance(data.get("cookie_string"), str):
        return data["cookie_string"].strip()

    cookies_data: Any = data
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        cookies_data = data["cookies"]

    if not isinstance(cookies_data, list):
        return ""

    pairs: list[str] = []
    seen_names: set[str] = set()
    for cookie in cookies_data:
        if not isinstance(cookie, dict):
            continue
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        if not name or name in seen_names:
            continue
        seen_names.add(name)
        pairs.append(f"{name}={value}")
    return "; ".join(pairs).strip()


def get_cookie_string() -> str:
    env_cookie = os.environ.get("GOOFISH_COOKIE_STRING", "").strip()
    if env_cookie:
        return env_cookie
    return load_cookie_string_from_goofish_cli()


def normalize_snapshot_response(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result["ok"] = True
    result["source"] = "snapshot"
    result["response_source"] = "snapshot"
    result["snapshot"] = True
    return result


def snapshot_not_found_response(snapshot_path: Path) -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "snapshot_not_found",
        "message": "items snapshot not found",
        "snapshot_path": str(snapshot_path),
        "items": [],
        "item_count": 0,
    }


def get_refresh_hint(reason: str) -> str:
    hints = {
        "missing_cookie": (
            "missing login cookie. ensure goofish account is logged in and goofish-state is mounted to "
            "/root/.goofish-cli in goofish-bridge container."
        ),
        "not_logged_in": "cookie exists but account is not logged in on personal page. re-login and refresh again.",
        "playwright_not_installed": "playwright package missing in runtime. rebuild goofish-bridge image.",
        "playwright_browser_missing": "playwright chromium missing. rebuild image or run playwright install chromium.",
        "playwright_runtime_dependency_missing": (
            "system dependencies for playwright chromium are missing. rebuild image with playwright deps."
        ),
        "section_tab_not_found": "requested section tab is not visible on personal page. try default sections first.",
        "invalid_sections": "unsupported sections. use selling,offline,draft.",
    }
    return hints.get(reason, "refresh failed. check goofish-bridge logs for details.")


def refresh_items_snapshot_payload(
    *,
    snapshot_path: Path,
    headless: bool,
    sections: str | None,
    max_scroll_rounds: int,
) -> dict[str, Any]:
    cookie_string = get_cookie_string()
    if not cookie_string:
        reason = "missing_cookie"
        return {
            "ok": False,
            "reason": reason,
            "message": "missing Goofish cookie string",
            "hint": get_refresh_hint(reason),
            "snapshot_path": str(snapshot_path),
            "items": [],
            "item_count": 0,
        }

    safe_rounds = max(1, int(max_scroll_rounds))
    try:
        payload = collect_current_account_items(
            cookie_string,
            output_path=None,
            headless=headless,
            sections=parse_sections_param(sections),
            max_scroll_rounds=safe_rounds,
        )
        save_items_snapshot(snapshot_path, payload)
        result = dict(payload)
        result["snapshot_path"] = str(snapshot_path)
        result["refreshed"] = True
        result["source"] = "live_refresh"
        result["response_source"] = "live_refresh"
        return result
    except ItemCollectionError as exc:
        return {
            "ok": False,
            "reason": exc.reason,
            "message": exc.message,
            "hint": get_refresh_hint(exc.reason),
            "details": exc.details,
            "snapshot_path": str(snapshot_path),
            "items": [],
            "item_count": 0,
        }
    except Exception as exc:  # noqa: BLE001
        message = redact_sensitive(str(exc)).strip() or "item collection failed"
        reason = "collection_failed"
        return {
            "ok": False,
            "reason": reason,
            "message": message,
            "hint": get_refresh_hint(reason),
            "snapshot_path": str(snapshot_path),
            "items": [],
            "item_count": 0,
        }


def parse_sections_param(raw_sections: str | None) -> list[str] | None:
    if raw_sections is None:
        return None
    parts = [part.strip() for part in raw_sections.split(",") if part.strip()]
    return parts or None


def normalize_decide_input(raw_payload: dict[str, Any] | None) -> dict[str, Any]:
    root = raw_payload if isinstance(raw_payload, dict) else {}
    payload_source = "root"
    payload: dict[str, Any] = root

    body = root.get("body")
    if isinstance(body, dict):
        payload = body
        payload_source = "body_object"
    elif isinstance(body, str):
        parsed_body = parse_json_object(body)
        if parsed_body is not None:
            payload = parsed_body
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
    try:
        content_type: Any = int(content_type_raw)
    except (TypeError, ValueError):
        content_type = to_optional_string(content_type_raw)

    return {
        "cid": cid,
        "send_user_id": send_user_id,
        "send_message": send_message,
        "content_type": content_type,
        "dry_run": parse_truthy(
            pick_value(payload.get("dry_run"), payload.get("dryRun"), root.get("dry_run"), root.get("dryRun"))
        ),
        "cooldown_seconds": to_int(
            pick_value(
                payload.get("cooldown_seconds"),
                payload.get("cooldownSeconds"),
                root.get("cooldown_seconds"),
                root.get("cooldownSeconds"),
            ),
            default_value=DEFAULT_AUTOREPLY_STATE["cooldown_seconds"],
            min_value=0,
        ),
        "max_reply_chars": to_int(
            pick_value(
                payload.get("max_reply_chars"),
                payload.get("maxReplyChars"),
                root.get("max_reply_chars"),
                root.get("maxReplyChars"),
            ),
            default_value=get_max_reply_chars(),
            min_value=1,
        ),
        "item_id": to_optional_string(
            pick_value(payload.get("item_id"), payload.get("itemId"), root.get("item_id"), root.get("itemId"))
        ),
        "original_payload": payload,
        "webhook_meta": {
            "payload_source": payload_source,
            "headers": root.get("headers") if isinstance(root.get("headers"), dict) else {},
            "query": root.get("query") if isinstance(root.get("query"), dict) else {},
            "params": root.get("params") if isinstance(root.get("params"), dict) else {},
        },
    }


def create_decide_result_base(normalized: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    runtime_mode = str(os.environ.get("OPENCLAW_RUNTIME_MODE", "openai_chat") or "openai_chat").strip().lower()
    if runtime_mode != "openai_chat":
        runtime_mode = "openai_chat"

    max_reply_chars = to_int(
        normalized.get("max_reply_chars"),
        default_value=to_int(state.get("max_reply_chars"), get_max_reply_chars(), min_value=1),
        min_value=1,
    )

    return {
        "ok": True,
        "send": False,
        "reason": "init",
        "cid": normalized.get("cid", ""),
        "send_user_id": normalized.get("send_user_id", ""),
        "send_message": normalized.get("send_message", ""),
        "content_type": normalized.get("content_type", ""),
        "dry_run": parse_truthy(normalized.get("dry_run")),
        "cooldown_seconds": to_int(
            normalized.get("cooldown_seconds"),
            default_value=to_int(state.get("cooldown_seconds"), DEFAULT_AUTOREPLY_STATE["cooldown_seconds"], min_value=0),
            min_value=0,
        ),
        "max_reply_chars": max_reply_chars,
        "item_id": to_optional_string(normalized.get("item_id")),
        "handoff": False,
        "handoff_reason": "",
        "should_send": True,
        "final_reply": "",
        "reply_source": "",
        "item_context_status": "missing",
        "item_context_reason": "snapshot_unavailable",
        "openai_runtime_mode": runtime_mode,
        "openai_runtime_url": to_optional_string(os.environ.get("OPENCLAW_CHAT_COMPLETIONS_URL", "")),
        "openai_model": to_optional_string(os.environ.get("OPENCLAW_MODEL", "openclaw/default")) or "openclaw/default",
        "openai_timeout_seconds": env_int("OPENCLAW_TIMEOUT_SECONDS", 20, min_value=1),
        "openai_response": None,
        "openai_http_status": None,
        "route_reason": "",
        "error": "",
        "dedup_key": "",
        "dedup_skipped": False,
        "dedup_skip_reason": "",
        "cooldown_skipped": False,
        "cooldown_skip_reason": "",
        "handoff_notify_error": "",
    }


def apply_dedup_guard(result: dict[str, Any]) -> bool:
    if result.get("dry_run") is True:
        result["dedup_skipped"] = True
        result["dedup_skip_reason"] = "dry_run"
        return False

    cid = to_optional_string(result.get("cid"))
    send_user_id = to_optional_string(result.get("send_user_id"))
    send_message = to_optional_string(result.get("send_message"))
    dedup_key = f"{cid}::{send_user_id}::{send_message}"
    result["dedup_key"] = dedup_key

    def _mutate(state: dict[str, Any]) -> bool:
        dedup_order = state["dedup_order"]
        dedup_map = state["dedup_map"]
        is_duplicate = dedup_key in dedup_map
        if not is_duplicate:
            dedup_map[dedup_key] = int(time.time() * 1000)
            dedup_order.append(dedup_key)
            while len(dedup_order) > 500:
                oldest = dedup_order.pop(0)
                dedup_map.pop(oldest, None)
        return is_duplicate

    is_duplicate = mutate_runtime_state(_mutate)
    if is_duplicate:
        result["reason"] = "duplicate_message"
        result["route_reason"] = "duplicate_message"
        result["should_send"] = False
        result["send"] = False
        return True
    return False


def apply_cooldown_guard(result: dict[str, Any]) -> bool:
    cooldown_seconds = to_int(result.get("cooldown_seconds"), default_value=15, min_value=0)
    result["cooldown_seconds"] = cooldown_seconds

    if result.get("dry_run") is True:
        result["cooldown_skipped"] = True
        result["cooldown_skip_reason"] = "dry_run"
        return False

    cid = to_optional_string(result.get("cid"))
    if not cid or cooldown_seconds <= 0:
        return False

    now_ms = int(time.time() * 1000)

    def _mutate(state: dict[str, Any]) -> dict[str, Any]:
        cooldown_store = state["cooldown_store"]
        last_allowed = to_int(cooldown_store.get(cid), default_value=0, min_value=0)
        elapsed = (now_ms - last_allowed) / 1000 if last_allowed > 0 else cooldown_seconds
        if last_allowed > 0 and elapsed < cooldown_seconds:
            remaining = max(1, int(cooldown_seconds - elapsed + 0.999))
            return {"blocked": True, "remaining": remaining}
        cooldown_store[cid] = now_ms
        return {"blocked": False, "remaining": 0}

    cooldown_result = mutate_runtime_state(_mutate)
    if cooldown_result.get("blocked"):
        result["reason"] = "cooldown_active"
        result["route_reason"] = "cooldown_active"
        result["should_send"] = False
        result["send"] = False
        result["remaining_seconds"] = cooldown_result.get("remaining", 0)
        return True
    return False


def notify_handoff(result: dict[str, Any]) -> None:
    url = to_optional_string(os.environ.get("HANDOFF_NOTIFY_WEBHOOK_URL", ""))
    if not url:
        return
    payload = {
        "event": "handoff",
        "cid": result.get("cid", ""),
        "send_user_id": result.get("send_user_id", ""),
        "send_message": result.get("send_message", ""),
        "handoff_reason": result.get("handoff_reason", ""),
        "route_reason": result.get("route_reason", ""),
        "timestamp": int(time.time()),
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        data=data,
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            return
    except Exception as exc:  # noqa: BLE001
        result["handoff_notify_error"] = redact_sensitive(str(exc))


def run_handoff_gate(result: dict[str, Any]) -> bool:
    message = to_optional_string(result.get("send_message"))
    lowered = message.lower()
    hit = next((kw for kw in HANDOFF_KEYWORDS if kw in lowered), "")
    if not hit:
        return False

    result["handoff"] = True
    result["handoff_reason"] = f"hit_handoff_keyword:{hit}"
    result["reason"] = "handoff_gate"
    result["route_reason"] = "handoff_gate"
    result["should_send"] = False
    result["send"] = False
    notify_handoff(result)
    return True


def attach_item_context(result: dict[str, Any]) -> None:
    snapshot_path = get_snapshot_path()
    snapshot = load_items_snapshot(snapshot_path)
    if snapshot is None:
        result["item_context_status"] = "missing"
        result["item_context_reason"] = "snapshot_unavailable"
        result["item_context"] = None
        return

    if snapshot.get("ok") is True:
        result["item_context_status"] = "available"
        result["item_context_reason"] = ""
    else:
        reason = to_optional_string(snapshot.get("reason"))
        result["item_context_status"] = "missing" if reason else "error"
        result["item_context_reason"] = reason or to_optional_string(snapshot.get("message")) or "snapshot_unavailable"

    result["item_context"] = {
        "available": snapshot.get("ok") is True,
        "source": "items_snapshot",
        "item_count": to_int(snapshot.get("item_count"), default_value=0, min_value=0),
        "items": snapshot.get("items") if isinstance(snapshot.get("items"), list) else [],
        "section_counts": snapshot.get("section_counts") if isinstance(snapshot.get("section_counts"), dict) else {},
        "metadata": snapshot.get("metadata") if isinstance(snapshot.get("metadata"), dict) else {},
    }


def build_customer_service_policy() -> dict[str, Any]:
    return {
        "mode": "handoff_gate",
        "default_action": "allow_openclaw_autoreply",
        "handoff_only": True,
        "handoff_triggers": [
            "refund_or_after_sale",
            "complaint_or_report",
            "counterfeit_or_legal_risk",
            "abuse_or_threat",
            "off_platform_or_external_contact",
            "address_or_order_or_payment_or_shipping_dispute",
            "promise_requires_manual_confirmation",
        ],
        "send_guardrails": {
            "must_block_when": ["handoff_true", "should_send_false", "empty_reply", "system_exception"],
            "send_via_bridge_only": True,
        },
    }


def build_openai_chat_request(result: dict[str, Any]) -> dict[str, Any]:
    system_prompt = (
        "你是闲鱼客服助手。必须只输出JSON对象，字段只允许 reply、should_send、handoff、reason。"
        "禁止输出思考过程、Markdown、<think>。"
        "若需要人工介入、涉及风险或不确定承诺，必须 handoff=true 且 should_send=false。"
    )
    user_payload = {
        "cid": result.get("cid", ""),
        "send_user_id": result.get("send_user_id", ""),
        "buyer_message": result.get("send_message", ""),
        "item_context": result.get("item_context"),
        "item_context_status": result.get("item_context_status", ""),
        "item_context_reason": result.get("item_context_reason", ""),
        "customer_service_policy": build_customer_service_policy(),
        "max_reply_chars": result.get("max_reply_chars", 80),
        "route_reason": result.get("route_reason", "default_openclaw"),
        "handoff": result.get("handoff", False),
        "handoff_reason": result.get("handoff_reason", ""),
    }
    return {
        "model": result.get("openai_model") or "openclaw/default",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        "user": result.get("cid") or result.get("send_user_id") or "",
    }


def post_json(url: str, headers: dict[str, str], payload: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url=url, method="POST", headers=headers, data=data)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            parsed = parse_json_object(raw_body)
            return {
                "ok": True,
                "http_status": int(getattr(response, "status", 200)),
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
            "error": redact_sensitive(str(exc)),
        }
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return {
            "ok": False,
            "http_status": None,
            "elapsed_ms": elapsed_ms,
            "body": "",
            "error": redact_sensitive(str(exc)),
        }


def pick_text(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return ""


def collect_objects(raw_response: Any) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    def add(value: Any) -> None:
        parsed = parse_json_object(value)
        if parsed is not None:
            objects.append(parsed)

    add(raw_response)
    top = parse_json_object(raw_response)
    if top is None:
        return objects

    add(top.get("data"))
    add(top.get("result"))
    add(top.get("output"))
    add(top.get("response"))
    add(top.get("payload"))

    data = parse_json_object(top.get("data"))
    if data is not None:
        add(data.get("result"))
        add(data.get("output"))

    output = parse_json_object(top.get("output"))
    if output is not None:
        add(output.get("result"))

    return objects


def sanitize_reply_text(text: str, max_chars: int) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<think>[\s\S]*?</think>", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"```(?:thinking|reasoning|analysis)[\s\S]*?```", " ", cleaned, flags=re.IGNORECASE)
    markers = ["最终回复：", "最终回复:", "买家可见回复：", "买家可见回复:", "回复：", "回复:"]
    for marker in markers:
        idx = cleaned.rfind(marker)
        if idx >= 0:
            cleaned = cleaned[idx + len(marker) :]
    cleaned = " ".join(part.strip() for part in cleaned.splitlines() if part.strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_chars].strip()


def normalize_openai_response(result: dict[str, Any], response_body: Any) -> None:
    raw = parse_json_object(response_body) or {"raw": response_body}
    objects = collect_objects(raw)

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
            ("choices[0].message.content", ((obj.get("choices") or [{}])[0].get("message") or {}).get("content")),
            ("choices[0].text", (obj.get("choices") or [{}])[0].get("text")),
        ]
        for source, value in candidates:
            text = pick_text(value)
            if text:
                reply = text
                reply_source = source
                break
        if reply:
            break

    content_object = parse_json_object(reply) if reply_source == "choices[0].message.content" else None
    if content_object is not None:
        objects.insert(0, content_object)
        content_reply = pick_text(content_object.get("reply"))
        if content_reply:
            reply = content_reply
            reply_source = "choices[0].message.content.reply"
        else:
            reply = ""
            reply_source = "choices[0].message.content.object"

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
            candidates = [
                ((obj.get("error") or {}).get("message") if isinstance(obj.get("error"), dict) else obj.get("error")),
                obj.get("err"),
                obj.get("exception"),
                obj.get("message"),
            ]
            for value in candidates:
                text = pick_text(value)
                if not text:
                    continue
                lowered = text.lower()
                if any(
                    key in lowered
                    for key in ["status code", "unauthorized", "forbidden", "timeout", "error", "exception"]
                ):
                    error = text
                    break

    if handoff is None:
        handoff = False
    if should_send is None:
        should_send = not handoff

    maybe_html = reply.strip().lower()
    if not error and (maybe_html.startswith("<!doctype html") or maybe_html.startswith("<html")):
        error = "openai_html_response"

    result["openai_response"] = raw
    result["reply_source"] = reply_source
    result["should_send"] = should_send
    result["handoff"] = handoff
    if reason:
        result["openai_reason"] = reason

    max_chars = to_int(result.get("max_reply_chars"), default_value=80, min_value=1)
    final_reply = sanitize_reply_text(reply, max_chars)

    if handoff:
        result["handoff_reason"] = reason or result.get("handoff_reason") or "openai_handoff"

    if error:
        result["error"] = error
        result["should_send"] = False
        result["handoff"] = True
        if not result.get("handoff_reason"):
            result["handoff_reason"] = f"openai_error:{error}"[:160]
        result["final_reply"] = ""
        return

    if final_reply:
        has_external_contact, keyword = detect_external_contact(final_reply)
        if has_external_contact:
            result["error"] = f"external_contact_in_reply:{keyword}"
            result["handoff"] = True
            result["should_send"] = False
            result["handoff_reason"] = result.get("handoff_reason") or "external_contact_in_reply"
            result["final_reply"] = ""
            return

        abnormal, abnormal_type = detect_abnormal_text(final_reply)
        if abnormal:
            result["error"] = f"abnormal_reply:{abnormal_type}"
            result["handoff"] = True
            result["should_send"] = False
            result["handoff_reason"] = result.get("handoff_reason") or "abnormal_reply"
            result["final_reply"] = ""
            return

    result["final_reply"] = final_reply


def call_openai_runtime(result: dict[str, Any]) -> None:
    url = to_optional_string(result.get("openai_runtime_url"))
    token = to_optional_string(os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
    timeout_seconds = to_int(result.get("openai_timeout_seconds"), default_value=20, min_value=1)

    if not url:
        result["error"] = "missing_openai_runtime_url"
        result["openai_response"] = {"error": "missing_openai_runtime_url"}
        return
    if not token:
        result["error"] = "missing_openai_gateway_token"
        result["openai_response"] = {"error": "missing_openai_gateway_token"}
        return

    payload = build_openai_chat_request(result)
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }

    response = post_json(url, headers, payload, timeout_seconds)
    result["openai_http_status"] = response.get("http_status")
    result["openai_response"] = response.get("body")

    if not response.get("ok"):
        error = to_optional_string(response.get("error")) or "openai_http_error"
        status = response.get("http_status")
        status_part = f"status_{status}" if status is not None else "transport"
        result["error"] = f"openai_request_failed:{status_part}:{error}"[:220]
        result["handoff"] = True
        result["should_send"] = False
        result["handoff_reason"] = result.get("handoff_reason") or "openai_request_failed"
        return

    normalize_openai_response(result, response.get("body"))


def run_autoreply_decide(raw_payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_decide_input(raw_payload)
    state = load_autoreply_state()
    result = create_decide_result_base(normalized, state)

    if not result["cid"] or not result["send_user_id"] or not result["send_message"]:
        result["reason"] = "invalid_request"
        result["route_reason"] = "invalid_request"
        result["should_send"] = False
        result["error"] = "cid/send_user_id/send_message are required"
        return result

    if state.get("enabled") is not True:
        result["reason"] = "autoreply_disabled"
        result["route_reason"] = "autoreply_disabled"
        result["should_send"] = False
        return result

    if state.get("auto_send") is not True and not result["dry_run"]:
        result["reason"] = "auto_send_disabled"
        result["route_reason"] = "auto_send_disabled"
        result["should_send"] = False
        return result

    if apply_dedup_guard(result):
        return result

    if apply_cooldown_guard(result):
        return result

    if run_handoff_gate(result):
        return result

    attach_item_context(result)
    call_openai_runtime(result)

    if result.get("error"):
        result["reason"] = "system_exception"
        result["route_reason"] = "system_exception"
        result["should_send"] = False
        result["send"] = False
        return result

    if result.get("handoff") is True:
        result["reason"] = "handoff_gate"
        result["route_reason"] = "openai_handoff"
        result["should_send"] = False
        result["send"] = False
        if not result.get("handoff_reason"):
            result["handoff_reason"] = "openai_handoff"
        return result

    if result.get("should_send") is not True:
        result["reason"] = "should_send_false"
        result["route_reason"] = "openai_should_send_false"
        result["send"] = False
        return result

    if not to_optional_string(result.get("final_reply")):
        result["reason"] = "empty_reply"
        result["route_reason"] = "openai_no_valid_reply"
        result["should_send"] = False
        result["send"] = False
        return result

    if result["dry_run"] is True:
        result["reason"] = "dry_run"
        result["route_reason"] = "dry_run"
        result["send"] = False
        return result

    result["reason"] = "ready_to_send"
    result["route_reason"] = "ready_to_send"
    result["send"] = True
    return result


@APP.on_event("startup")
def on_startup() -> None:
    setup_logging()
    LOGGER.info("goofish-bridge startup")
    state_file = get_autoreply_state_file()
    if not state_file.exists():
        save_autoreply_state(dict(DEFAULT_AUTOREPLY_STATE))
        LOGGER.info("created default autoreply state file at %s", state_file)
    runtime_file = get_runtime_state_file()
    if not runtime_file.exists():
        save_runtime_state(dict(DEFAULT_RUNTIME_STATE))
        LOGGER.info("created runtime state file at %s", runtime_file)


@APP.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "goofish-bridge"}


@APP.get("/items/snapshot")
def items_snapshot() -> JSONResponse:
    snapshot_path = get_snapshot_path()
    payload = load_items_snapshot(snapshot_path)
    if payload is None:
        return JSONResponse(status_code=200, content=snapshot_not_found_response(snapshot_path))
    return JSONResponse(status_code=200, content=normalize_snapshot_response(payload))


@APP.get("/items/selling")
def items_selling(
    refresh: bool = False,
    headless: bool = True,
    sections: str | None = None,
    max_scroll_rounds: int = 8,
) -> JSONResponse:
    snapshot_path = get_snapshot_path()

    if not refresh:
        payload = load_items_snapshot(snapshot_path)
        if payload is None:
            return JSONResponse(status_code=200, content=snapshot_not_found_response(snapshot_path))
        return JSONResponse(status_code=200, content=normalize_snapshot_response(payload))

    payload = refresh_items_snapshot_payload(
        snapshot_path=snapshot_path,
        headless=headless,
        sections=sections,
        max_scroll_rounds=max_scroll_rounds,
    )
    return JSONResponse(status_code=200, content=payload)


@APP.post("/items/snapshot/refresh")
def items_snapshot_refresh(
    headless: bool = True,
    sections: str | None = None,
    max_scroll_rounds: int = 8,
) -> JSONResponse:
    snapshot_path = get_snapshot_path()
    payload = refresh_items_snapshot_payload(
        snapshot_path=snapshot_path,
        headless=headless,
        sections=sections,
        max_scroll_rounds=max_scroll_rounds,
    )
    return JSONResponse(status_code=200, content=payload)


@APP.get("/autoreply/status")
def autoreply_status(bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> JSONResponse:
    auth_resp = require_bridge_token(bridge_token)
    if auth_resp is not None:
        return auth_resp
    return JSONResponse(status_code=200, content={"ok": True, "state": load_autoreply_state()})


@APP.post("/autoreply/start")
def autoreply_start(bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> JSONResponse:
    auth_resp = require_bridge_token(bridge_token)
    if auth_resp is not None:
        return auth_resp
    state = set_autoreply_enabled(True)
    return JSONResponse(status_code=200, content={"ok": True, "state": state})


@APP.post("/autoreply/stop")
def autoreply_stop(bridge_token: str | None = Header(default=None, alias="X-Bridge-Token")) -> JSONResponse:
    auth_resp = require_bridge_token(bridge_token)
    if auth_resp is not None:
        return auth_resp
    state = set_autoreply_enabled(False)
    return JSONResponse(status_code=200, content={"ok": True, "state": state})


@APP.post("/autoreply/decide")
def autoreply_decide(
    payload: dict[str, Any],
    bridge_token: str | None = Header(default=None, alias="X-Bridge-Token"),
) -> JSONResponse:
    auth_resp = require_bridge_token(bridge_token)
    if auth_resp is not None:
        return auth_resp
    result = run_autoreply_decide(payload)
    return JSONResponse(status_code=200, content=result)


@APP.post("/send")
def send(
    payload: SendRequest,
    bridge_token: str | None = Header(default=None, alias="X-Bridge-Token"),
) -> JSONResponse:
    auth_resp = require_bridge_token(bridge_token)
    if auth_resp is not None:
        return auth_resp

    cid = to_optional_string(getattr(payload, "cid", ""))
    toid = to_optional_string(getattr(payload, "toid", ""))
    text = to_optional_string(getattr(payload, "text", ""))

    if not cid or not toid or not text:
        return build_send_error_response(
            status_code=400,
            reason="invalid_request",
            cid=cid,
            toid=toid,
            exit_code=2,
            stderr="cid, toid, text are required",
        )

    max_reply_chars = get_max_reply_chars()
    if len(text) > max_reply_chars:
        return build_send_error_response(
            status_code=400,
            reason="text_too_long",
            cid=cid,
            toid=toid,
            exit_code=2,
            stderr=f"text is too long, max={max_reply_chars}",
        )

    state = load_autoreply_state()
    if state.get("enabled") is not True:
        return build_send_error_response(
            status_code=409,
            reason="autoreply_disabled",
            cid=cid,
            toid=toid,
            exit_code=3,
            stderr="autoreply is disabled",
        )
    if state.get("auto_send") is not True:
        return build_send_error_response(
            status_code=409,
            reason="auto_send_disabled",
            cid=cid,
            toid=toid,
            exit_code=3,
            stderr="auto_send is disabled",
        )

    safe_mode = to_bool(state.get("safe_mode"), default=True)
    if safe_mode:
        has_external_contact, keyword = detect_external_contact(text)
        if has_external_contact:
            return build_send_error_response(
                status_code=400,
                reason="external_contact_blocked",
                cid=cid,
                toid=toid,
                exit_code=SEND_GUARD_EXIT_CODE,
                stderr=f"external contact keyword blocked: {keyword}",
            )

        abnormal, abnormal_type = detect_abnormal_text(text)
        if abnormal:
            return build_send_error_response(
                status_code=400,
                reason="abnormal_text_blocked",
                cid=cid,
                toid=toid,
                exit_code=SEND_GUARD_EXIT_CODE,
                stderr=f"abnormal text blocked: {abnormal_type}",
            )

        interval_seconds = to_int(
            state.get("global_send_interval_seconds"),
            default_value=to_int(DEFAULT_AUTOREPLY_STATE.get("global_send_interval_seconds"), 30, min_value=0),
            min_value=0,
        )
        rate_limited, retry_after_seconds = check_global_send_interval(interval_seconds)
        if rate_limited:
            return build_send_error_response(
                status_code=429,
                reason="global_rate_limited",
                cid=cid,
                toid=toid,
                exit_code=SEND_GUARD_EXIT_CODE,
                stderr=(
                    "global send interval active, retry after "
                    f"{retry_after_seconds}s (interval={interval_seconds}s)"
                ),
            )

    timeout_seconds = env_int("GOOFISH_SEND_TIMEOUT_SECONDS", 30, min_value=1)
    result = run_goofish_command(
        ["goofish", "message", "send", "--cid", cid, "--toid", toid, "--text", text],
        timeout_seconds=timeout_seconds,
    )

    response = {
        "ok": result["ok"],
        "sent": result["ok"],
        "reason": "sent" if result["ok"] else "goofish_send_failed",
        "cid": cid,
        "toid": toid,
        "exit_code": result["exit_code"],
        "stdout": result["stdout"],
        "stderr": result["stderr"],
    }

    if result["ok"]:
        if safe_mode:
            mark_send_success()
        return JSONResponse(status_code=200, content=response)
    return JSONResponse(status_code=500, content=response)


@APP.get("/status")
def status() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "goofish-bridge",
        "autoreply": load_autoreply_state(),
        "runtime": load_runtime_state(),
        "goofish_auth": summarize_goofish_auth_status(),
    }
