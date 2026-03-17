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
应该是 `NodePort`，不是 `ClusterIP`

#### 3. 检查 NodePort 端口
```bash
kubectl get svc <agent-svc-name> -n efp-agents -o jsonpath='{.spec.ports[0].nodePort}'
```

#### 4. 检查 Portal 日志
```bash
tail -50 /var/log/portal.log
```

#### 5. 验证 ProxyService 的 node_ip
```bash
cd /root/engineering-flow-platform-portal
python3 -c "from app.services.proxy_service import ProxyService; print(ProxyService().node_ip)"
```
**应该是 Portal 所在节点的 IP**（192.168.8.235），不是其他节点 IP

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
| 代理连接到错误 IP | `proxy_service.py` 硬编码了 node_ip | 使用 `hostname -I` 自动检测 |
| Agent 处于 ImagePullBackOff | 镜像不可访问 | 检查镜像仓库权限或使用公共镜像 |
| K8s 功能完全不工作 | `K8S_ENABLED=false` | 设置 `K8S_ENABLED=true` |

### 关键配置 (.env)

```bash
K8S_ENABLED=true
K8S_INCLUSTER=false
K8S_AGENT_SERVICE_TYPE=NodePort
DEFAULT_AGENT_IMAGE_REPO=ghcr.io/dvnuo/engineering-flow-platform
DEFAULT_AGENT_IMAGE_TAG=latest
```

### 核心修复代码

`app/services/proxy_service.py`:
```python
@property
def node_ip(self):
    if self._node_ip is None:
        import subprocess
        try:
            result = subprocess.run(['hostname', '-I'], capture_output=True, text=True)
            if result.returncode == 0:
                self._node_ip = result.stdout.strip().split()[0]
            else:
                self._node_ip = "192.168.8.235"  # Fallback
        except Exception:
            self._node_ip = "192.168.8.235"  # Fallback
    return self._node_ip
```
