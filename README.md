# Engineering Flow Platform Portal (v1 Spec)

这是一个面向内部团队的机器人门户（Portal）项目说明。目标是先快速实现一个可运行、可演进的 v1：

- **FastAPI Portal**
- **SQLite（单实例）**
- **EKS 上单副本 Deployment + EBS PVC**
- **由 Portal 调用 Kubernetes API 动态创建机器人资源**

---

## 当前实现进度（已落地）

- ✅ FastAPI 应用骨架（`app/main.py`）
- ✅ SQLite + SQLAlchemy 模型（`users`/`robots`/`audit_logs`）
- ✅ 基础认证 API（login/logout/me，cookie session）
- ✅ 管理员用户 API（创建/列表/改密）
- ✅ 机器人 API（mine/public/create/detail/start/stop/share/unshare/delete/status + delete-runtime/destroy）
- ✅ 管理端 API（/api/admin/robots, /api/admin/audit-logs）
- ✅ k8s_service 抽象与机器人生命周期接口接入（支持本地 no-op 模式）
- ✅ `/r/{robot_id}` 反向代理访问入口（含权限与运行状态校验）
- ✅ Dockerfile 与依赖清单
- ✅ 机器人状态流转约束（start/stop 按状态机校验）

### 本地启动

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
```

默认会在首次启动时创建管理员账号：

- username: `admin`（默认，可通过环境变量覆盖）
- password: `admin123`（默认，仅限本地开发）


### Kubernetes 开关（开发/生产）

可通过环境变量控制是否真实调用 Kubernetes API：

- `K8S_ENABLED=false`（默认，本地 no-op）
- `K8S_ENABLED=true`（启用真实 K8s 调用）
- `ROBOTS_NAMESPACE=robots`
- `K8S_STORAGE_CLASS=gp3`
- `BOOTSTRAP_ADMIN_USERNAME=admin`
- `BOOTSTRAP_ADMIN_PASSWORD=admin123`

---

## 1. v1 范围

### 包含

- 本地账号密码登录（session）
- 管理员手动创建用户
- My Space（我的机器人）/ Public Space（公开机器人）
- 机器人创建、查看、启动、停止、删除
- 机器人分享/取消分享
- 机器人运行状态与错误信息查看
- Portal 运行在 EKS
- 每个机器人对应：
  - 1 个 Deployment（`replicas=1`）
  - 1 个 Service
  - 1 个 PVC（独立存储）

### 不包含

- SSO
- 每用户独立 namespace
- 复杂 RBAC / 审批流
- 多副本 Portal
- PostgreSQL / RDS
- Operator / CRD
- 细粒度协作者模型

---

## 2. 总体架构

```text
[Browser]
   |
   v
[ALB/Ingress]
   |
   v
[Portal Web/API - FastAPI]
   | \
   |  \--> [SQLite on EBS PVC]
   |
   \--> [Kubernetes API]
            |
            +--> create Deployment for robot
            +--> create PVC for robot
            +--> create Service for robot
            +--> query Pod / Deployment status
```

建议 namespace：

- `portal-system`（Portal）
- `robots`（机器人工作负载）

---

## 3. 数据模型（最小可用）

### `users`

- `id` INTEGER PK
- `username` TEXT UNIQUE NOT NULL
- `password_hash` TEXT NOT NULL
- `role` TEXT NOT NULL (`admin`/`user`)
- `is_active` BOOLEAN NOT NULL DEFAULT 1
- `created_at` DATETIME NOT NULL
- `updated_at` DATETIME NOT NULL

### `robots`

- `id` TEXT PK (uuid)
- `name` TEXT NOT NULL
- `description` TEXT
- `owner_user_id` INTEGER NOT NULL (FK users.id)
- `visibility` TEXT NOT NULL (`private`/`public`)
- `status` TEXT NOT NULL (`creating`/`running`/`stopped`/`deleting`/`failed`)
- `image` TEXT NOT NULL
- `cpu` TEXT
- `memory` TEXT
- `disk_size_gi` INTEGER NOT NULL
- `mount_path` TEXT NOT NULL DEFAULT `/data`
- `namespace` TEXT NOT NULL
- `deployment_name` TEXT NOT NULL
- `service_name` TEXT NOT NULL
- `pvc_name` TEXT NOT NULL
- `endpoint_path` TEXT
- `last_error` TEXT
- `created_at` DATETIME NOT NULL
- `updated_at` DATETIME NOT NULL

### `audit_logs`

- `id` INTEGER PK
- `user_id` INTEGER
- `action` TEXT NOT NULL
- `target_type` TEXT NOT NULL (`user`/`robot`)
- `target_id` TEXT NOT NULL
- `details_json` TEXT
- `created_at` DATETIME NOT NULL

---

## 4. API 契约（v1）

### 认证与用户

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`
- `POST /api/users` (admin)
- `GET /api/users` (admin)
- `PATCH /api/users/{id}/password`

### 机器人

- `GET /api/robots/mine`
- `GET /api/robots/public`
- `POST /api/robots`
- `GET /api/robots/{id}`
- `POST /api/robots/{id}/start`
- `POST /api/robots/{id}/stop`
- `POST /api/robots/{id}/share`
- `POST /api/robots/{id}/unshare`
- `DELETE /api/robots/{id}`
- `POST /api/robots/{id}/delete-runtime`
- `POST /api/robots/{id}/destroy`
- `GET /api/robots/{id}/status`

### 管理端

- `GET /api/admin/robots`
- `GET /api/admin/audit-logs`

---

## 5. 机器人生命周期

状态机：

```text
creating -> running -> stopped
   |          |         |
   +--------> failed <--+
            \
             -> deleting
```

创建流程（简化）：

1. 用户提交 `name/image/disk_size_gi/cpu/memory`
2. 写入 DB，状态设为 `creating`
3. 创建 PVC
4. 创建 Deployment（`replicas=1`）
5. 创建 Service
6. 检查 PVC Bound + Pod Ready
7. 成功更新 `running`，失败更新 `failed + last_error`

停止：Deployment scale 到 0。  
启动：Deployment scale 到 1。  
删除：优先支持两种动作：

- `Delete Runtime`（删 Deployment + Service，保留 PVC）
- `Destroy Completely`（Deployment + Service + PVC）

---

## 6. 建议目录结构

```text
portal/
  app/
    main.py
    config.py
    db.py
    deps.py

    models/
      user.py
      robot.py
      audit_log.py

    schemas/
      auth.py
      user.py
      robot.py

    api/
      auth.py
      users.py
      robots.py
      admin.py

    services/
      auth_service.py
      user_service.py
      robot_service.py
      k8s_service.py
      proxy_service.py
      audit_service.py

    repositories/
      user_repo.py
      robot_repo.py
      audit_repo.py

    templates/
      login.html
      my_robots.html
      create_robot.html
      robot_detail.html
      public_space.html
      admin_users.html

    static/
      css/
      js/

  migrations/
  tests/
  Dockerfile
  requirements.txt
  alembic.ini
```

---

## 7. 访问路径建议

v1 推荐统一入口代理：

- 用户访问：`https://portal.example.com/r/{robot_id}/...`
- Portal 内部代理到：`http://robot-<id>-svc.robots.svc.cluster.local`

优势：

- 权限检查集中在 Portal
- 不需要给每个机器人维护独立 Ingress
- 更适合 v1 多租户管理

---

## 8. Kubernetes 最小权限建议

Portal 的 ServiceAccount 只授予 `robots` namespace 的必要权限：

- Deployments：`get/list/watch/create/delete/patch`
- PVC：`get/list/watch/create/delete`
- Services：`get/list/watch/create/delete`
- Pods：`get/list/watch`

资源统一打标签：

- `owner-id`
- `robot-id`
- `visibility`

---

## 9. 分阶段落地计划

### Phase 1：Portal 基础可用

- FastAPI 项目初始化
- SQLite + Alembic
- 登录/会话
- 用户管理
- My/Public Space 基础页面

### Phase 2：机器人管理（不接 K8s）

- robots CRUD
- 状态机与页面
- 审计日志基础

### Phase 3：接入 Kubernetes

- `k8s_service.py`（PVC/Deployment/Service 生命周期）
- 状态轮询与错误回写

### Phase 4：代理与分享

- `/r/{robot_id}` 请求代理
- Public Space 展示打通
- 运维可观测性补齐

---

## 10. 给 Codex 的任务拆分（可直接执行）

1. 初始化项目骨架（FastAPI + Jinja2 + SQLAlchemy + Alembic + Dockerfile）
2. 定义 `users/robots/audit_logs` 数据模型与迁移
3. 实现 session 登录与用户管理 API
4. 实现 My Space / Public Space 页面与查询 API
5. 实现 robots 的创建/启动/停止/删除/分享 API
6. 实现 Kubernetes 资源管理服务（create/scale/delete/status）
7. 实现 `/r/{robot_id}` 反向代理
8. 补充测试、README、部署 YAML 与运维说明

---

## 11. 成功标准（Definition of Done）

- 管理员可创建用户并登录
- 普通用户只能管理自己的机器人
- 用户能创建机器人并在页面看到 `creating -> running`
- 用户能停止并再次启动机器人
- 分享后机器人可在 Public Space 可见
- 删除运行时不会误删数据（默认保留 PVC）
- 审计日志可追踪关键动作
- 在 EKS 单副本部署可稳定运行
