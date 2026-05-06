# Portal T13 Smoke

This smoke suite validates Portal-side contracts only, without starting real runtime services.

It covers:
- runtime_type strict helper tests
- runtime trace header propagation
- alembic runtime_type server_default cleanup
- config/docs default consistency
- k8s source overlay contract

Live runtime contract validation is owned by runtime repo or multi-repo integration workspaces.

Run:

```bash
integration/scripts/smoke_portal.sh
```
