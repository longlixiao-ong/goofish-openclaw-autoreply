# N8N Control Workflow

- 控制 workflow 文件：`n8n/workflows/goofish-autoreply-control.example.json`
- 主发送 workflow 文件：`n8n/workflows/goofish-inbound.example.json`

控制 workflow 可以单独导入、单独激活。

控制 workflow 不包含 `/send`。

## 测试命令

测试模式：

```powershell
curl.exe -X POST http://127.0.0.1:5678/webhook-test/goofish-autoreply/start
curl.exe -X POST http://127.0.0.1:5678/webhook-test/goofish-autoreply/stop
curl.exe http://127.0.0.1:5678/webhook-test/goofish-autoreply/status
```

生产模式：

```powershell
curl.exe -X POST http://127.0.0.1:5678/webhook/goofish-autoreply/start
curl.exe -X POST http://127.0.0.1:5678/webhook/goofish-autoreply/stop
curl.exe http://127.0.0.1:5678/webhook/goofish-autoreply/status
```

安全收尾：

```powershell
curl.exe -X POST http://127.0.0.1:8787/autoreply/stop -H "X-Bridge-Token: $env:BRIDGE_AUTH_TOKEN"
curl.exe http://127.0.0.1:8787/autoreply/status -H "X-Bridge-Token: $env:BRIDGE_AUTH_TOKEN"
```

## Verified local production webhook test

测试日期：2026-04-29

已验证：

- 只启动 n8n 和 goofish-bridge
- 未启动 goofish-watcher
- 控制 workflow：goofish-autoreply-control.example
- 生产 webhook start 通过：
  curl.exe -X POST http://127.0.0.1:5678/webhook/goofish-autoreply/start
- bridge 状态变为 enabled=true
- 生产 webhook stop 通过：
  curl.exe -X POST http://127.0.0.1:5678/webhook/goofish-autoreply/stop
- bridge 状态恢复 enabled=false
- 生产 webhook status 通过：
  curl.exe http://127.0.0.1:5678/webhook/goofish-autoreply/status
- bridge 日志显示 n8n 容器成功调用：
  POST /autoreply/start 200
  POST /autoreply/stop 200
  GET /autoreply/status 200

安全确认：

- 未调用 /send
- 未运行 goofish message send
- 未启动 goofish-watcher
- 未真实发送闲鱼消息
- 测试结束后 docker compose down 已执行
- 最终 autoreply enabled=false
