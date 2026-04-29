# goofish-bridge

Long-running HTTP service for n8n to call goofish-cli send and auto-reply switch.

This service does not bypass goofish-cli limits or platform controls. It only wraps the official CLI calls:

- `goofish message send`
- `goofish auth status`

## Endpoints

```text
GET  /health
GET  /status
GET  /items/snapshot
GET  /items/selling
POST /items/snapshot/refresh
POST /send
POST /autoreply/start
POST /autoreply/stop
GET  /autoreply/status
```

## Environment

```env
GOOFISH_BRIDGE_HOST=0.0.0.0
GOOFISH_BRIDGE_PORT=8787
MAX_REPLY_CHARS=80
AUTOREPLY_STATE_FILE=/app/data/autoreply-state.json
ITEMS_SNAPSHOT_PATH=/app/data/items_snapshot.json
GOOFISH_SEND_TIMEOUT_SECONDS=30
GOOFISH_AUTH_STATUS_TIMEOUT_SECONDS=15
```

## Local run

```bash
pip install -r requirements.txt
uvicorn app:APP --host 0.0.0.0 --port 8787
```

## Send API example

```bash
curl -X POST http://localhost:8787/send \
  -H "Content-Type: application/json" \
  -d '{"cid":"123","toid":"456","text":"在的，喜欢可拍"}'
```

Response shape:

```json
{
  "ok": true,
  "cid": "123",
  "toid": "456",
  "exit_code": 0,
  "stdout": "...",
  "stderr": ""
}
```

## Read-only item snapshot refresh

```bash
# refresh from current logged-in account (read-only collection)
curl "http://localhost:8787/items/selling?refresh=true"

# or explicit refresh endpoint
curl -X POST "http://localhost:8787/items/snapshot/refresh"

# read current snapshot used by n8n item_context chain
curl "http://localhost:8787/items/snapshot"
```
