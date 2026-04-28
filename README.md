# 闲鱼自动客服方案：goofish-cli + n8n + OpenClaw

> 目标：把闲鱼做成一个可长期运行、可控、可停机、可审计的自动客服通道。  
> `goofish-cli` 负责闲鱼登录、收消息、发消息、查询商品、上传图片、发布商品。  
> `n8n` 负责编排流程、自动客服开关、去重、冷却、发送队列、风控分流。  
> `OpenClaw` 负责模型、视觉理解、回复策略、记忆、小刀议价规则。

---

## 1. 项目目标

这个项目不是再做一个“自带模型的闲鱼机器人”，而是把闲鱼接入到一个更可控的 AI 工作流里。

核心目标：

1. 买家在闲鱼发消息后，系统能自动接收。
2. 文本消息可以由 OpenClaw 根据卖家规则生成回复。
3. 图片消息可以进入视觉模型识别，再由 OpenClaw 结合商品信息回复。
4. 小刀、砍价、包邮、发货、售后、外联等闲鱼场景有明确策略。
5. 回复前必须清理思考过程，禁止把 `<think>`、分析、推理发给买家。
6. 触发风控时必须停机，不高频重试、不绕过滑块、不伪造设备。
7. 支持 24 小时运行，但必须有健康检查、限流、熔断和通知。
8. 后续可以扩展商品发布、图片上传、商品诊断、订单提醒等能力。

---

## 2. 选型结论

### 2.1 为什么不继续使用独立自动回复脚本

一些闲鱼自动回复项目可以跑通基础回复，但通常存在以下问题：

- 自带模型配置，不能直接使用 OpenClaw 里的模型和记忆。
- Prompt 和回复逻辑写死在项目内部，不方便在 OpenClaw 中持续调教。
- 回复质量、小刀策略、风控策略需要大量二次开发。
- 图片消息链路不完整，无法稳定把买家图片交给视觉模型。
- 很难做到“我在 OpenClaw 里说开启自动客服，它就自动接管”。

### 2.2 推荐底座：goofish-cli

`goofish-cli` 更适合作为闲鱼能力层，因为它把闲鱼核心能力抽成 CLI / MCP 工具，而不是把模型写死在项目里。

它适合负责：

- 登录态管理
- 商品查询
- 商品发布
- 图片上传
- 类目识别
- 会话列表
- IM 实时监听
- 文本/图片发送
- MCP 工具暴露
- 写操作限流
- RGV587 风控熔断

本方案中：

```text
goofish-cli = 闲鱼操作层
n8n         = 自动化编排层
OpenClaw   = AI 大脑
```

---

## 3. 总体架构

### 3.1 MVP 架构

第一阶段先用最小链路跑通：

```text
闲鱼买家消息
  ↓
goofish message watch
  ↓ JSONL
goofish-watcher.py
  ↓ HTTP POST
n8n Webhook
  ↓
去重 / 冷却 / 自动客服开关 / 风险分级
  ↓
OpenClaw Agent
  ↓
返回结构化 JSON
  ↓
n8n 清洗 reply + 风控扫描
  ↓
goofish message send
  ↓
回复闲鱼买家
```

优点：实现快，便于验证。
缺点：发送阶段如果通过 n8n 执行命令，会有一定延迟。

### 3.2 稳定版架构

后续稳定运行时，建议改成服务化：

```text
Docker Compose
├── n8n
├── goofish-bridge
│   ├── watch loop
│   ├── send queue
│   ├── health check
│   ├── autoreply on/off
│   └── HTTP API
└── OpenClaw
```

稳定版中，n8n 不直接执行 shell 命令，而是通过 HTTP 调用 `goofish-bridge`：

```text
POST /send
POST /autoreply/start
POST /autoreply/stop
GET  /health
GET  /status
```

---

## 4. 组件职责

### 4.1 goofish-cli

负责所有闲鱼底层动作：

```text
auth login
auth status
message watch
message send
message history
message list-chats
item get
item publish
media upload
search items
```

goofish-cli 不负责：

```text
不做最终客服回复
不配置 OpenAI / DashScope / Gemini 模型
不写死客服 prompt
不判断复杂议价策略
不承担 OpenClaw 的记忆与视觉能力
```

### 4.2 n8n

n8n 负责流程编排：

```text
接收 goofish-watcher 推来的消息
判断自动客服是否开启
做 message_id / cid 去重
做同一会话冷却
做全局发送限流
按风险等级分流
调用 OpenClaw
清洗最终回复
做外联词扫描
调用 goofish message send 或 goofish-bridge /send
失败时熔断
通知用户人工处理
```

### 4.3 OpenClaw

OpenClaw 负责智能判断：

```text
根据商品、上下文、买家消息生成回复
识别图片内容
处理小刀 / 砍价 / 包邮 / 发货 / 售后
保持卖家人设
避免 AI 味
遵守闲鱼规则
只输出买家可见回复
```

---

## 5. 消息流程

### 5.1 文本消息

示例：

```text
买家：还在吗？
```

`goofish message watch` 输出：

```json
{
  "event": "message",
  "cid": "60585751957",
  "send_user_id": "2215266653893",
  "send_user_name": "买家昵称",
  "send_message": "还在吗",
  "content_type": 1
}
```

n8n 判断为低风险简单消息，可以不调用模型，直接规则回复：

```text
在的，喜欢可拍
```

### 5.2 议价消息

示例：

```text
买家：能便宜点吗？
```

流程：

```text
n8n 判断 intent=price
  ↓
OpenClaw 根据议价规则生成结构化回复
  ↓
n8n 清洗 reply
  ↓
外联词扫描
  ↓
goofish message send
```

OpenClaw 推荐输出：

```json
{
  "send": true,
  "reply": "可以小刀，你出个合适价",
  "risk": "low",
  "handoff": false
}
```

### 5.3 图片消息

当前需要补强 `goofish-cli` 的图片消息解析，让 `message watch` 输出图片字段。

理想格式：

```json
{
  "event": "message",
  "cid": "60585751957",
  "send_user_id": "2215266653893",
  "send_user_name": "买家昵称",
  "send_message": "",
  "content_type": 2,
  "image_url": "https://...",
  "image_width": 1080,
  "image_height": 1080
}
```

流程：

```text
买家发送图片
  ↓
goofish watch 输出 image_url
  ↓
n8n 判断 content_type=2
  ↓
OpenClaw 视觉模型识别图片
  ↓
OpenClaw 结合商品信息生成回复
  ↓
n8n 清洗回复
  ↓
goofish message send
```

图片识别失败时建议回复：

```text
图片这边看不太清，可以再拍清楚点
```

---

## 6. 自动客服开关

自动客服必须有开关，不允许永久无条件自动回复。

建议状态：

```json
{
  "enabled": false,
  "mode": "auto",
  "safe_mode": true,
  "auto_send": true,
  "cooldown_seconds": 15,
  "global_send_interval_seconds": 30,
  "max_reply_chars": 80
}
```

可提供接口：

```text
POST /autoreply/start
POST /autoreply/stop
GET  /autoreply/status
```

在 OpenClaw 里可以做成工具：

```text
xianyu_autoreply_start
xianyu_autoreply_stop
xianyu_autoreply_status
```

目标体验：

```text
用户：开启闲鱼自动客服
OpenClaw：调用 xianyu_autoreply_start
系统：自动客服开启
```

---

## 7. 回复质量控制

### 7.1 OpenClaw 输出格式

OpenClaw 必须输出 JSON，不允许直接输出一大段自然语言：

```json
{
  "send": true,
  "reply": "可以小刀一点，合适可拍",
  "risk": "low",
  "handoff": false
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `send` | 是否允许发送 |
| `reply` | 买家可见回复 |
| `risk` | low / medium / high |
| `handoff` | 是否转人工 |

### 7.2 禁止输出内容

最终发给买家的内容不得包含：

```text
<think>
思考：
分析：
推理：
判断：
策略：
最终回复：
回复：
Markdown 列表
模型解释
```

### 7.3 n8n 回复清洗函数

n8n 发送前必须执行清洗：

```js
function sanitizeReply(input) {
  let text = String(input || "");

  text = text.replace(/<think>[\s\S]*?<\/think>/gi, "");
  text = text.replace(/```(?:thinking|reasoning|analysis)[\s\S]*?```/gi, "");

  const markers = [
    "最终回复：", "最终回复:",
    "买家可见回复：", "买家可见回复:",
    "回复：", "回复:"
  ];

  for (const m of markers) {
    if (text.includes(m)) text = text.split(m).pop();
  }

  text = text
    .split("\n")
    .map(s => s.trim())
    .filter(s => s && !/^(思考|分析|推理|判断|策略|步骤|理由|reasoning|analysis)/i.test(s))
    .join(" ");

  text = text.replace(/^[#>*\-\d\.\s]+/g, "").trim();
  text = text.replace(/[“”"']/g, "").trim();

  if (/微信|QQ|支付宝|银行卡|线下|转账|私聊|加我/.test(text)) {
    return "平台内沟通就行，合适可拍";
  }

  if (!text) return "";
  return text.slice(0, 80);
}

return {
  reply: sanitizeReply($json.reply)
};
```

---

## 8. 闲鱼业务规则

### 8.1 低风险自动回复

| 买家消息 | 回复 |
|---|---|
| 还在吗 | 在的，喜欢可拍 |
| 在吗 | 在的 |
| 包邮吗 | 默认不包，合适可以小刀 |
| 今天能发吗 | 今天拍下可以尽快发 |
| 能便宜点吗 | 可以小刀，你出个合适价 |
| 最低多少 | 价格比较实，合适可拍 |

### 8.2 小刀 / 议价规则

```text
第一次问价：
  不主动报大幅优惠，让买家先出价。

第二次议价：
  可以小刀一点，但不承诺大额让利。

砍价太狠：
  礼貌拒绝，强调价格已经很实。

多轮砍价：
  明确最终态度，避免无限拉扯。

疑似恶意压价：
  转人工或礼貌结束。
```

推荐话术：

```text
可以小刀，你出个合适价
这个刀太多了，价格已经很实
最低了，合适可拍
可以少一点，喜欢就拍
```

### 8.3 外联风险

买家出现以下内容时，不自动引导站外沟通：

```text
微信
QQ
支付宝
银行卡
线下
转账
私聊
加我
走外面
```

统一替换为：

```text
平台内沟通就行，合适可拍
```

### 8.4 高风险转人工

以下场景不自动回复：

```text
退款
投诉
售后纠纷
质量争议
威胁举报
辱骂
疑似诈骗
金额争议较大
模型无法判断
图片识别失败且问题复杂
```

---

## 9. 风控设计

### 9.1 风控不能靠 n8n 解决

n8n 只能减少触发概率，不能绕过平台风控。

正确职责：

```text
goofish-cli：底层限流 + 熔断
n8n：业务风控 + 队列 + 停机策略
OpenClaw：回复判断
用户：处理滑块、x5sec、登录态失效
```

### 9.2 底层限流

建议环境变量：

```env
GOOFISH_WRITE_RPM=1
GOOFISH_CIRCUIT_BREAK_MINUTES=30
```

含义：

```text
GOOFISH_WRITE_RPM=1
每分钟最多 1 次写操作，降低风控概率。

GOOFISH_CIRCUIT_BREAK_MINUTES=30
触发风控后熔断 30 分钟。
```

### 9.3 n8n 业务限流

建议：

```text
同一会话冷却：15 秒
全局发送间隔：20–60 秒
连续发送失败：3 次熔断
命中 RGV587：立即关闭自动客服
命中 FAIL_SYS_USER_VALIDATE：立即关闭自动客服
命中 x5sec 问题：立即关闭自动客服
```

### 9.4 风控恢复流程

当出现：

```text
RGV587
FAIL_SYS_USER_VALIDATE
FAIL_SYS_ILLEGAL_ACCESS
x5sec 缺失
Cookie 失效
```

处理：

```text
1. n8n 关闭自动客服
2. 通知用户
3. 用户打开闲鱼网页版
4. 正常浏览 3–5 分钟
5. 点击消息 / 商品详情
6. 完成滑块或验证
7. 重新导入登录态
8. auth status 确认有效
9. 再开启自动客服
```

禁止：

```text
不要自动重试
不要高频重试
不要尝试绕过滑块
不要伪造设备
不要批量切号规避风控
```

---

## 10. 24 小时运行设计

### 10.1 必须服务化

不能靠手动打开终端。

推荐：

```text
Docker Compose
systemd
supervisor
PM2
```

### 10.2 Docker Compose 示例

```yaml
services:
  n8n:
    image: n8nio/n8n:latest
    restart: always
    ports:
      - "5678:5678"
    volumes:
      - ./n8n_data:/home/node/.n8n

  goofish-watcher:
    build: ./goofish-watcher
    restart: always
    environment:
      - N8N_WEBHOOK_URL=http://n8n:5678/webhook/goofish-inbound
      - GOOFISH_WRITE_RPM=1
      - GOOFISH_CIRCUIT_BREAK_MINUTES=30
    volumes:
      - ./goofish-state:/root/.goofish-cli
    depends_on:
      - n8n

  openclaw:
    image: your-openclaw-image
    restart: always
    volumes:
      - ./openclaw-data:/data
```

### 10.3 健康检查

每 5 分钟检查：

```text
goofish auth status
n8n workflow 是否可用
OpenClaw 是否可调用
watcher 是否在运行
最近是否有连续失败
```

异常处理：

```text
登录态失效 → 停止自动客服
OpenClaw 不可用 → 停止自动客服
发送失败超过 3 次 → 停止自动客服
触发风控 → 停止自动客服
```

---

## 11. n8n 工作流设计

### 11.1 工作流 1：接收闲鱼消息

```text
Webhook: /goofish-inbound
  ↓
Code: message_id 去重
  ↓
Code: 自动客服开关检查
  ↓
Code: 会话冷却检查
  ↓
IF: 是否高风险消息
  ├─ 是：通知人工，不发送
  └─ 否：继续
  ↓
IF: 是否图片消息
  ├─ 是：调用视觉模型 / OpenClaw 图片理解
  └─ 否：跳过
  ↓
HTTP Request: 调 OpenClaw Agent
  ↓
Code: sanitizeReply
  ↓
Code: 外联词扫描
  ↓
Queue / Wait: 全局发送限流
  ↓
Execute Command 或 HTTP: goofish message send
  ↓
记录日志
```

### 11.2 工作流 2：自动客服开关

```text
Webhook: /goofish-autoreply/start
  ↓
设置 enabled=true

Webhook: /goofish-autoreply/stop
  ↓
设置 enabled=false

Webhook: /goofish-autoreply/status
  ↓
返回当前状态
```

### 11.3 工作流 3：健康检查

```text
Cron: 每 5 分钟
  ↓
goofish auth status
  ↓
检查 OpenClaw
  ↓
检查最近错误
  ↓
异常则关闭自动客服并通知
```

---

## 12. goofish-watcher.py 设计

### 12.1 作用

`goofish-watcher.py` 不生成回复，只转发消息。

```text
goofish message watch
  ↓
读取 stdout JSONL
  ↓
过滤 event=message
  ↓
POST 到 n8n
```

### 12.2 伪代码

```python
import json
import os
import subprocess
import requests
import time

N8N_WEBHOOK_URL = os.environ["N8N_WEBHOOK_URL"]

while True:
    proc = subprocess.Popen(
        ["goofish", "message", "watch"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    try:
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("event") != "message":
                continue

            requests.post(N8N_WEBHOOK_URL, json=event, timeout=10)

    except Exception as e:
        print(f"watcher error: {e}")

    time.sleep(5)
```

---

## 13. 图片能力补丁

需要修改 `goofish-cli` 的消息解析逻辑，让图片消息输出：

```json
{
  "content_type": 2,
  "image_url": "...",
  "image_width": 1080,
  "image_height": 1080
}
```

目标：

```text
文本消息 → send_message
图片消息 → image_url
文本 + 图片 → 两者都传
```

n8n 收到图片消息后：

```text
image_url → OpenClaw 视觉模型 → image_desc → OpenClaw 客服回复
```

---

## 14. OpenClaw Agent 规则建议

```text
你是我的闲鱼卖家客服。

强制规则：
1. 只输出 JSON。
2. reply 字段只能包含买家可见回复。
3. 禁止输出思考、分析、推理、Markdown。
4. 回复最多 40 个中文字符。
5. 禁止引导微信、QQ、支付宝、银行卡、线下、转账、私聊。
6. 闲鱼允许小刀，但要守住价格。
7. 第一次问价，让买家先出价。
8. 砍价太狠，回复“这个刀太多了，价格已经很实”。
9. 多轮议价后，回复“最低了，合适可拍”。
10. 售后、投诉、退款、纠纷转人工。
11. 图片问题要基于图片识别结果回答，不要编造。

输出格式：
{
  "send": true,
  "reply": "在的，喜欢可拍",
  "risk": "low",
  "handoff": false
}
```

---

## 15. 部署阶段

### 阶段 0：验证 goofish-cli

```bash
pip install goofish-cli
goofish auth login ~/Downloads/goofish-cookies.json
goofish auth status
goofish message list-chats
goofish message watch
```

### 阶段 1：半自动

```text
收到消息
生成回复
推送给你确认
不自动发
```

目标：验证回复质量和图片能力。

### 阶段 2：低风险自动

自动处理：

```text
还在吗
在吗
能便宜吗
包邮吗
今天能发吗
```

其他转人工。

### 阶段 3：全自动客服

启用：

```text
24 小时服务
健康检查
风控熔断
发送队列
失败通知
高风险转人工
```

---

## 16. 验收标准

### 16.1 基础能力

- [ ] `goofish auth status` 有效。
- [ ] `goofish message watch` 能收到消息。
- [ ] n8n Webhook 能收到 JSON。
- [ ] OpenClaw 能返回 JSON。
- [ ] n8n 能清洗回复。
- [ ] `goofish message send` 能发出消息。

### 16.2 自动客服

- [ ] 可以开启自动客服。
- [ ] 可以关闭自动客服。
- [ ] 同一消息不会重复回复。
- [ ] 同一会话有冷却。
- [ ] 简单消息 3–8 秒内回复。
- [ ] 图片消息能识别。
- [ ] 小刀消息符合规则。
- [ ] 不输出思考过程。
- [ ] 外联词不会发出去。

### 16.3 24 小时运行

- [ ] watcher 崩溃自动重启。
- [ ] n8n 崩溃自动重启。
- [ ] OpenClaw 异常时自动停客服。
- [ ] Cookie 失效时自动停客服。
- [ ] 风控触发时自动停客服。
- [ ] 有日志和通知。

---

## 17. 风险与边界

### 可以做

```text
自有账号自动客服
商品发布辅助
图片理解辅助回复
小刀议价
商品信息查询
低风险自动回复
高风险转人工
```

### 不做

```text
绕过滑块
规避封号
伪造设备
批量注册账号
刷单
虚假交易
引导站外交易
隐藏微信/QQ/支付宝
欺诈性描述商品
```

---

## 18. 最终路线

推荐路线：

```text
goofish-cli 跑通
  ↓
goofish-watcher.py 转发消息到 n8n
  ↓
n8n 跑通 OpenClaw 回复
  ↓
n8n 清洗 + 风控 + 限流
  ↓
低风险自动回复
  ↓
补图片字段
  ↓
24 小时 Docker 化
  ↓
再考虑 goofish-bridge HTTP 常驻服务
```

最终形态：

```text
闲鱼 = 输入输出通道
goofish-cli = 闲鱼操作层
n8n = 自动化流程控制层
OpenClaw = 唯一 AI 大脑
```

---

## 19. Day1 正式可跑版（feat/mvp-autoreply）

### 19.1 Windows Codex App 开发流程

1. 在 Codex App 打开仓库 `C:\Users\lucky\Projects\goofish-openclaw-autoreply`。
2. 确认分支是 `feat/mvp-autoreply`，仅在该分支开发和验证。
3. 在本地先完成静态校验与编译检查，不做真实闲鱼发信压测。
4. 把 n8n workflow 与 snippets 作为示例模板导入，再按本机环境补齐路径。

### 19.2 PowerShell 常用命令（MVP）

```powershell
# 1) 确认分支
git branch --show-current

# 2) 编译检查（必须）
python -m py_compile goofish-watcher/watcher.py scripts/send_text.py

# 3) 启动示例服务（复制 example 文件后再运行）
docker compose -f docker-compose.yml up -d n8n goofish-watcher

# 4) 查看 watcher 日志
docker compose logs -f goofish-watcher
```

### 19.3 MVP 运行步骤（文本自动客服闭环）

1. 准备 `.env`（从 `.env.example` 复制），仅本地使用，不提交。
2. 准备 `data/autoreply-state.json`（可从 `data/autoreply-state.example.json` 复制）。
3. 启动 `n8n` 与 `goofish-watcher`。
4. 在 n8n 导入 `n8n/workflows/goofish-inbound.example.json`。
5. 按节点补齐本机 `OPENCLAW_REPLY_URL` 与 `send_text.py` 执行路径。
6. 保持 `safe_mode` 与限流参数，先用低风险问句做流程验证。

### 19.4 n8n Webhook 配置

- `goofish-watcher` 环境变量：`N8N_WEBHOOK_URL=http://n8n:5678/webhook/goofish-inbound`
- n8n Webhook 节点：`POST /webhook/goofish-inbound`
- 入站只处理 `event=message`，其余事件直接忽略。

### 19.5 OpenClaw 返回 JSON 格式（要求）

```json
{
  "send": true,
  "reply": "在的，喜欢可拍",
  "risk": "low",
  "handoff": false
}
```

### 19.6 测试清单（Day1）

- [ ] `python -m py_compile goofish-watcher/watcher.py scripts/send_text.py` 通过。
- [ ] watcher 只转发 `event=message`。
- [ ] n8n 收到 Webhook 后可完成：去重→开关→冷却→风险分类→回复清洗→外联扫描。
- [ ] `scripts/send_text.py` 失败时返回非 0，输出 JSON。
- [ ] 日志中不出现 Cookie / API Key。

### 19.7 安全提交提醒

不要提交以下内容到 Git：

- `.env`
- Cookie 文件或 `goofish-state/`
- API Key / Token
- `logs/`
- `n8n_data/`

---

## 20. 正式路线：goofish-bridge（HTTP 服务）

从这一版开始，n8n 发送阶段不再执行本地命令，不再调用 `scripts/send_text.py`。

正式链路改为：

```text
goofish message watch
  -> goofish-watcher
  -> n8n
  -> OpenClaw
  -> n8n sanitize/risk
  -> HTTP POST goofish-bridge /send
  -> goofish message send
```

关键边界：

- n8n 只通过 HTTP 调 `goofish-bridge`。
- `goofish-state` 只挂载在 `goofish-watcher` 和 `goofish-bridge`，n8n 不持有 Cookie。
- `goofish-bridge` 内部仍调用官方 `goofish-cli`，保留其限流与熔断，不做绕过。
- `scripts/send_text.py` 保留为本地调试备用脚本，不在正式 n8n 发送链路中使用。

### 20.1 Docker Compose 启动步骤

```powershell
# 1) 复制示例配置
Copy-Item .env.example .env

# 2) 启动正式链路服务
docker compose -f docker-compose.yml up -d n8n goofish-watcher goofish-bridge

# 3) 查看 bridge 日志
docker compose logs -f goofish-bridge
```

### 20.2 goofish-bridge 接口示例

```bash
# health
curl http://localhost:8787/health

# status
curl http://localhost:8787/status

# send
curl -X POST http://localhost:8787/send \
  -H "Content-Type: application/json" \
  -d '{"cid":"60585751957","toid":"2215266653893","text":"在的，喜欢可拍"}'

# 开启自动客服
curl -X POST http://localhost:8787/autoreply/start

# 关闭自动客服
curl -X POST http://localhost:8787/autoreply/stop

# 自动客服状态
curl http://localhost:8787/autoreply/status
```

### 20.3 n8n workflow 约束

- 入站消息 workflow：`n8n/workflows/goofish-inbound.example.json`
- 发送节点必须是 `HTTP Request -> http://goofish-bridge:8787/send`
- 开关节点必须调用：
  - `POST http://goofish-bridge:8787/autoreply/start`
  - `POST http://goofish-bridge:8787/autoreply/stop`
  - `GET  http://goofish-bridge:8787/autoreply/status`

### 20.4 正式路线测试步骤

```powershell
python -m py_compile goofish-watcher/watcher.py scripts/send_text.py goofish-bridge/app.py
python -m json.tool data/autoreply-state.example.json
python -m json.tool n8n/workflows/goofish-inbound.example.json
```

说明：

- 测试只做编译和 JSON 结构校验。
- 不运行真实 `goofish message send` 测试。
- 不运行真实闲鱼发送测试。
