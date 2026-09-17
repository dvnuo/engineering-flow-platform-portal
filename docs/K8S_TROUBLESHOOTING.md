# Kubernetes Troubleshooting Guide

Use this guide when Portal can create an assistant but cannot reach its runtime, or when a runtime never becomes ready. For deployment instructions, see the [Kubernetes setup guide](../k8s/README.md). Commands below use Bash and the default namespaces: `default` for Portal and `efp-agents` for assistants. Replace names in angle brackets before running a command.

## Understand the connection first

The browser talks to Portal. Portal then proxies the request to the assistant's Kubernetes Service. A successful direct connection from your laptop does not prove that the Portal process can reach the same address.

| Where Portal runs | Runtime Service | Address used by Portal |
| --- | --- | --- |
| Inside the cluster | `ClusterIP` (default) | `http://<service>.<namespace>.svc.cluster.local:8000` |
| Outside the cluster | `NodePort` | `http://<reachable-kubernetes-node-ip>:<node-port>` |

`K8S_AGENT_SERVICE_TYPE` controls assistant Services. The type of `efp-portal-service`, which exposes Portal itself, is a separate setting in the deployment manifest.

Typical symptoms include `Proxy upstream failure: All connection attempts failed`, a chat that cannot connect, or an assistant stuck in `creating`. An assistant showing `running` is not sufficient evidence of a live runtime: with Kubernetes disabled, Portal can create a local demonstration record without starting a pod.

## 1. Confirm that Kubernetes integration is active

For an in-cluster deployment, the Portal container needs `K8S_ENABLED=true`, `K8S_INCLUSTER=true`, and the `portal` ServiceAccount and RoleBinding from `k8s/service-account.yaml`.

For a Portal process running outside the cluster, use `K8S_ENABLED=true`, `K8S_INCLUSTER=false`, and `K8S_KUBECONFIG` pointing to a kubeconfig file readable by that process. A kubeconfig on the host is not automatically available inside a Docker container.

Check the context and access:

```bash
kubectl config current-context
kubectl get pods -n efp-agents
kubectl auth can-i get services -n efp-agents
```

The last command checks your current kubeconfig identity. To check the in-cluster Portal identity, an administrator with impersonation permission can run:

```bash
kubectl auth can-i get services -n efp-agents --as=system:serviceaccount:default:portal
kubectl auth can-i create deployments -n efp-agents --as=system:serviceaccount:default:portal
```

If there are no assistant resources, inspect Portal's effective configuration. `K8sService` disables its integration if Kubernetes client initialization fails, so invalid credentials or an unreadable kubeconfig can also leave Portal in its local fallback mode.

## 2. Inspect the assistant's resources

Find the assistant ID in Portal, then list matching resources:

```bash
kubectl get deployments,pods,services -n efp-agents -l agent-id=<agent-id> -o wide
kubectl get pvc -n efp-agents
kubectl get events -n efp-agents --sort-by=.metadata.creationTimestamp
```

Inspect the actual pod name from that output:

```bash
kubectl describe pod <agent-pod-name> -n efp-agents
kubectl logs <agent-pod-name> -n efp-agents -c agent --tail=100
```

If an init container is failing, use its name from `kubectl describe pod`. For example:

```bash
kubectl logs <agent-pod-name> -n efp-agents -c skills-git-clone --tail=100
```

Both supported runtimes expose `/ready` on port `8000`. A pod becomes ready only after the runtime's startup configuration succeeds. A running container with readiness `0/1` still needs investigation.

## 3. Check the runtime Service and endpoints

```bash
kubectl get service <agent-service-name> -n efp-agents -o yaml
kubectl get endpointslice -n efp-agents -l kubernetes.io/service-name=<agent-service-name>
```

The Service should select the assistant's pod and expose port `8000`. If it has no ready endpoints, resolve pod readiness before investigating Portal's proxy.

For an outside-cluster Portal, new assistants need `K8S_AGENT_SERVICE_TYPE=NodePort`. Changing this setting does not convert an existing Service: the creation path preserves existing Services. An administrator can update the existing Service explicitly, after reviewing the resulting network exposure:

```bash
kubectl patch service <agent-service-name> -n efp-agents --type=merge -p '{"spec":{"type":"NodePort"}}'
kubectl get service <agent-service-name> -n efp-agents -o jsonpath='{.spec.ports[0].nodePort}'
```

For an in-cluster Portal, keep `ClusterIP` unless the deployment specifically requires another network arrangement.

## 4. Verify the NodePort address, if applicable

**`K8S_NODE_IP` must be a Kubernetes node address reachable from Portal. It is not necessarily the IP of the machine running Portal.** List candidate addresses with:

```bash
kubectl get nodes -o wide
```

Choose a node address that is routable from the Portal host or container. Cloud firewalls, security groups, local firewalls, VPNs, and container networking can affect access to the allocated NodePort.

`ProxyService.node_ip` checks the process environment in this order:

1. `K8S_NODE_IP`.
2. `NODE_IP`.
3. The first non-loopback IPv4-looking address from `hostname -I`.

The third option inspects the machine or container running Portal. It does not discover Kubernetes nodes, and can therefore produce the wrong address. Explicitly set `K8S_NODE_IP` for an outside-cluster deployment.

Unlike the settings loaded through `app/config.py`, these two IP overrides are read directly from `os.environ`. Adding `K8S_NODE_IP` only to Portal's `.env` file does **not** set this override. Export it before launching Portal, pass it through Docker's environment, or add it to the Kubernetes container environment. Restart Portal after changing it; the detected address is cached.

Example for Bash, in the same shell that starts Portal:

```bash
export K8S_NODE_IP="192.0.2.10"  # Replace with your reachable Kubernetes node IP.
python -c "from app.services.proxy_service import ProxyService; print(ProxyService().node_ip)"
```

Example for PowerShell:

```powershell
$env:K8S_NODE_IP = "192.0.2.10" # Replace with your reachable Kubernetes node IP.
python -c "from app.services.proxy_service import ProxyService; print(ProxyService().node_ip)"
```

Run diagnostics inside the Portal container when Portal is containerized. Running the Python command on your laptop would inspect your laptop's environment instead.

## 5. Test connectivity from Portal's network environment

For NodePort, run this from the Portal host or container, using its reachable node address:

```bash
NODE_PORT=$(kubectl get service <agent-service-name> -n efp-agents -o jsonpath='{.spec.ports[0].nodePort}')
curl --connect-timeout 5 "http://${K8S_NODE_IP}:${NODE_PORT}/ready"
```

If the Portal container has no `kubectl`, obtain the port using your administration shell and use the resulting number in the connection test. Python and `httpx` are available in the Portal image, so an in-cluster test can use:

```bash
kubectl exec -n default deployment/efp-portal-deployment -c portal-container -- python -c "import httpx; r = httpx.get('http://<agent-service-name>.efp-agents.svc.cluster.local:8000/ready', timeout=5); print(r.status_code); print(r.text)"
```

Interpret the result:

| Result | Next step |
| --- | --- |
| HTTP `200` from `/ready` | Network and startup readiness work; investigate the failing API request and runtime logs. |
| Connection refused | Check the port, ready endpoints, pod state, and whether the runtime listens on `8000`. |
| Connection timeout | Check routing, firewall rules, NetworkPolicies, and NodePort reachability. |
| DNS resolution failure | Confirm Portal is inside the cluster for a `ClusterIP` address and cluster DNS is available. |
| Non-`200` readiness response | Inspect runtime logs and the applied runtime profile. |

## 6. Inspect Portal's logs

Use the command matching how you launched Portal:

```bash
# Kubernetes deployment from this repository.
kubectl logs -n default deployment/efp-portal-deployment -c portal-container --tail=100

# Docker; replace the container name.
docker logs --tail=100 <portal-container-name>

# systemd, only if you created a service named portal.
journalctl -u portal -n 100
```

For a direct `uvicorn` process, read the terminal or the log destination you configured. Portal does not assume a `/var/log/portal.log` file exists.

Look for `Resolved runtime base URL`. The message identifies the assistant, Service, namespace, and upstream address. `service_type=fallback` means Portal could not resolve the Service through the Kubernetes API and fell back to cluster DNS; check credentials and Service access before relying on that address.

## Common problems

| Problem | Likely cause | Action |
| --- | --- | --- |
| Assistant exists, but no pod exists | Kubernetes disabled or client initialization failed | Check `K8S_ENABLED`, `K8S_INCLUSTER`, kubeconfig, and ServiceAccount access. |
| Outside-cluster Portal uses `.svc.cluster.local` | Runtime is `ClusterIP`, or Service lookup failed | Fix API access and configure a reachable NodePort Service. |
| Proxy connects to the wrong IP | Host/container address was auto-detected | Export `K8S_NODE_IP` as a reachable Kubernetes node IP, then restart Portal. |
| `ImagePullBackOff` | Runtime image/tag unavailable or registry access denied | Inspect pod events and verify the selected runtime image and registry credentials. |
| `Init:Error` or `Init:CrashLoopBackOff` | Repository, branch, token, or asset structure is invalid | Inspect the failed init-container logs; check the skills and agent-settings repositories. |
| PVC is `Pending` | Missing provisioner, unmatched volume, or access-mode mismatch | Inspect the PVC and storage setup; see the [storage notes](../k8s/README.md#2-prepare-persistent-storage). |
| Chat fails during long quiet periods | Ingress or load-balancer stream timeout | Review timeouts along the entire path; the sample Ingress sets read/send timeouts to 3,600 seconds. |
| Upload returns HTTP `413` | An ingress, Portal, or runtime size limit was exceeded | Align `EFP_MAX_UPLOAD_MB` with the runtime and ingress limits. |

## Outside-cluster configuration example

The following values can be placed in Portal's `.env` file. Replace the kubeconfig path with one that exists on the Portal host:

```dotenv
K8S_ENABLED=true
K8S_INCLUSTER=false
K8S_KUBECONFIG=/absolute/path/to/kubeconfig
K8S_AGENT_SERVICE_TYPE=NodePort
DEFAULT_AGENT_IMAGE_REPO=ghcr.io/dvnuo/engineering-flow-platform
DEFAULT_AGENT_IMAGE_TAG=latest
```

Set `K8S_NODE_IP` separately in the process environment as described above. Set `PORTAL_INTERNAL_BASE_URL` to a Portal address reachable **from the runtime pods**; `localhost` would point back to each runtime pod. Use the [configuration reference](../README.md) for authentication, storage, runtime selection, and other deployment settings.
