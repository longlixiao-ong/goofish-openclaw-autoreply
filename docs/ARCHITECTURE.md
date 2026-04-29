# Architecture

## Goal

Use `goofish-cli` as the Goofish/Xianyu operation layer, `n8n` as the workflow orchestration layer, and `OpenClaw` as the only AI brain.

## MVP data flow

```text
Goofish buyer message
  -> goofish message watch
  -> goofish-watcher.py
  -> n8n webhook
  -> de-dup / cooldown / handoff gate
  -> (handoff hit) stop and handoff
  -> (default) item_context + OpenClaw Agent
  -> sanitize reply + send gate
  -> goofish-bridge /send
  -> Goofish buyer
```

## Stable data flow

```text
Goofish WebSocket
  -> goofish-bridge watch loop
  -> n8n webhook
  -> handoff gate (n8n)
  -> OpenClaw Agent (default path)
  -> n8n sanitize + should_send/handoff gate
  -> goofish-bridge /send
  -> Goofish WebSocket
```

## Core boundaries

- `goofish-cli`: login state, IM watch, IM send, item query, media upload, publish, built-in rate limit and circuit breaker.
- `n8n`: workflow orchestration, state switch, de-dup, cooldown, handoff gating, dry-run diagnostics, send decision.
- `OpenClaw`: model, image understanding, seller persona, bargain strategy, memory and final response decision.

## Non-goals

- Do not automate around platform safety checks.
- Do not operate unauthorized accounts.
- Do not support fraudulent trading behavior.
- Do not guide buyers to off-platform transactions.
- Do not expose cookies, API keys or session state.
