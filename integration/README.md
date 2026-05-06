# Portal T13 Smoke

This smoke suite validates Portal-side contracts only, without starting real runtime services.

It covers:
- runtime_type strict helper tests, including legacy missing runtime_type compatibility
- runtime trace header propagation for generic proxy, streaming/multipart proxy, WebSocket events, and /app/chat/send
- browser-spoofed identity/trace header rejection
- ProxyService outbound trace/identity header allowlist and sanitization
- alembic runtime_type server_default cleanup and legacy DB upgrade
- config/docs default consistency
- k8s source overlay contract

Live runtime contract validation is owned by runtime repo or multi-repo integration workspaces.

Run:

```bash
integration/scripts/smoke_portal.sh
```
