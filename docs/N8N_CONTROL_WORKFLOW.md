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
curl.exe -X POST http://127.0.0.1:8787/autoreply/stop
curl.exe http://127.0.0.1:8787/autoreply/status
```
