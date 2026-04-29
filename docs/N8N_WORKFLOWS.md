# n8n Workflows

## Workflow 1: inbound Goofish message (production)

Endpoint:

```text
POST /webhook/goofish-inbound
```

Chain:

```text
Webhook
  -> goofish-bridge /autoreply/decide
  -> IF 应发送
      true  -> goofish-bridge /send
      false -> 不发送结束
```

Rules:

1. n8n does not maintain dedup/cooldown/risk/OpenAI logic.
2. All business decisioning is centralized in `/autoreply/decide`.
3. `dry_run=true` must always route to `不发送结束` and never call `/send`.
4. Final send must go through `/send` only.
5. Bridge token header is required when `BRIDGE_AUTH_TOKEN` is configured:
   - `X-Bridge-Token: {{$env.BRIDGE_AUTH_TOKEN}}`

Bridge decision output expected fields include:

- `send`
- `reason`
- `dry_run`
- `handoff`
- `handoff_reason`
- `should_send`
- `final_reply`
- `item_context_status`
- `item_context_reason`
- `openai_response`
- `error`

## Workflow 2: auto-reply switch

Endpoints:

```text
POST /webhook/goofish-autoreply/start
POST /webhook/goofish-autoreply/stop
GET  /webhook/goofish-autoreply/status
```

These control bridge autoreply state and should forward `X-Bridge-Token` when auth is enabled.
