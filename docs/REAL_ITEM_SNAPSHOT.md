# Real Item Snapshot Refresh

## Goal

Generate and refresh the current account item snapshot for auto-reply context:

- refresh source: current logged-in account personal page (read-only)
- snapshot file in container: `/app/data/items_snapshot.json`
- default host mapping in this repo: `./data/items_snapshot.json`

## Start services (without watcher)

```powershell
Copy-Item docker-compose.example.yml docker-compose.yml
Copy-Item .env.example .env
docker compose build goofish-bridge openclaw
docker compose up -d n8n goofish-bridge openclaw
```

## Refresh snapshot (preferred)

```powershell
python scripts/refresh_items_snapshot.py --base-url http://127.0.0.1:8787
```

If host HTTP stack has issues (for example `WinError 10106`), call through container:

```powershell
python scripts/refresh_items_snapshot.py --via-container
```

The script only calls:

- `GET /items/selling?refresh=true`
- `GET /items/snapshot`

It does not call `/send` and does not send buyer messages.

## Direct API refresh

```powershell
curl.exe "http://127.0.0.1:8787/items/selling?refresh=true" -H "X-Bridge-Token: $env:BRIDGE_AUTH_TOKEN"
curl.exe "http://127.0.0.1:8787/items/snapshot" -H "X-Bridge-Token: $env:BRIDGE_AUTH_TOKEN"
```

Optional explicit refresh endpoint:

```powershell
curl.exe -X POST "http://127.0.0.1:8787/items/snapshot/refresh" -H "X-Bridge-Token: $env:BRIDGE_AUTH_TOKEN"
```

## How bridge uses snapshot

`goofish-bridge /autoreply/decide` 会读取本地快照文件并生成最小 item_context：

- 指定 `item_id` 时只传对应商品
- 未指定时最多传 3 条在售商品
- 字段仅包含 `item_id/title/price/status/status_label`

## Common errors

- `missing_cookie`
  - bridge runtime cannot find Goofish cookie state in `/root/.goofish-cli`.
- `not_logged_in`
  - cookie exists but personal page still not logged in.
- `playwright_browser_missing`
  - chromium binary missing in bridge runtime.
- `playwright_runtime_dependency_missing`
  - OS dependencies for playwright browser are missing.
- `section_tab_not_found`
  - requested section tab not visible on current page.

## Risk boundary

- Refresh is read-only item collection.
- Do not use this step to send messages.
- `/send` safety gate remains unchanged.
