# PRODUCTION RUNBOOK

## 1) 正式运行架构

```text
goofish-watcher
  -> n8n /webhook/goofish-inbound
  -> goofish-bridge /autoreply/decide
  -> (send=true) goofish-bridge /send
  -> goofish-cli message send
```

职责边界：

- n8n：只编排，不承载核心业务判断。
- goofish-bridge：核心决策与最终安全闸门。
- OpenAI-compatible runtime（New API/OpenClaw Gateway）：回复决策模型层。

## 2) .env 配置模板

参考 `.env.example`，正式至少配置：

- `BRIDGE_AUTH_TOKEN`
- `OPENCLAW_RUNTIME_MODE=openai_chat`
- `OPENCLAW_CHAT_COMPLETIONS_URL`
- `OPENCLAW_GATEWAY_TOKEN`
- `OPENCLAW_MODEL`
- `OPENCLAW_TIMEOUT_SECONDS`
- `AUTOREPLY_STATE_FILE`
- `AUTOREPLY_RUNTIME_STATE_FILE`
- `ITEMS_SNAPSHOT_PATH`

## 3) New API 3000 配置方式

若你的 New API/OpenAI-compatible 服务在本机 `3000`：

```env
OPENCLAW_RUNTIME_MODE=openai_chat
OPENCLAW_CHAT_COMPLETIONS_URL=http://host.docker.internal:3000/v1/chat/completions
OPENCLAW_MODEL=openclaw/default
OPENCLAW_GATEWAY_TOKEN=replace-with-real-token
```

## 4) BRIDGE_AUTH_TOKEN 配置

设置后必须通过 header 鉴权：

```text
X-Bridge-Token: <BRIDGE_AUTH_TOKEN>
```

至少受保护：

- `/autoreply/decide`
- `/send`

## 5) 启动命令

```bash
cp .env.example .env
# 填好 .env 后
docker compose -f docker-compose.example.yml up -d n8n goofish-bridge goofish-watcher openclaw
```

## 6) 导入 n8n workflow

导入：

- `n8n/workflows/goofish-inbound.example.json`

确认链路为：

- `Webhook -> /autoreply/decide -> IF 应发送 -> /send`

## 7) dry-run 验证

推荐先跑：

```bash
python scripts/production_preflight.py
```

重点看：

- `decide_dry_run_presale`：`send=false`
- `decide_dry_run_handoff`：`handoff=true` 且 `send=false`
- `dry_run_never_send`：dry-run 永不触发 `/send`

## 8) 正式开启自动回复

先确认 dry-run 稳定后：

1. `POST /autoreply/start`
2. 将入站请求 `dry_run=false`
3. 继续观察 `/autoreply/decide` 返回与 `/send` 拦截日志

## 9) 停止自动回复

```text
POST /autoreply/stop
```

## 10) 转人工通知配置

配置：

```env
HANDOFF_NOTIFY_WEBHOOK_URL=https://your-webhook.example/path
```

命中转人工关键词后，bridge 会发送通知；通知失败不会触发自动发送，且会记录 `handoff_notify_error`。

## 11) 故障排查

优先检查：

1. `GET /health`
2. `GET /autoreply/status`
3. `/autoreply/decide` 返回中的：
   - `reason`
   - `error`
   - `openai_http_status`
   - `openai_response`
4. `AUTOREPLY_STATE_FILE` 与 `AUTOREPLY_RUNTIME_STATE_FILE` 是否可写
5. `OPENCLAW_CHAT_COMPLETIONS_URL`、token、模型名是否有效

## 12) 回滚到 mock-openclaw

1. 保持 `OPENCLAW_RUNTIME_MODE=openai_chat`
2. 改为本地 mock endpoint：

```env
OPENCLAW_CHAT_COMPLETIONS_URL=http://openclaw:18789/v1/chat/completions
```

3. 重新执行 dry-run 验证，确认闭环正常后再继续。

## 13) 风险边界（必须遵守）

- 不绕开平台机制，不绕控。
- 不发送外部联系方式。
- 转人工问题不自动回复。
- 真实发送必须经过 `/send` 安全闸门。
