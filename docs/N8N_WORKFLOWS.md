# n8n Workflows

This document defines the first workflow set.

## Workflow 1: inbound Goofish message

Endpoint:

```text
POST /webhook/goofish-inbound
```

Expected input:

```json
{
  "event": "message",
  "cid": "60585751957",
  "send_user_id": "2215266653893",
  "send_user_name": "buyer",
  "send_message": "还在吗",
  "content_type": 1,
  "image_url": ""
}
```

Steps:

1. Normalize inbound webhook payload:
   - If Webhook output is wrapped as `{ body, headers, query, params }` and `body` is an object, use `body`.
   - If `body` is a JSON string, parse it as payload.
   - Otherwise use top-level payload.
   - Keep `headers/query/params` in `webhook_meta`; normalize core fields to top-level (`cid`, `send_user_id`, `send_message`, `dry_run`, etc.).
2. Validate payload.
3. De-duplicate by `cid`, `send_user_id`, message text, optional message id and timestamp.
4. Dry-run requests skip dedup persistence (`dedup_skipped=true`) to avoid polluting dedup store.
5. Duplicate route guard only triggers when `is_duplicate=true` and `dry_run` is not truthy (`true/"true"/1/"1"`).
6. Check auto-reply state.
7. Apply conversation cooldown.
8. Run handoff gate classification (refund/after-sale/complaint/legal/off-platform/contact/payment/shipping/order disputes, threats/abuse, etc.).
9. If handoff gate hits: `handoff=true`, stop before OpenClaw and before `/send`.
10. If handoff gate does not hit: read `GET /items/snapshot`, attach `item_context`, then call OpenClaw.
11. Normalize OpenClaw response contract: `reply`, `should_send`, `handoff`, `reason`.
12. Sanitize reply and run external-contact scan.
13. Send gate (fail closed): block send on `handoff=true`, `should_send=false`, empty reply, or any system exception.
14. Non-dry-run send path must go through `POST /send` only.

OpenClaw runtime mode selection:

- `OPENCLAW_RUNTIME_MODE=custom_reply`
  - Calls `OPENCLAW_REPLY_URL`
  - Sends custom payload (`cid/toid/message/item_context/customer_service_policy/...`)
- `OPENCLAW_RUNTIME_MODE=openai_chat`
  - Calls `OPENCLAW_CHAT_COMPLETIONS_URL`
  - Sends OpenAI Chat Completions payload
  - Adds `Authorization: Bearer {{$env.OPENCLAW_GATEWAY_TOKEN}}`

OpenClaw response compatibility notes:

- Preferred fields: `reply`, `should_send`, `handoff`, `reason`
- Also normalized from common variants such as:
  - `send` / `shouldSend`
  - nested `data.*`, `result.*`, `output.*`
  - `final_reply`, `answer`, `choices[0].message.content`
- HTTP failure and transport errors are treated as `system_exception` and will not be sent.

Dry-run output (always `send=false`) includes:

- `handoff`
- `handoff_reason`
- `route_reason`
- `item_context_status`
- `item_context_reason`
- `openclaw_response`
- `should_send`
- `final_reply`

## Workflow 2: auto-reply switch

Endpoints:

```text
POST /webhook/goofish-autoreply/start
POST /webhook/goofish-autoreply/stop
GET  /webhook/goofish-autoreply/status
```

State:

```json
{
  "enabled": false,
  "mode": "auto",
  "safe_mode": true,
  "auto_send": true,
  "cooldown_seconds": 15,
  "global_send_interval_seconds": 30,
  "max_reply_chars": 80
}
```

## Workflow 3: health check

Schedule: every 5 minutes.

Checks:

- `goofish auth status`
- n8n workflow state
- OpenClaw endpoint availability
- watcher heartbeat
- recent send failures
- risk-control errors

Failure action:

- Disable auto-reply.
- Notify the owner.
- Preserve logs for review.
