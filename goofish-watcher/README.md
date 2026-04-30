# goofish-watcher

This component is the MVP message forwarder.

It does not generate replies. It only:

1. Runs `goofish message watch`.
2. Reads JSONL messages from stdout.
3. Filters `event=message`.
4. Sends the payload to n8n webhook.
5. Writes dead-letter JSONL when forwarding fails (no auto replay).

## Environment

```env
N8N_WEBHOOK_URL=http://n8n:5678/webhook/goofish-inbound
WATCHER_FAILED_EVENTS_PATH=logs/failed_events.jsonl
```

Dead-letter rows include:

- `timestamp`
- `cid`
- `send_user_id`
- `event` (sanitized summary)
- `error` (redacted)

Replay helper (dry-run only, never sends):

```bash
python ../scripts/replay_failed_events.py --path logs/failed_events.jsonl --tail
```

## Local run

```bash
python watcher.py
```

## Docker run

Use the root `docker-compose.example.yml` as the starting point.
