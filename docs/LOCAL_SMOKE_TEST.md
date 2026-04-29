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

# 2) Prepare compose file from example
Copy-Item docker-compose.example.yml docker-compose.yml

# 3) Prepare a local fake item snapshot (no network, no cookie read)
python scripts/write_test_items_snapshot.py

# 4) Build bridge + mock OpenClaw images
docker compose build goofish-bridge openclaw

# 5) Start required services for dry-run chain (watcher is not started)
docker compose up -d n8n goofish-bridge openclaw

# 6) Quick health probe
curl http://127.0.0.1:8787/health

# 7) Snapshot probe used by n8n item_context chain
curl http://127.0.0.1:8787/items/snapshot

# 8) Run smoke checker (no /send)
python scripts/smoke_bridge.py

# 9) Follow bridge logs
docker compose logs -f goofish-bridge

# 10) Stop and clean up containers
docker compose down
```
