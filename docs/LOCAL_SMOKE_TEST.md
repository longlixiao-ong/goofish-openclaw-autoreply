# Local Smoke Test

This smoke test validates local `goofish-bridge` endpoint availability only.

It **does not** call `/send` and **does not** perform real Goofish/Xianyu message sending.

## Steps

```powershell
# 1) Prepare local env file
Copy-Item .env.example .env

# 2) Prepare compose file from example
Copy-Item docker-compose.example.yml docker-compose.yml

# 3) Build bridge image
docker compose build goofish-bridge

# 4) Start required services
docker compose up -d n8n goofish-bridge

# 5) Quick health probe
curl http://127.0.0.1:8787/health

# 6) Run smoke checker (no /send)
python scripts/smoke_bridge.py

# 7) Follow bridge logs
docker compose logs -f goofish-bridge

# 8) Stop and clean up containers
docker compose down
```
