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


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "mock-openclaw"}


@app.post("/reply")
def reply(payload: ReplyRequest) -> dict:
    rule_reply = (payload.rule_reply or "").strip()
    message = (payload.message or "").strip()
    item_context = payload.item_context if isinstance(payload.item_context, dict) else None
    item_context_available = bool(item_context and item_context.get("available") is True)

    if rule_reply:
        reply_text = rule_reply
    elif "包邮" in message:
        reply_text = "默认不包，合适可以小刀"
    elif "还在吗" in message or "在吗" in message:
        reply_text = "在的，喜欢可拍"
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

    return {
        "ok": True,
        "reply": reply_text,
        "source": "mock-openclaw",
        "cid": payload.cid or "",
        "toid": payload.toid or "",
        "received_item_context": item_context_available,
        "item_context_status": item_context_status,
        "item_context_reason": item_context_reason,
        "item_context_summary": item_context_summary,
    }
