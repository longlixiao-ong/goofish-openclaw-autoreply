# OpenClaw Runtime Integration

本文件说明如何在 n8n inbound workflow 中切换两种 OpenClaw runtime：

- `custom_reply`（兼容 mock-openclaw / 自定义 `/reply`）
- `openai_chat`（官方 OpenClaw Gateway `/v1/chat/completions`）

边界不变：

- 不绕过 `goofish-bridge /send` 安全闸门
- 不启动 watcher 做真实监听验证
- 不执行真实闲鱼发送

---

## 1) 环境变量

在 `.env` 配置：

```text
OPENCLAW_RUNTIME_MODE=custom_reply
OPENCLAW_REPLY_URL=http://openclaw:18789/reply
OPENCLAW_CHAT_COMPLETIONS_URL=http://host.docker.internal:18789/v1/chat/completions
OPENCLAW_GATEWAY_TOKEN=<不要提交真实值>
OPENCLAW_MODEL=openclaw/default
```

说明：

- `OPENCLAW_RUNTIME_MODE=custom_reply` 时使用 `OPENCLAW_REPLY_URL`
- `OPENCLAW_RUNTIME_MODE=openai_chat` 时使用 `OPENCLAW_CHAT_COMPLETIONS_URL`
- `OPENCLAW_GATEWAY_TOKEN` 只用于 `openai_chat`，通过 `Authorization: Bearer <token>` 发送

---

## 2) runtime 模式

### A. `custom_reply`（默认本地开发）

- 请求 URL：`OPENCLAW_REPLY_URL`
- 请求体：当前自定义 JSON（`cid/toid/message/item_context/customer_service_policy/...`）
- 兼容 mock-openclaw，不破坏本地开发链路

### B. `openai_chat`（官方 Gateway）

- 请求 URL：`OPENCLAW_CHAT_COMPLETIONS_URL`
- Header：`Authorization: Bearer <OPENCLAW_GATEWAY_TOKEN>`
- 请求体（OpenAI Chat Completions）：

```json
{
  "model": "openclaw/default",
  "messages": [
    {"role": "system", "content": "...客服策略..."},
    {"role": "user", "content": "...买家消息 + item_context JSON..."}
  ],
  "user": "<cid或send_user_id>"
}
```

---

## 3) 响应解析与兼容

workflow 归一化节点支持：

- `reply` / `text` / `message` / `content`
- `choices[0].message.content`
- 嵌套对象：`data.*` / `result.*` / `output.*`
- 发送布尔别名：`should_send` / `shouldSend` / `send`
- 转人工别名：`handoff` / `needs_handoff` / `needs_human` 等

对于 `openai_chat`：

- 若 `choices[0].message.content` 本身是 JSON 字符串，会二次解析提取 `reply/should_send/handoff/reason`

---

## 4) Fail-Closed 行为（必须）

以下任一命中都不会进入 `/send`：

- `handoff=true`
- `should_send=false`
- `reply` 为空
- OpenClaw HTTP 调用失败
- OpenClaw 返回错误对象（如 unauthorized/forbidden/internal error）
- `openai_chat` 返回 HTML 内容

因此 401/403/500/HTML 响应都 fail-closed。

---

## 5) dry-run 验证（不发送）

### 5.1 协议脚本验证

`scripts/test_openclaw_reply.py` 同时支持两种模式：

```powershell
# custom_reply
python scripts/test_openclaw_reply.py --mode custom_reply --url http://127.0.0.1:18789/reply

# openai_chat
python scripts/test_openclaw_reply.py --mode openai_chat --url http://host.docker.internal:18789/v1/chat/completions --token "<TOKEN>" --model openclaw/default
```

离线校验（不发 HTTP）：

```powershell
python scripts/test_openclaw_reply.py --self-check
```

离线校验覆盖：

- custom_reply 请求格式
- openai_chat 请求格式
- `choices[0].message.content` 解析
- Unauthorized/HTML/error fail-closed

### 5.2 n8n 入站 dry-run

向 inbound webhook 发 `dry_run=true`，检查返回字段：

- `openclaw_response`
- `should_send`
- `handoff`
- `handoff_reason`（或 `openclaw_reason`）
- `final_reply`
- `send=false`

---

## 6) 切换与回滚

切到官方 Gateway（`openai_chat`）：

1. 设置 `OPENCLAW_RUNTIME_MODE=openai_chat`
2. 设置 `OPENCLAW_CHAT_COMPLETIONS_URL` 和 `OPENCLAW_GATEWAY_TOKEN`
3. 保持不启动 watcher

回滚到 mock：

1. 设置 `OPENCLAW_RUNTIME_MODE=custom_reply`
2. 设置 `OPENCLAW_REPLY_URL=http://openclaw:18789/reply`
3. 启动本地 `openclaw` mock 服务
