# Step by Step

### Create Nginx Class
```
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
```

### Create Namespace
```
kubectl apply -f namespaces.yaml
```

### Create PVC
```
kubectl apply -f efp-efs-pvc.yaml
```

### Create Secret
```
kubectl apply -f portal-git-clone/efp-portal-secret.yaml
kubectl apply -f efp-agents-secret.yaml
```

`efp-portal-secret` should include:
- `BOOTSTRAP_ADMIN_PASSWORD`
- (optional) `GIT_TOKEN` for init clone

`efp-agents-secret` should include:
- `EFP_CONFIG_KEY`
- (optional) `GIT_TOKEN` used by agent runtime/skill/tools git-clone initContainers

### Create Deployment
```
kubectl apply -f portal-git-clone/efp-portal-deployment.yaml
```

### Create Ingress
```
kubectl apply -f efp-portal-ingress.yaml
```

### Get Ingress IP
```
kubectl get svc -n ingress-nginx
```

## Note
Add ENV `BOOTSTRAP_ADMIN_PASSWORD` for portal admin
```
kubectl edit deploy efp-portal-deployment
```

Optional: customize git token key name used in `efp-agents-secret`:

- `K8S_GIT_TOKEN_KEY` (default `GIT_TOKEN`)

K8s clone uses HTTPS + `GIT_ASKPASS` + token-only auth. The askpass username response is fixed to `x-access-token`, and Portal does not rewrite clone URLs to authenticated URL forms.

Provisioning notes:
- Native runtime agents clone runtime repo and skill repo into the shared PVC and mount `/app/.git`, `/app/src`, `/app/skills`.
- OpenCode runtime agents do not mount native runtime overlays (`/app/.git`, `/app/src`, `/app/skills`). They use the image-contained runtime and mount workspace data (default `/workspace`).
- The OpenCode container `workingDir` is set to the effective workspace path, defaulting to `/workspace`.
- If tools repo is configured for OpenCode agents, Portal clones it in initContainer to `/workspace-data/tools`; main container sees it at `<workspace>/tools`.

`PORTAL_INTERNAL_BASE_URL` should point to Portal internal DNS (example: `http://efp-portal-service.default.svc.cluster.local`).

Portal requires Alembic migrations before startup for both first-time bootstrap and upgrades:
```
alembic upgrade head
```
