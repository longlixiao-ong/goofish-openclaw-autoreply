# goofish-bridge

Planned stable component.

The bridge will replace shell-based send operations with a long-running HTTP service.

Planned endpoints:

```text
GET  /health
GET  /status
POST /send
POST /autoreply/start
POST /autoreply/stop
GET  /autoreply/status
```

Initial MVP does not require this component. Start with `goofish-watcher` + n8n first.
