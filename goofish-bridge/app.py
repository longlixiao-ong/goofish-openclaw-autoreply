"""Long-running HTTP bridge for goofish-cli send + autoreply switch state."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel
from starlette.responses import JSONResponse

from items import collect_current_account_items, write_snapshot


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


def snapshot_not_found_response() -> dict[str, Any]:
    return {
        "ok": False,
        "reason": "snapshot_not_found",
        "message": "items snapshot not found",
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
        return JSONResponse(status_code=200, content=snapshot_not_found_response())
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
            return JSONResponse(status_code=200, content=snapshot_not_found_response())
        return JSONResponse(status_code=200, content=normalize_snapshot_response(payload))

    cookie_string = get_cookie_string()
    if not cookie_string:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "reason": "missing_cookie",
                "message": "missing Goofish cookie string",
                "items": [],
                "item_count": 0,
            },
        )

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
        return JSONResponse(status_code=200, content=payload)
    except Exception as exc:  # noqa: BLE001
        message = redact_sensitive(str(exc)).strip() or "item collection failed"
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "reason": "collection_failed",
                "message": message,
                "items": [],
                "item_count": 0,
            },
        )


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
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "sent": False,
                "reason": "invalid_request",
                "cid": cid,
                "toid": toid,
                "exit_code": 2,
                "stdout": "",
                "stderr": "cid, toid, text are required",
            },
        )

    max_reply_chars = get_max_reply_chars()
    if len(text) > max_reply_chars:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "sent": False,
                "reason": "text_too_long",
                "cid": cid,
                "toid": toid,
                "exit_code": 2,
                "stdout": "",
                "stderr": f"text is too long, max={max_reply_chars}",
            },
        )

    state = load_autoreply_state()
    if state.get("enabled") is not True:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "sent": False,
                "reason": "autoreply_disabled",
                "cid": cid,
                "toid": toid,
                "exit_code": 3,
                "stdout": "",
                "stderr": "autoreply is disabled",
            },
        )
    if state.get("auto_send") is not True:
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "sent": False,
                "reason": "auto_send_disabled",
                "cid": cid,
                "toid": toid,
                "exit_code": 3,
                "stdout": "",
                "stderr": "auto_send is disabled",
            },
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
