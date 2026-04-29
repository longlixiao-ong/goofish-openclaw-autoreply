"""Long-running HTTP bridge for goofish-cli send + autoreply switch state."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI
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

SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)\b(cookie|api[_-]?key|authorization|token)\b\s*[:=]\s*([^\s,;]+)"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"(?i)([?&](?:token|key|api_key|apikey|signature)=)[^&\s]+"), r"\1<redacted>"),
]

APP = FastAPI(title="goofish-bridge", version="1.0.0")

SEND_GUARD_EXIT_CODE = 4
LAST_SUCCESS_SEND_AT: float | None = None
LAST_SUCCESS_SEND_LOCK = threading.Lock()

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
    with LAST_SUCCESS_SEND_LOCK:
        if LAST_SUCCESS_SEND_AT is None:
            return False, 0
        elapsed = now - LAST_SUCCESS_SEND_AT
    if elapsed >= interval_seconds:
        return False, 0
    retry_after = max(1, int(interval_seconds - elapsed + 0.999))
    return True, retry_after


def mark_send_success() -> None:
    global LAST_SUCCESS_SEND_AT
    with LAST_SUCCESS_SEND_LOCK:
        LAST_SUCCESS_SEND_AT = time.time()


def get_autoreply_state_file() -> Path:
    raw = os.environ.get("AUTOREPLY_STATE_FILE", "/app/data/autoreply-state.json")
    return Path(raw)


def load_autoreply_state() -> dict[str, Any]:
    state_file = get_autoreply_state_file()
    if not state_file.exists():
        return dict(DEFAULT_AUTOREPLY_STATE)
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("failed to parse autoreply state, fallback default: %s", exc)
        return dict(DEFAULT_AUTOREPLY_STATE)
    if not isinstance(data, dict):
        return dict(DEFAULT_AUTOREPLY_STATE)
    state = dict(DEFAULT_AUTOREPLY_STATE)
    state.update(data)
    return state


def save_autoreply_state(state: dict[str, Any]) -> None:
    state_file = get_autoreply_state_file()
    state_file.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_AUTOREPLY_STATE)
    merged.update(state)
    state_file.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def set_autoreply_enabled(enabled: bool) -> dict[str, Any]:
    state = load_autoreply_state()
    state["enabled"] = enabled
    save_autoreply_state(state)
    return state


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


@APP.on_event("startup")
def on_startup() -> None:
    setup_logging()
    LOGGER.info("goofish-bridge startup")
    state_file = get_autoreply_state_file()
    if not state_file.exists():
        save_autoreply_state(dict(DEFAULT_AUTOREPLY_STATE))
        LOGGER.info("created default autoreply state file at %s", state_file)


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
def autoreply_status() -> dict[str, Any]:
    return {"ok": True, "state": load_autoreply_state()}


@APP.post("/autoreply/start")
def autoreply_start() -> dict[str, Any]:
    state = set_autoreply_enabled(True)
    return {"ok": True, "state": state}


@APP.post("/autoreply/stop")
def autoreply_stop() -> dict[str, Any]:
    state = set_autoreply_enabled(False)
    return {"ok": True, "state": state}


@APP.post("/send")
def send(payload: SendRequest) -> JSONResponse:
    cid = payload.cid.strip()
    toid = payload.toid.strip()
    text = payload.text.strip()

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
        "goofish_auth": summarize_goofish_auth_status(),
    }
