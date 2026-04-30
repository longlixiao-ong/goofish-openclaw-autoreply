# Architecture

## Goal

以 OpenClaw 为唯一 AI 主控，`goofish-cli` 为闲鱼操作层，`n8n` 为编排层，`goofish-bridge` 为安全网关。

## Production Flow

```text
Goofish buyer message
  -> goofish message watch
  -> goofish-watcher
  -> n8n inbound webhook
  -> goofish-bridge /autoreply/decide
  -> IF(send=true && dry_run=false)
      -> goofish-bridge /send
      -> goofish message send
```

## Responsibility Boundaries

- `OpenClaw`（唯一 AI 大脑）
  - 模型选择与路由
  - Prompt 与策略
  - 记忆与视觉理解
  - 小刀议价/客服回复策略

- `goofish-bridge`（安全网关）
  - `X-Bridge-Token` 鉴权
  - 入站归一化与字段校验
  - dedup（TTL）/cooldown
  - 转人工 gate
  - item_context 最小化读取与透传
  - OpenClaw 调用与结果归一化
  - fail-closed 决策
  - 最终 `/send` 安全闸门

- `n8n`（编排层）
  - Webhook 接入
  - 调用 `/autoreply/decide`
  - 按 `send/dry_run` 路由到 `/send` 或“不发送结束”
  - 可观测性与控制流编排

- `goofish-cli`（操作层）
  - 登录、收发消息、商品/媒体等平台动作
  - 自带写操作限流与熔断

## Non-goals

- 不把 goofish-bridge 做成独立客服模型系统
- 不在 bridge 中配置底层模型供应商
- 不绕过平台风控、不自动处理滑块、不引导站外交易
