# goofish-bridge

`goofish-bridge` is the production runtime core for inbound autoreply decision and final send guard.

Responsibilities:

- `/autoreply/decide`: normalize inbound payload, state checks, dedup, cooldown, handoff gate, item context, OpenAI-compatible runtime call, fail-closed decision.
- `/send`: final send guard and `goofish message send` execution.
- `/autoreply/start|stop|status`: runtime switch.
- `/items/snapshot*`: item context source.

`goofish-bridge` does not bypass `goofish-cli` and does not bypass platform controls.

## Endpoints

```text
GET  /health
GET  /status
GET  /items/snapshot
GET  /items/selling
POST /items/snapshot/refresh

GET  /autoreply/status
POST /autoreply/start
POST /autoreply/stop
POST /autoreply/decide

POST /send
```

## Auth

If `BRIDGE_AUTH_TOKEN` is configured, these endpoints require:

```text
X-Bridge-Token: <BRIDGE_AUTH_TOKEN>
```

Required at minimum:

- `/autoreply/decide`
- `/send`

Current implementation also protects `/autoreply/start|stop|status`.

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
OPENCLAW_MODEL=openclaw/default
OPENCLAW_TIMEOUT_SECONDS=20

HANDOFF_NOTIFY_WEBHOOK_URL=
```

## Runtime State

- `AUTOREPLY_STATE_FILE`: switch and guard config (`enabled`, `auto_send`, `safe_mode`, etc.).
- `AUTOREPLY_RUNTIME_STATE_FILE`: dedup/cooldown/runtime counters:
  - `dedup_order` / `dedup_map`
  - `cooldown_store`
  - `last_success_send_at`

Writes are atomic (`.tmp` + `os.replace`) with lock protection.

## `/autoreply/decide` input

Supports direct payload:

```json
{
  "cid": "...",
  "send_user_id": "...",
  "send_message": "...",
  "content_type": 1,
  "dry_run": true,
  "cooldown_seconds": 15,
  "max_reply_chars": 120,
  "item_id": "optional"
}
```

Also supports webhook wrapper:

```json
{
  "body": { "...": "..." },
  "headers": {},
  "query": {},
  "params": {}
}
```

## `/autoreply/decide` output (core fields)

```json
{
  "ok": true,
  "send": false,
  "reason": "dry_run",
  "cid": "...",
  "send_user_id": "...",
  "send_message": "...",
  "dry_run": true,
  "handoff": false,
  "handoff_reason": "",
  "should_send": true,
  "final_reply": "...",
  "reply_source": "...",
  "item_context_status": "available",
  "item_context_reason": "",
  "openai_runtime_url": "...",
  "openai_model": "openclaw/default",
  "openai_response": {},
  "error": ""
}
```

## `/send` final guard

When `safe_mode=true`, `/send` blocks:

- external contact words (微信/QQ/支付宝/银行卡/转账/私聊/加我/线下/电话/手机号/vx/v信/wechat)
- abnormal text (empty/punctuation-only/traceback/exception/reasoning leak)
- global interval (`global_send_interval_seconds`)

`/send` always fail-closed and returns structured failure:

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

## Local run

```bash
pip install -r requirements.txt
uvicorn app:APP --host 0.0.0.0 --port 8787
```
