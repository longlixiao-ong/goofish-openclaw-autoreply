# goofish-bridge

`goofish-bridge` 是生产安全网关，不是独立客服模型层。

职责边界：

- 只调用 OpenClaw Gateway / Agent 接口（OpenAI-compatible `/v1/chat/completions`）。
- 不管理底层模型供应商，不配置 GPT/Gemini/DashScope/DeepSeek 客服模型。
- 不承载复杂客服 Prompt/议价策略/卖家人设策略。
- 负责安全边界：鉴权、状态检查、dedup、cooldown、转人工门控、item_context 精简透传、fail-closed、最终 `/send` 闸门。

OpenClaw 负责：

- 模型选择、Prompt、记忆、视觉理解、回复策略、小刀议价规则。

## Endpoints

```text
GET  /health                            # 无鉴权，仅返回基础存活状态
GET  /status                            # 需鉴权
GET  /items/snapshot                    # 需鉴权
GET  /items/selling                     # 需鉴权
POST /items/snapshot/refresh            # 需鉴权

GET  /autoreply/status                  # 需鉴权
POST /autoreply/start                   # 需鉴权
POST /autoreply/stop                    # 需鉴权
POST /autoreply/decide                  # 需鉴权

POST /send                              # 需鉴权，最终发送闸门
```

## Auth

配置 `BRIDGE_AUTH_TOKEN` 后，除 `/health` 外所有接口都必须带：

```text
X-Bridge-Token: <BRIDGE_AUTH_TOKEN>
```

`/health` 固定最小返回：

```json
{"ok": true, "service": "goofish-bridge"}
```

不返回鉴权状态、token、Cookie、runtime 细节。

## Environment

```env
GOOFISH_BRIDGE_HOST=0.0.0.0
GOOFISH_BRIDGE_PORT=8787

MAX_REPLY_CHARS=80
GOOFISH_SEND_TIMEOUT_SECONDS=30
GOOFISH_AUTH_STATUS_TIMEOUT_SECONDS=15

BRIDGE_AUTH_TOKEN=replace-with-strong-random-token

AUTOREPLY_STATE_FILE=/app/data/autoreply-state.json
AUTOREPLY_RUNTIME_STATE_FILE=/app/data/autoreply-runtime-state.json
ITEMS_SNAPSHOT_PATH=/app/data/items_snapshot.json

OPENCLAW_RUNTIME_MODE=openai_chat
OPENCLAW_CHAT_COMPLETIONS_URL=http://host.docker.internal:18789/v1/chat/completions
OPENCLAW_GATEWAY_TOKEN=replace-with-token
# OPENCLAW_MODEL 是 OpenClaw Gateway 路由名/兼容字段，不是 bridge 直配底层模型。
OPENCLAW_MODEL=openclaw/default
OPENCLAW_TIMEOUT_SECONDS=20

HANDOFF_NOTIFY_WEBHOOK_URL=
```

## `/autoreply/decide` Input

支持直接 payload：

```json
{
  "cid": "...",
  "send_user_id": "...",
  "send_message": "...",
  "message_id": "optional",
  "content_type": 1,
  "dry_run": true,
  "cooldown_seconds": 15,
  "max_reply_chars": 80,
  "item_id": "optional",
  "image_url": "optional"
}
```

也支持 webhook wrapper：

```json
{
  "body": { "...": "..." },
  "headers": {},
  "query": {},
  "params": {}
}
```

## `/autoreply/decide` Flow

1. 入站归一化（text 为主；image 字段透传用于后续扩展）。
2. 必填字段校验。
3. 自动客服状态检查。
4. dedup（优先 `message_id`，否则 `cid+send_user_id+send_message+time_bucket`，TTL 默认 600 秒）。
5. cooldown（按 `cid`，返回 `remaining_seconds`）。
6. 转人工门控。
7. item_context 精简读取（指定 item 或最多 3 条在售，字段最小化）。
8. 调用 OpenClaw Gateway。
9. 归一化 OpenClaw 返回（要求 `choices[0].message.content` JSON）。
10. fail-closed 决策（非 JSON/HTML/空回复/外联词/推理泄露/异常文本等均不发送并转人工）。

## `/autoreply/decide` Output (core fields)

```json
{
  "ok": true,
  "send": false,
  "reason": "dry_run",
  "route_reason": "dry_run",
  "cid": "...",
  "send_user_id": "...",
  "send_message": "...",
  "dry_run": true,
  "handoff": false,
  "handoff_reason": "",
  "should_send": true,
  "final_reply": "...",
  "reply_source": "choices[0].message.content",
  "risk": "low",
  "item_context_status": "available",
  "item_context_reason": "top_selling",
  "openai_http_status": 200,
  "openai_response": {},
  "openclaw_output": {},
  "error": ""
}
```

## `/send` Final Guard

`safe_mode=true` 时会拦截：

- 外联词（微信/QQ/支付宝/银行卡/转账/私聊/加我/线下/电话/手机号/vx/wechat）
- 异常文本（空文本、标点文本、traceback/exception、`<think>`/reasoning/analysis、`undefined|null|nan` 独立词）
- 全局发送间隔（`global_send_interval_seconds`）

命中时 fail-closed：

```json
{
  "ok": false,
  "sent": false,
  "reason": "external_contact_blocked",
  "cid": "123",
  "toid": "456",
  "exit_code": 4,
  "stdout": "",
  "stderr": "external contact keyword blocked: 微信"
}
```

## Local Run

```bash
pip install -r requirements.txt
uvicorn app:APP --host 0.0.0.0 --port 8787
```
