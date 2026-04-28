# Risk Control

## Principles

The automation must reduce risk, not bypass platform checks.

## Layer 1: goofish-cli

Use the built-in protection:

```env
GOOFISH_WRITE_RPM=1
GOOFISH_CIRCUIT_BREAK_MINUTES=30
```

Write operations must use the official `goofish message send` command or the later bridge wrapper that preserves the same limiter behavior.

## Layer 2: n8n workflow

n8n is responsible for business-level guardrails:

- De-duplicate inbound messages.
- Apply per-conversation cooldown.
- Apply global send queue.
- Stop on repeated failures.
- Stop when authentication or platform validation fails.
- Route high-risk messages to manual handling.

## Layer 3: human recovery

When login or platform validation fails:

1. Disable auto-reply.
2. Notify the owner.
3. Ask the owner to refresh login state through normal browser use.
4. Re-import login state with goofish-cli.
5. Verify `goofish auth status`.
6. Re-enable auto-reply only after health checks pass.

## High-risk messages

Do not auto-send replies for:

- Refunds
- Complaints
- After-sales disputes
- Hostile or threatening messages
- External-contact requests
- Payment outside the platform
- Large price disputes
- Ambiguous image-only messages
