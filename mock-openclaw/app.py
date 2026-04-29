from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-openclaw")


class ReplyRequest(BaseModel):
    cid: str = ""
    toid: str = ""
    message: str = ""
    risk: str = ""
    rule_reply: str = ""
    item_context: dict[str, Any] | None = None
    item_context_status: str = ""
    item_context_reason: str = ""
    customer_service_policy: dict[str, Any] | None = None


class ChatCompletionsRequest(BaseModel):
    model: str = "openclaw/default"
    messages: list[dict[str, Any]] = []
    user: str = ""


def make_mock_decision(message: str) -> dict[str, Any]:
    lowered_message = (message or "").strip().lower()
    handoff_keywords = [
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
    hit_handoff_keyword = next((kw for kw in handoff_keywords if kw in lowered_message), "")

    handoff = bool(hit_handoff_keyword)
    should_send = not handoff
    reason = f"hit_handoff_keyword:{hit_handoff_keyword}" if handoff else "mock"

    if handoff:
        reply_text = ""
    elif "包邮" in message:
        reply_text = "默认不包，合适可以小刀"
    elif "还在吗" in message or "在吗" in message:
        reply_text = "在的，喜欢可拍"
    elif "别回复" in message or "不要回复" in message:
        should_send = False
        reason = "mock_should_send_false"
        reply_text = ""
    else:
        reply_text = "价格合适可以拍"

    if not reply_text and should_send:
        should_send = False
        reason = "mock_no_valid_reply"

    return {
        "reply": reply_text,
        "should_send": should_send,
        "handoff": handoff,
        "reason": reason,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "mock-openclaw"}


@app.post("/reply")
def reply(payload: ReplyRequest) -> dict[str, Any]:
    decision = make_mock_decision(payload.message)
    return {
        "ok": True,
        "source": "mock-openclaw",
        "cid": payload.cid,
        "toid": payload.toid,
        **decision,
    }


@app.post("/v1/chat/completions")
def chat_completions(payload: ChatCompletionsRequest) -> dict[str, Any]:
    user_message = ""
    for message in payload.messages:
        if str(message.get("role", "")).lower() != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            user_message = content
            break
    decision = make_mock_decision(user_message)
    content = json.dumps(decision, ensure_ascii=False)
    return {
        "id": "mock-chatcmpl-001",
        "object": "chat.completion",
        "created": 0,
        "model": payload.model or "openclaw/default",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }
