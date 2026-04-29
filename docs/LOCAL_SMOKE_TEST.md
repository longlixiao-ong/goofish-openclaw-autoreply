# Local Smoke Test

This smoke test validates local `goofish-bridge` endpoint availability only.

It **does not** call `/send` and **does not** perform real Goofish/Xianyu message sending.
It will restore the original `autoreply` enabled state before exit.
If initial state cannot be identified, it restores to `stop/off` for safety.

## Steps

```powershell
# 1) Prepare local env file
Copy-Item .env.example .env

# Optional but recommended: keep snapshot path aligned with compose mount
# ITEMS_SNAPSHOT_PATH=/app/data/items_snapshot.json

# Optional mode switch:
# - mock mode (default): OPENCLAW_REPLY_URL=http://openclaw:18789/reply
# - real mode: set OPENCLAW_REPLY_URL to real OpenClaw /reply URL

# 2) Prepare compose file from example
Copy-Item docker-compose.example.yml docker-compose.yml

# 3) Build bridge + mock OpenClaw images (only required for mock mode)
docker compose build goofish-bridge openclaw

# 4A) Start services for mock mode dry-run chain (watcher is not started)
docker compose up -d n8n goofish-bridge openclaw

# 4B) Start services for real OpenClaw mode (do not start local openclaw mock)
# docker compose up -d n8n goofish-bridge

# 5) Quick health probe
curl http://127.0.0.1:8787/health

# 5.1) OpenClaw protocol probe only (never calls /send)
# mock mode:
python scripts/test_openclaw_reply.py --url http://127.0.0.1:18789/reply
# real mode:
# python scripts/test_openclaw_reply.py --url "<YOUR_REAL_OPENCLAW_REPLY_URL>"

# 6A) Preferred: refresh real snapshot from current logged-in account (read-only)
python scripts/refresh_items_snapshot.py --base-url http://127.0.0.1:8787

# If host HTTP stack is broken, run refresh through container:
# python scripts/refresh_items_snapshot.py --via-container

# 6B) Fallback: if no local login state is available, use a fake snapshot fixture
# python scripts/write_test_items_snapshot.py

# 7) Snapshot probe used by n8n item_context chain
curl http://127.0.0.1:8787/items/snapshot

# 8) Run smoke checker (no /send)
python scripts/smoke_bridge.py

# 9) Follow bridge logs
docker compose logs -f goofish-bridge

# 10) Stop and clean up containers
docker compose down
```
