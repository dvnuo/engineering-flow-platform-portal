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
- (optional) `GIT_TOKEN` for Portal init clone and `/api/git-repos/branches` lookups

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

Portal also injects `efp-portal-secret.GIT_TOKEN` into the main container as `GIT_REPO_AUTH_PAT` so the create-agent wizard can list branches for private skills and agent-settings repositories.

Provisioning notes:
- Portal provisions one Python EFP runtime image; there is no runtime selector or alternate-runtime K8s path.
- Agent workspace data mounts at `/workspace` by default, and the container `workingDir` follows the effective workspace path.
- Portal does not support runtime source overlay; runtime pods run the configured image and do not mount `/app/src` or `/app/.git` from a cloned runtime repo.
- Skill repo content is provisioned to `/app/skills`; Portal does not provision a separate tools repo.
- Runtime owns tools, skills execution, loop control, context shaping, compaction, sessions, and permissions.

`PORTAL_INTERNAL_BASE_URL` should point to Portal internal DNS (example: `http://efp-portal-service.default.svc.cluster.local`).

Portal requires Alembic migrations before startup for both first-time bootstrap and upgrades:
```
alembic upgrade head
```
