from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-openclaw")


class ReplyRequest(BaseModel):
    cid: Optional[str] = ""
    toid: Optional[str] = ""
    message: Optional[str] = ""
    risk: Optional[str] = ""
    rule_reply: Optional[str] = ""
    item_context: Optional[dict[str, Any]] = None
    item_context_status: Optional[str] = ""
    item_context_reason: Optional[str] = ""
    customer_service_policy: Optional[dict[str, Any]] = None


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "mock-openclaw"}


@app.post("/reply")
def reply(payload: ReplyRequest) -> dict:
    message = (payload.message or "").strip()
    item_context = payload.item_context if isinstance(payload.item_context, dict) else None
    item_context_available = bool(item_context and item_context.get("available") is True)
    lowered_message = message.lower()

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
    reason = f"hit_handoff_keyword:{hit_handoff_keyword}" if handoff else "default_openclaw"

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

    item_context_status = (payload.item_context_status or "").strip()
    if not item_context_status:
        item_context_status = "available" if item_context_available else "missing"

    item_context_reason = (payload.item_context_reason or "").strip()

    if item_context_available:
        items = item_context.get("items")
        if not isinstance(items, list):
            items = []
        first_item = items[0] if items and isinstance(items[0], dict) else {}
        item_context_summary = {
            "item_count": item_context.get("item_count"),
            "first_item_title": str(first_item.get("title") or ""),
            "first_item_price": str(first_item.get("price") or ""),
            "source": str(item_context.get("source") or ""),
        }
    else:
        item_context_summary = {
            "item_count": 0,
            "first_item_title": "",
            "first_item_price": "",
            "source": "",
        }

    if not reply_text and should_send:
        should_send = False
        reason = "mock_no_valid_reply"

    return {
        "ok": True,
        "reply": reply_text,
        "should_send": should_send,
        "handoff": handoff,
        "reason": reason,
        "source": "mock-openclaw",
        "cid": payload.cid or "",
        "toid": payload.toid or "",
        "customer_service_policy_mode": (
            payload.customer_service_policy.get("mode")
            if isinstance(payload.customer_service_policy, dict)
            else ""
        ),
        "received_item_context": item_context_available,
        "item_context_status": item_context_status,
        "item_context_reason": item_context_reason,
        "item_context_summary": item_context_summary,
    }
