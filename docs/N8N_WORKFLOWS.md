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

1. Validate payload.
2. De-duplicate by `cid`, `send_user_id`, message text, optional message id and timestamp.
3. Check auto-reply state.
4. Apply conversation cooldown.
5. Classify risk.
6. If image exists, call OpenClaw vision flow.
7. Call OpenClaw reply flow.
8. Sanitize reply.
9. Scan external-contact and off-platform payment words.
10. Send or hand off.

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
