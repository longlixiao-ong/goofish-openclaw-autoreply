# SMOKE TEST RESULT

- 测试日期：2026-04-29
- 环境：Windows PowerShell + Docker Compose

## 服务

- n8n
- goofish-bridge
- 未启动 goofish-watcher

## 已验证

- docker compose build goofish-bridge：通过
- docker compose up -d n8n goofish-bridge：通过
- docker compose ps：goofish-bridge running，n8n running
- goofish-bridge 端口绑定为 127.0.0.1:8787->8787/tcp
- curl http://127.0.0.1:8787/health：通过
- curl http://127.0.0.1:8787/autoreply/status：通过
- python scripts/smoke_bridge.py：通过
- n8n webhook-test goofish-autoreply/start：通过
- n8n webhook-test goofish-autoreply/stop：通过

## 安全确认

- 未启动 goofish-watcher
- 未调用 /send
- 未执行 goofish message send
- 未真实发送闲鱼消息
- 测试结束后 autoreply enabled=false
- docker compose down 已执行，容器已移除
