# OpenClaw Runtime Integration

本项目的 bridge 运行时只支持：

- `OPENCLAW_RUNTIME_MODE=openai_chat`
- OpenClaw Gateway OpenAI-compatible `/v1/chat/completions`

不再维护 `custom_reply` 兼容路径。

## 1) 运行时配置

`.env` 示例：

```text
OPENCLAW_RUNTIME_MODE=openai_chat
OPENCLAW_CHAT_COMPLETIONS_URL=http://host.docker.internal:18789/v1/chat/completions
OPENCLAW_GATEWAY_TOKEN=<不要提交真实值>
OPENCLAW_MODEL=openclaw/default
OPENCLAW_TIMEOUT_SECONDS=20
```

说明：

- `OPENCLAW_MODEL` 是 OpenClaw Gateway 路由名/兼容字段。
- bridge 不管理底层模型供应商，底层模型由 OpenClaw / New API 后台管理。

## 2) Bridge -> OpenClaw 请求结构

bridge 发送给 OpenClaw 的核心上下文：

- `buyer_message`
- `cid`
- `send_user_id`
- `content_type`
- `item_context`（最小化字段）
- `conversation_state`（简要）
- `customer_service_policy`
- `bridge_guardrails`
- `dry_run`
- `max_reply_chars`

bridge 不注入复杂客服策略 Prompt；复杂策略由 OpenClaw 侧统一管理。

## 3) OpenClaw 返回归一化要求

bridge 要求：

- 使用 OpenAI-compatible `choices[0].message.content`
- `content` 必须是 JSON 对象
- 标准字段：

```json
{
  "reply": "...",
  "should_send": true,
  "handoff": false,
  "reason": "...",
  "risk": "low"
}
```

若命中以下任一情况，bridge 必须 fail-closed（不发送并转人工）：

- 非 JSON
- HTML/错误页
- 空回复
- 外联词
- `<think>`/reasoning 泄露
- 异常文本（traceback/exception/`undefined|null|nan` 独立词）

## 4) dry-run 验证（不发送）

协议脚本：

```powershell
python scripts/test_openclaw_reply.py --url http://host.docker.internal:18789/v1/chat/completions --token "<TOKEN>" --model openclaw/default
python scripts/test_openclaw_reply.py --self-check
```

全链路预检：

```powershell
python scripts/production_preflight.py
```

关键检查点：

- presale dry-run：`reason=dry_run`、`final_reply` 非空、`reply_source!=none`、`openai_http_status=200`、`send=false`
- handoff dry-run：`handoff=true`、`send=false`、reason/route_reason/handoff_reason 可解释

## 5) 边界

- 不绕过 `/send` 安全闸门。
- 不执行真实闲鱼发送进行 runtime 协议测试。
