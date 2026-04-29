"""Offline guard tests for goofish-bridge /send.

This script never calls real goofish-cli and never sends buyer messages.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def install_stubs() -> None:
    fastapi_mod = types.ModuleType("fastapi")

    class FastAPI:  # noqa: D401
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def get(self, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            def deco(fn):  # type: ignore[no-untyped-def]
                return fn

            return deco

        def post(self, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            def deco(fn):  # type: ignore[no-untyped-def]
                return fn

            return deco

        def on_event(self, *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
            def deco(fn):  # type: ignore[no-untyped-def]
                return fn

            return deco

    fastapi_mod.FastAPI = FastAPI  # type: ignore[attr-defined]
    sys.modules["fastapi"] = fastapi_mod

    pydantic_mod = types.ModuleType("pydantic")

    class BaseModel:  # noqa: D401
        pass

    pydantic_mod.BaseModel = BaseModel  # type: ignore[attr-defined]
    sys.modules["pydantic"] = pydantic_mod

    starlette_mod = types.ModuleType("starlette")
    responses_mod = types.ModuleType("starlette.responses")

    class JSONResponse:  # noqa: D401
        def __init__(self, *, status_code: int = 200, content: Any = None) -> None:
            self.status_code = status_code
            self.content = content

    responses_mod.JSONResponse = JSONResponse  # type: ignore[attr-defined]
    starlette_mod.responses = responses_mod  # type: ignore[attr-defined]
    sys.modules["starlette"] = starlette_mod
    sys.modules["starlette.responses"] = responses_mod

    items_mod = types.ModuleType("items")

    class ItemCollectionError(Exception):
        def __init__(self, reason: str, message: str, details: dict[str, Any] | None = None) -> None:
            super().__init__(message)
            self.reason = reason
            self.message = message
            self.details = details or {}

    def collect_current_account_items(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "items": [], "item_count": 0}

    def write_snapshot(path: str, payload: dict[str, Any]) -> None:
        Path(path).write_text("{}", encoding="utf-8")

    items_mod.ItemCollectionError = ItemCollectionError  # type: ignore[attr-defined]
    items_mod.collect_current_account_items = collect_current_account_items  # type: ignore[attr-defined]
    items_mod.write_snapshot = write_snapshot  # type: ignore[attr-defined]
    sys.modules["items"] = items_mod


def import_bridge_module():
    install_stubs()
    spec = importlib.util.spec_from_file_location("goofish_bridge_app", "goofish-bridge/app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def assert_response(resp: Any, *, status: int, reason: str) -> None:
    assert getattr(resp, "status_code", None) == status, resp
    content = getattr(resp, "content", None)
    assert isinstance(content, dict), resp
    assert content.get("reason") == reason, content
    if status == 200:
        assert content.get("ok") is True, content
    else:
        assert content.get("ok") is False, content


def main() -> int:
    bridge = import_bridge_module()

    state: dict[str, Any] = {
        "enabled": True,
        "auto_send": True,
        "safe_mode": True,
        "global_send_interval_seconds": 30,
    }
    call_count = {"n": 0}

    def fake_load_state() -> dict[str, Any]:
        return dict(state)

    def fake_run_goofish_command(args: list[str], timeout_seconds: int = 30) -> dict[str, Any]:
        call_count["n"] += 1
        return {"ok": True, "exit_code": 0, "stdout": "ok", "stderr": ""}

    bridge.load_autoreply_state = fake_load_state
    bridge.run_goofish_command = fake_run_goofish_command
    bridge.get_max_reply_chars = lambda: 80

    # Case 1: external contact blocked in safe_mode; goofish command should NOT be called.
    bridge.LAST_SUCCESS_SEND_AT = None
    call_count["n"] = 0
    resp = bridge.send(SimpleNamespace(cid="c1", toid="u1", text="加我微信聊"))
    assert_response(resp, status=400, reason="external_contact_blocked")
    assert call_count["n"] == 0, "goofish command should not run when external contact blocked"

    # Case 2: abnormal text blocked in safe_mode; goofish command should NOT be called.
    bridge.LAST_SUCCESS_SEND_AT = None
    call_count["n"] = 0
    resp = bridge.send(SimpleNamespace(cid="c2", toid="u2", text="Traceback: stack trace leaked"))
    assert_response(resp, status=400, reason="abnormal_text_blocked")
    assert call_count["n"] == 0, "goofish command should not run when abnormal text blocked"

    # Case 3: global rate limit in safe_mode; second request should be blocked before goofish command.
    bridge.LAST_SUCCESS_SEND_AT = None
    call_count["n"] = 0
    resp1 = bridge.send(SimpleNamespace(cid="c3", toid="u3", text="在的，喜欢可拍"))
    assert getattr(resp1, "status_code", None) == 200, resp1
    assert call_count["n"] == 1, "first request should call goofish command"

    resp2 = bridge.send(SimpleNamespace(cid="c4", toid="u4", text="价格合适可以拍"))
    assert_response(resp2, status=429, reason="global_rate_limited")
    assert call_count["n"] == 1, "second request should be blocked by rate limit without goofish call"

    # Case 4: safe_mode=false keeps basic limits but disables advanced guards.
    state["safe_mode"] = False
    bridge.LAST_SUCCESS_SEND_AT = None
    call_count["n"] = 0
    resp3 = bridge.send(SimpleNamespace(cid="c5", toid="u5", text="加我微信聊"))
    assert getattr(resp3, "status_code", None) == 200, resp3
    assert call_count["n"] == 1, "safe_mode=false should allow text to reach goofish command"

    # Base limits still apply in safe_mode=false.
    resp4 = bridge.send(SimpleNamespace(cid="c6", toid="u6", text="x" * 120))
    assert_response(resp4, status=400, reason="text_too_long")
    assert call_count["n"] == 1, "text_too_long should fail before goofish command"

    print("bridge_send_guard_tests: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
