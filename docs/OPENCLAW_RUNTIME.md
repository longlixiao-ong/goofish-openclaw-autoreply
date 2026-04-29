# OpenClaw Runtime Integration

本文件用于把项目从 `mock-openclaw` 开发验证模式，切换到真实 OpenClaw 运行模式。

边界不变：

- 不绕过 `goofish-bridge /send` 安全闸门
- 不启动 watcher 进行真实监听验证
- 不执行真实闲鱼发送

---

## 1) 两种运行模式

### A. 默认 mock 模式（本地开发）

- `.env` 使用：
  - `OPENCLAW_REPLY_URL=http://openclaw:18789/reply`
- 启动服务（不启动 watcher）：

```powershell
docker compose up -d n8n goofish-bridge openclaw
```

### B. 真实 OpenClaw 模式（联调/上线前）

1. 修改 `.env`：

```text
OPENCLAW_REPLY_URL=http://<real-openclaw-host>:<port>/reply
# 或 https://<real-openclaw-domain>/reply
```

2. 启动服务（不启动 watcher，且不启动 mock `openclaw`）：

```powershell
docker compose up -d n8n goofish-bridge
```

说明：`docker-compose.example.yml` 仍保留 `openclaw` mock 服务，便于快速回滚。

---

## 2) 请求协议（n8n -> OpenClaw）

当前 inbound workflow 会向 `OPENCLAW_REPLY_URL` 发送至少以下字段：

- `cid`
- `toid`
- `message`
- `risk`
- `risk_reason`
- `route_reason`
- `handoff`
- `handoff_reason`
- `dry_run`
- `item_context`
- `item_context_status`
- `item_context_reason`
- `customer_service_policy`

其中 `customer_service_policy` 表达：

- 当前模式为 `handoff_gate`
- 默认允许自动回复
- 命中转人工或异常需阻断发送
- 所有发送必须经过 bridge `/send`

---

## 3) 响应协议（OpenClaw -> n8n）

建议格式：

```json
{
  "reply": "买家可见回复",
  "should_send": true,
  "handoff": false,
  "reason": "normal_presale"
}
```

需转人工：

```json
{
  "reply": "",
  "should_send": false,
  "handoff": true,
  "reason": "needs_human"
}
```

n8n 已兼容多种返回形态（如 `data.*`、`result.*`、`output.*`、`send`/`shouldSend` 别名等），并归一化到：

- `reply`
- `should_send`
- `handoff`
- `handoff_reason` / `openclaw_reason`
- `openclaw_response`

---

## 4) Fail-Closed 发送门控（必须）

即使 OpenClaw 可达，也只有在以下条件同时满足时才会进入 `/send`：

- `handoff != true`
- `should_send != false`
- `final_reply` 非空
- OpenClaw 调用无系统异常

否则全部 `send=false` 结束。

---

## 5) dry-run 验证（不发送）

### 5.1 只测 OpenClaw 协议（推荐先做）

```powershell
python scripts/test_openclaw_reply.py --url "<OPENCLAW_REPLY_URL>"
```

该脚本只请求 OpenClaw `/reply`，不调用 `/send`，输出：

- 原始响应
- 归一化后的 `reply/should_send/handoff/reason`
- 协议兼容性检查结果

### 5.2 走 n8n 入站链路 dry-run（仍不发送）

向 n8n webhook 发送 `dry_run=true` 入站消息，检查返回中是否包含：

- `openclaw_response`
- `should_send`
- `handoff`
- `handoff_reason`（或 `openclaw_reason`）
- `final_reply`
- `send=false`

---

## 6) 回滚到 mock 模式

1. 恢复 `.env`：

```text
OPENCLAW_REPLY_URL=http://openclaw:18789/reply
```

2. 重新启动 mock 服务（仍不启动 watcher）：

```powershell
docker compose up -d n8n goofish-bridge openclaw
```

3. 运行协议测试确认：

```powershell
python scripts/test_openclaw_reply.py --url "http://127.0.0.1:18789/reply"
```

---

## 7) 常见问题

- `OPENCLAW_REPLY_URL` 指向真实地址后返回超时/连接失败：
  - 检查网络可达性、TLS/证书、端口与路径是否正确（必须是 `/reply`）。
- 真实 OpenClaw 返回字段不一致：
  - 优先补齐 `reply/should_send/handoff/reason`；
  - 或保持现有字段但确保能被 workflow 归一化识别。
- dry-run 能通但非 dry-run 不发送：
  - 检查是否命中门控（`handoff=true`、`should_send=false`、空回复、异常）。
