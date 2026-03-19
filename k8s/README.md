# Step by Step

### Create Nginx Class
```
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/cloud/deploy.yaml
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