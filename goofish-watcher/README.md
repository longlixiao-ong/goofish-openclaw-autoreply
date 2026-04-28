# goofish-watcher

This component is the MVP message forwarder.

It does not generate replies. It only:

1. Runs `goofish message watch`.
2. Reads JSONL messages from stdout.
3. Filters `event=message`.
4. Sends the payload to n8n webhook.

## Environment

```env
N8N_WEBHOOK_URL=http://n8n:5678/webhook/goofish-inbound
```

## Local run

```bash
python watcher.py
```

## Docker run

Use the root `docker-compose.example.yml` as the starting point.
