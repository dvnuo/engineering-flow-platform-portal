# Portal T13 Smoke

This smoke suite validates Portal-side T13 contracts only, without starting real runtime services.

It covers create/edit/proxy/k8s spec contract checks from Portal tests, including
opencode state persistence mounts/env, runtime events proxy streaming routes, websocket
event proxy headers, and runtime-profile sync payload shaping.

Live runtime contract validation is owned by runtime repo or multi-repo integration workspaces.

Run:

```bash
integration/scripts/smoke_portal.sh
```
