"""Forward `goofish message watch` JSONL message events to n8n."""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from typing import Any
from urllib.parse import urlsplit

import requests


LOGGER = logging.getLogger("goofish-watcher")
SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"(?i)\b(cookie|api[_-]?key|authorization|token)\b\s*[:=]\s*([^\s,;]+)"),
        r"\1=<redacted>",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"(?i)([?&](?:token|key|api_key|apikey|signature)=)[^&\s]+"), r"\1<redacted>"),
]


def redact_sensitive(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def get_env_int(name: str, default_value: int, min_value: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default_value
    try:
        value = int(raw)
    except ValueError:
        LOGGER.warning("invalid int for %s, fallback=%s", name, default_value)
        return default_value
    return max(min_value, value)


def get_env_float(name: str, default_value: float, min_value: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default_value
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("invalid float for %s, fallback=%s", name, default_value)
        return default_value
    return max(min_value, value)


def get_safe_url_label(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme or not parsed.netloc:
        return "<invalid-url>"
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def setup_logging() -> None:
    log_level = os.environ.get("WATCHER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def start_watch_process() -> subprocess.Popen[str]:
    return subprocess.Popen(
        ["goofish", "message", "watch"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def parse_watch_line(line: str) -> dict[str, Any] | None:
    if not line.startswith("{"):
        LOGGER.debug("non-json watch output skipped: %s", redact_sensitive(line[:200]))
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        LOGGER.warning("invalid json line skipped")
        return None
    if not isinstance(data, dict):
        return None
    return data


def post_event_with_retry(
    url: str,
    event: dict[str, Any],
    timeout_seconds: float,
    max_retries: int,
    retry_delay_seconds: float,
) -> None:
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(url, json=event, timeout=timeout_seconds)
            response.raise_for_status()
            return
        except requests.RequestException as exc:
            if attempt >= max_retries:
                raise RuntimeError(f"post failed after {max_retries + 1} attempts: {exc}") from exc
            wait_seconds = retry_delay_seconds * (2**attempt)
            LOGGER.warning("post retry %s/%s in %.1fs", attempt + 1, max_retries, wait_seconds)
            time.sleep(wait_seconds)


def stop_process(proc: subprocess.Popen[str]) -> int | None:
    if proc.poll() is not None:
        return proc.returncode
    proc.terminate()
    try:
        return proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return proc.returncode


def main() -> int:
    setup_logging()
    webhook_url = os.environ.get("N8N_WEBHOOK_URL")
    if not webhook_url:
        LOGGER.error("N8N_WEBHOOK_URL is required")
        return 2

    restart_delay_seconds = get_env_int("WATCHER_RESTART_DELAY_SECONDS", 5, min_value=1)
    post_timeout_seconds = get_env_float("WATCHER_POST_TIMEOUT_SECONDS", 10.0, min_value=1.0)
    post_max_retries = get_env_int("WATCHER_POST_MAX_RETRIES", 3, min_value=0)
    post_retry_delay_seconds = get_env_float("WATCHER_POST_RETRY_DELAY_SECONDS", 1.0, min_value=0.1)

    LOGGER.info(
        "watcher started (webhook=%s, restart_delay=%ss, post_max_retries=%s)",
        get_safe_url_label(webhook_url),
        restart_delay_seconds,
        post_max_retries,
    )

    while True:
        proc = start_watch_process()
        LOGGER.info("goofish watch process started (pid=%s)", proc.pid)
        try:
            assert proc.stdout is not None
            for raw_line in proc.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                event = parse_watch_line(line)
                if event is None or event.get("event") != "message":
                    continue

                cid = str(event.get("cid", ""))
                toid = str(event.get("send_user_id", ""))
                try:
                    post_event_with_retry(
                        webhook_url,
                        event,
                        timeout_seconds=post_timeout_seconds,
                        max_retries=post_max_retries,
                        retry_delay_seconds=post_retry_delay_seconds,
                    )
                    LOGGER.info("forwarded message event (cid=%s, toid=%s)", cid, toid)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error(
                        "failed to forward message event (cid=%s, toid=%s): %s",
                        cid,
                        toid,
                        redact_sensitive(str(exc)),
                    )
        except KeyboardInterrupt:
            LOGGER.info("watcher interrupted, exiting")
            stop_process(proc)
            return 0
        except Exception as exc:  # noqa: BLE001
            LOGGER.error("watcher loop error: %s", redact_sensitive(str(exc)))
        finally:
            exit_code = stop_process(proc)
            LOGGER.warning(
                "goofish watch process exited (code=%s), restart in %ss",
                exit_code,
                restart_delay_seconds,
            )
            time.sleep(restart_delay_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
