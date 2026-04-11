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
- (optional) `GIT_USERNAME`, `GIT_TOKEN` for init clone

`efp-agents-secret` should include:
- `EFP_CONFIG_KEY`
- (optional) `GIT_USERNAME`, `GIT_TOKEN`

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

Optional: customize git credential key names used in `efp-agents-secret`:

- `K8S_GIT_USERNAME_KEY` (default `GIT_USERNAME`)
- `K8S_GIT_TOKEN_KEY` (default `GIT_TOKEN`)

`PORTAL_INTERNAL_BASE_URL` should point to Portal internal DNS (example: `http://efp-portal-service.default.svc.cluster.local`).

Portal requires Alembic migrations before startup for both first-time bootstrap and upgrades:
```
alembic upgrade head
```
