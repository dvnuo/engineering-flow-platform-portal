# K8s Integration Troubleshooting Guide

## Problem: Agent 创建成功但无法聊天

### 症状
- Portal 创建 Agent 成功，状态显示 "running"
- 聊天时返回 `Proxy upstream failure: All connection attempts failed`
- 直接访问 NodePort 端口可以访问 EFP Web UI

### 排查步骤

#### 1. 检查 Agent 状态
```bash
kubectl get pods -n efp-agents
kubectl get svc -n efp-agents | grep <agent-id>
```

#### 2. 检查 Service 类型
```bash
kubectl get svc <agent-svc-name> -n efp-agents -o jsonpath='{.spec.type}'
```

**说明**:
- `NodePort`: 当 Portal 在集群外部时使用（推荐开发环境）
- `ClusterIP`: 当 Portal 在集群内部时可使用（生产环境，更安全）

如果 Portal 在集群外部但显示 ClusterIP，需要设置 `K8S_AGENT_SERVICE_TYPE=NodePort`

#### 3. 检查 NodePort 端口
```bash
kubectl get svc <agent-svc-name> -n efp-agents -o jsonpath='{.spec.ports[0].nodePort}'
```

#### 4. 检查 Portal 日志

**Docker 部署**:
```bash
docker logs <container_name>
```

**K8s 部署**:
```bash
kubectl logs -n <namespace> <pod_name>
```

**Systemd 部署**:
```bash
journalctl -u portal -n 50
```

**直接部署**:
```bash
# 查看运行日志或日志文件
ps aux | grep uvicorn
tail -50 /var/log/portal.log
```

#### 5. 验证 ProxyService 的 node_ip

在 Portal 运行的机器上执行:
```bash
python3 -c "from app.services.proxy_service import ProxyService; print(ProxyService().node_ip)"
```

**应该是 Portal 所在节点的 IP**，可以通过以下命令确认:
```bash
hostname -I | awk '{print $1}'
```

#### 6. 手动测试连接
```bash
# 获取 NodePort
PORT=$(kubectl get svc <agent-svc-name> -n efp-agents -o jsonpath='{.spec.ports[0].nodePort}')
NODE_IP=$(hostname -I | awk '{print $1}')

# 测试连接
curl http://${NODE_IP}:${PORT}/
```

### 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Service 是 ClusterIP | `K8S_AGENT_SERVICE_TYPE` 未设置 | 设置 `K8S_AGENT_SERVICE_TYPE=NodePort` |
| 代理连接到错误 IP | 无法自动检测 node_ip | 设置环境变量 `K8S_NODE_IP=<正确IP>` |
| Agent 处于 ImagePullBackOff | 镜像不可访问 | 检查镜像仓库权限或使用公共镜像 |
| K8s 功能完全不工作 | `K8S_ENABLED=false` | 设置 `K8S_ENABLED=true` |

### 关键配置 (.env)

```bash
K8S_ENABLED=true
K8S_INCLUSTER=false
K8S_AGENT_SERVICE_TYPE=NodePort
# 可选: 手动指定节点 IP (优先级高于自动检测)
# K8S_NODE_IP=<portal-node-ip>
DEFAULT_AGENT_IMAGE_REPO=ghcr.io/dvnuo/engineering-flow-platform
DEFAULT_AGENT_IMAGE_TAG=latest
```

### 核心修复代码

`app/services/proxy_service.py`:
```python
@property
def node_ip(self):
    if self._node_ip is None:
        # 1. Try environment variable override first
        import os
        env_ip = os.environ.get('K8S_NODE_IP') or os.environ.get('NODE_IP')
        if env_ip:
            self._node_ip = env_ip
        else:
            # 2. Auto-detect via hostname -I
            import subprocess
            try:
                result = subprocess.run(
                    ['hostname', '-I'], capture_output=True, text=True
                )
                if result.returncode == 0:
                    # Filter for IPv4 addresses
                    ips = result.stdout.strip().split()
                    for ip in ips:
                        if '.' in ip and not ip.startswith('127.'):
                            self._node_ip = ip
                            break
                    if not self._node_ip and ips:
                        self._node_ip = ips[0]
            except Exception:
                pass
            
            # 3. Raise error if cannot determine
            if not self._node_ip:
                raise ValueError(
                    "Cannot determine node IP for K8s proxy. "
                    "Set K8S_NODE_IP environment variable."
                )
    return self._node_ip
```
