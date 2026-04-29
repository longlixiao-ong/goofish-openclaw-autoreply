from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-openclaw")


class ReplyRequest(BaseModel):
    cid: Optional[str] = ""
    toid: Optional[str] = ""
    message: Optional[str] = ""
    risk: Optional[str] = ""
    rule_reply: Optional[str] = ""


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "mock-openclaw"}


@app.post("/reply")
def reply(payload: ReplyRequest) -> dict:
    rule_reply = (payload.rule_reply or "").strip()
    message = (payload.message or "").strip()

    if rule_reply:
        reply_text = rule_reply
    elif "包邮" in message:
        reply_text = "默认不包，合适可以小刀"
    elif "还在吗" in message or "在吗" in message:
        reply_text = "在的，喜欢可拍"
    else:
        reply_text = "价格合适可以拍"

    return {
        "ok": True,
        "reply": reply_text,
        "source": "mock-openclaw",
        "cid": payload.cid or "",
        "toid": payload.toid or "",
    }
