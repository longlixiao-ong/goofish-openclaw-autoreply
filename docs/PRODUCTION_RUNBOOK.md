# PRODUCTION RUNBOOK

## 1) 生产架构（主控边界）

```text
goofish-watcher
  -> n8n /webhook/goofish-inbound
  -> goofish-bridge /autoreply/decide
  -> (send=true && dry_run=false) goofish-bridge /send
  -> goofish-cli message send
```

职责边界：

- OpenClaw：唯一 AI 大脑（模型、Prompt、记忆、视觉、回复策略、议价策略）。
- goofish-bridge：安全网关（鉴权、状态检查、dedup、cooldown、handoff gate、item_context 精简、OpenClaw 调用与归一化、fail-closed、最终 `/send` 闸门）。
- n8n：编排层（Webhook 接入、调用 bridge、按 `send/dry_run` 路由）。
- goofish-cli：闲鱼操作层（watch/send/item/media/auth）。

## 2) 必填环境变量

参考 `.env.example`，正式至少配置：

- `BRIDGE_AUTH_TOKEN`
- `OPENCLAW_RUNTIME_MODE=openai_chat`
- `OPENCLAW_CHAT_COMPLETIONS_URL`
- `OPENCLAW_GATEWAY_TOKEN`
- `OPENCLAW_MODEL`（OpenClaw Gateway 路由名/兼容字段）
- `OPENCLAW_TIMEOUT_SECONDS`
- `AUTOREPLY_STATE_FILE`
- `AUTOREPLY_RUNTIME_STATE_FILE`
- `ITEMS_SNAPSHOT_PATH`

重要说明：

- bridge 不直配底层模型供应商。
- 底层模型由 OpenClaw / New API 后台管理。

## 3) 接口鉴权

配置 `BRIDGE_AUTH_TOKEN` 后，除 `/health` 外，所有 bridge 接口都必须带：

```text
X-Bridge-Token: <BRIDGE_AUTH_TOKEN>
```

`/health` 无鉴权，且只返回最小存活信息，不泄露敏感状态。

## 4) 启动

```bash
cp .env.example .env
# 填好 .env 后
docker compose -f docker-compose.example.yml up -d n8n goofish-bridge goofish-watcher openclaw
```

## 5) 导入 n8n workflow

导入：

- `n8n/workflows/goofish-inbound.example.json`

必须确认链路为：

- `Webhook -> /autoreply/decide -> IF 应发送 -> (/send 或 不发送结束)`

且 IF 条件必须满足：

- `send=true && dry_run=false` 才允许走 `/send`
- `dry_run=true` 永远走“不发送结束”

## 6) dry-run 验证（先做）

```bash
python scripts/production_preflight.py
```

`decide_dry_run_presale` 必须同时满足：

- HTTP 200
- `dry_run=true`
- `send=false`
- `reason=="dry_run"`
- `final_reply` 非空
- `reply_source!="none"`
- `openai_http_status==200`
- 不调用 `/send`

`decide_dry_run_handoff` 必须同时满足：

- HTTP 200
- `handoff=true`
- `send=false`
- `reason/route_reason/handoff_reason` 能说明 handoff
- 不调用 `/send`

## 7) 开启/停止自动客服

开启：

```bash
curl -X POST http://127.0.0.1:8787/autoreply/start \
  -H "X-Bridge-Token: ${BRIDGE_AUTH_TOKEN}"
```

状态：

```bash
curl http://127.0.0.1:8787/autoreply/status \
  -H "X-Bridge-Token: ${BRIDGE_AUTH_TOKEN}"
```

停止：

```bash
curl -X POST http://127.0.0.1:8787/autoreply/stop \
  -H "X-Bridge-Token: ${BRIDGE_AUTH_TOKEN}"
```

## 8) fail-closed 与 handoff 通知

以下场景必须 fail-closed（不发送）并转人工：

- 关键词命中转人工
- OpenClaw 返回 `handoff=true`
- OpenClaw 请求失败
- OpenClaw 空回复
- OpenClaw 异常文本
- OpenClaw 回复含外联词
- `invalid_request`
- `system_exception`

通知配置：

```env
HANDOFF_NOTIFY_WEBHOOK_URL=https://your-webhook.example/path
```

规则：

- 未配置 webhook 时不报错。
- 通知失败仅记录 `handoff_notify_error`，绝不因此放行发送。

## 9) 运营合规边界（必须遵守）

- 不绕过平台风控。
- 不伪造设备。
- 不自动处理滑块。
- 不引导站外交易。
- 不提交 Cookie/Token/API Key/.env 到仓库。
