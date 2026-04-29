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
                "cid": cid,
                "toid": toid,
                "exit_code": 2,
                "stdout": "",
                "stderr": f"text is too long, max={max_reply_chars}",
            },
        )

    timeout_seconds = env_int("GOOFISH_SEND_TIMEOUT_SECONDS", 30, min_value=1)
    result = run_goofish_command(
        ["goofish", "message", "send", "--cid", cid, "--toid", toid, "--text", text],
        timeout_seconds=timeout_seconds,
    )
    response = {
        "ok": result["ok"],
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
