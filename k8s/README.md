# Kubernetes Deployment Guide

This guide explains the example manifests in this directory. Start with the [main README](../README.md) for Portal concepts and application settings. Run all commands below from the **repository root** with a `kubectl` context pointing at your intended cluster.

The examples create Portal in the `default` namespace and assistant runtimes in `efp-agents`. They require a Linux Kubernetes node, permission to create the included storage/RBAC resources, access to the configured image and Git repositories, and persistent storage. They are configuration examples: review the storage and placeholder authentication settings before applying a deployment.

## 1. Create the assistant namespace and Portal permissions

```bash
kubectl config current-context
kubectl apply -f k8s/namespaces.yaml
kubectl apply -f k8s/service-account.yaml
```

`service-account.yaml` creates the `portal` ServiceAccount in `default` and grants it management access to core and apps resources in `efp-agents`. Both Portal deployment examples reference this ServiceAccount. If you change namespaces, update the ServiceAccount, RoleBinding subject, manifests, `AGENTS_NAMESPACE`, and internal DNS URL together.

## 2. Prepare persistent storage

Read `k8s/efp-efs-pvc.yaml` before applying it. Despite the `efs` filename, the checked-in example uses **node-local `hostPath` storage**, not Amazon EFS:

| Claim | Namespace | Backing directory in the example | Contents |
| --- | --- | --- | --- |
| `efp-portal-efs-pvc` | `default` | `/data/portal` | Portal database and cloned Portal source |
| `efp-agents-efs-pvc` | `efp-agents` | `/data/agents` | Shared assistant volume, separated by assistant subdirectories |

The `ReadWriteMany` declaration does not turn node-local directories into shared storage. The sample volumes have no node affinity, so pods scheduled to another node can see a different directory. Use this example only for a single-node development cluster, or replace its backing volumes with storage appropriate for your cluster. For a multi-node deployment, provide storage accessible from all runtime nodes for the shared assistant claim and plan Portal database persistence explicitly.

For the single-node example:

```bash
kubectl apply -f k8s/efp-efs-pvc.yaml
kubectl get pvc -n default
kubectl get pvc -n efp-agents
```

Both claims should become `Bound`. If they stay `Pending`, inspect them with `kubectl describe pvc <claim-name> -n <namespace>` before proceeding.

Portal can create a missing shared assistant PVC using `K8S_STORAGE_CLASS` (default `local-path`) and `K8S_PVC_ACCESS_MODES` (default `["ReadWriteOnce"]`). These defaults are separate from the static example above. An existing claim is reused; changing those settings or an individual assistant's disk setting does not automatically resize or replace it.

## 3. Configure secrets

Prepare the following Secrets using your normal secret-management workflow. The checked-in YAML files contain empty placeholders; do not commit populated credentials.

| Secret | Namespace | Key | Purpose |
| --- | --- | --- | --- |
| `efp-portal-secret` | `default` | `BOOTSTRAP_ADMIN_PASSWORD` | Creates the initial `admin` account when it does not exist. |
| `efp-portal-secret` | `default` | `GIT_TOKEN` (optional for public repositories) | Clones Portal source and supplies `GIT_REPO_AUTH_PAT` for private repository branch lookup. |
| `efp-portal-secret` | `default` | `SECRET_KEY` (required) | Stable, private key that signs Portal sessions and from which each assistant's trust token is derived. Both deployment examples read it from this Secret. |
| `efp-agents-secret` | `efp-agents` | `GIT_TOKEN` (optional for public repositories) | Clones agent-settings and skills assets. |

After filling the relevant files locally or rendering them from a secret store:

```bash
kubectl apply -f k8s/portal-git-clone/efp-portal-secret.yaml
kubectl apply -f k8s/efp-agents-secret.yaml
```

Generate `SECRET_KEY` once and store it under that key in `efp-portal-secret`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Both deployment examples already pass it to the Portal container:

```yaml
- name: SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: efp-portal-secret
      key: SECRET_KEY
      optional: true
```

Outside debug mode the Portal refuses to start while `SECRET_KEY` is unset, empty or still the built-in default, and says so in the pod log. An installation that has been running without a key can set `ALLOW_INSECURE_SECRET_KEY=true` on the Portal container to keep starting, with a warning at every start, until its operator has added one. Setting or changing the key signs every member out and changes every assistant's trust token, so restart the assistants afterwards (see [Trust between Portal, assistants and other sites](#8-trust-between-portal-assistants-and-other-sites)).

The deployment does not import all Secret keys automatically. Keep `SECRET_KEY` stable across restarts. The bootstrap password initializes a missing admin account; changing it does not reset an existing admin's password.

Assistant Git clones use HTTPS with `GIT_ASKPASS`. The username is fixed to `x-access-token`; URLs are not rewritten to embed tokens. `K8S_GIT_TOKEN_KEY` defaults to `GIT_TOKEN` and can select a different key in `efp-agents-secret`. There is no separate tools-repository clone.

## 4. Choose and configure one Portal deployment

There are two alternative manifests, both defining the same Deployment and Service names. **Apply only one.** Both use an init container to clone Portal's configured Git branch and mount its application and Alembic files into the Portal image.

| Manifest | Portal Service type | Typical access method |
| --- | --- | --- |
| `k8s/portal-git-clone/efp-portal-deployment.yaml` | `ClusterIP` | Ingress or temporary port-forward |
| `k8s/efp-portal-deployment.yaml` | `NodePort` | A reachable node address plus the allocated Portal NodePort |

Review these values in your selected manifest before applying it:

- `GIT_REPO_URL`, `GIT_BRANCH`, and the Portal image must match the release you intend to run. A moving `master` branch and `latest` image can change on restart. The source overlay does not install new Python dependencies, so keep the image and source compatible.
- `BASE_URI` is the browser-facing Portal origin, including the scheme, for example `https://portal.example.com`.
- Both examples include a placeholder `SSO_ISSUER_URL`. Replace it with your real identity provider, or set it to `""` to disable company SSO. Set `SSO_CLIENT_ID` and any required `SSO_CLIENT_SECRET` for your provider, and register the callback at `<BASE_URI>/auth`. The bootstrap administrator can always open `/admlogin` for the password form; `/login` shows that form only when company SSO and Copilot login are both disabled.
- Replace or clear the example `GITHUB_ENTERPRISE_SSO_URL` and `GITHUB_USERNAME_SUFFIX`. Set `COPILOT_LOGIN_ENABLED=false` if that sign-in method is unavailable in your deployment.
- Configure `PORTAL_USER_ALLOWLIST` for initial permitted members, or manage allowed users from the admin interface after bootstrap. An empty initial list does not mean open registration.
- `SECRET_KEY` comes from `efp-portal-secret` as described in the previous section; the Portal does not start without it.
- Keep `K8S_ENABLED=true`, `K8S_INCLUSTER=true`, and `AGENTS_NAMESPACE=efp-agents` for these manifests.
- Keep `PORTAL_INTERNAL_BASE_URL=http://efp-portal-service.default.svc.cluster.local` when using the supplied names. Runtime pods must be able to reach this URL.
- Choose allowed engines with `ENABLED_RUNTIME_TYPES` (default `native`). Use `native,opencode` to offer both supported engines. Configure the corresponding image repository/tag settings described below.

The default runtime Service type is `ClusterIP`, which is appropriate because this Portal runs inside the cluster. Selecting the NodePort **Portal** manifest does not require NodePort **assistant** Services.

Apply your selected, configured manifest. For the ClusterIP example:

```bash
kubectl apply -f k8s/portal-git-clone/efp-portal-deployment.yaml
kubectl rollout status deployment/efp-portal-deployment -n default --timeout=300s
kubectl logs -n default deployment/efp-portal-deployment -c portal-container --tail=100
```

The Portal image's default startup command runs `alembic upgrade head` before starting Uvicorn. Preserve that behavior if you override the command: migrations are required for both first-time setup and upgrades. For a manually managed Python process, run `alembic upgrade head` against the configured database before startup.

If deployment fails during cloning, inspect the init container:

```bash
kubectl logs -n default deployment/efp-portal-deployment -c git-clone --tail=100
```

## 5. Open Portal

For a first local check, a port-forward works with either Service type:

```bash
kubectl port-forward -n default service/efp-portal-service 8080:80
```

Keep that terminal open and visit `http://localhost:8080/admlogin` for a bootstrap password-login check. Sign in with the configured bootstrap username (default `admin`) and password. This explicit admin route provides the password form even when external sign-in methods are enabled. To show the password form at `/login` as well, set `SSO_ISSUER_URL=""` and `COPILOT_LOGIN_ENABLED=false`. For SSO, use the public origin and callback registered with the identity provider.

For ingress access, use the ingress controller already supported by your cluster. `k8s/efp-portal-ingress.yaml` expects an `nginx` ingress class and contains ingress-nginx-specific timeout and upload annotations. Adjust its class and annotations for your controller, and configure your host name, TLS, and DNS before exposing it publicly. This file creates an Ingress resource; it does not install a controller or configure a certificate.

The rule in that file names a host, `portal.example.com`: replace it with the host of `BASE_URI`. A rule without a host matches every name that resolves to the ingress controller, so the Portal would also answer on other names, including a sibling subdomain that serves member apps. With the host set, the Portal is reachable under its own name only.

```bash
kubectl get ingressclass
kubectl apply -f k8s/efp-portal-ingress.yaml
kubectl get ingress -n default efp-portal-ingress
```

If your existing controller uses the sample `ingress-nginx` namespace, you can inspect its exposed address with `kubectl get service -n ingress-nginx`. Otherwise use your controller's namespace and Service.

For the NodePort Portal alternative, find its allocated port with:

```bash
kubectl get service -n default efp-portal-service -o wide
```

Visit `http://<reachable-node-ip>:<node-port>` for an internal development check. The image trusts forwarded headers by default; when exposing Portal directly, configure `FORWARDED_ALLOW_IPS` to match the proxies you actually trust.

## 6. Understand assistant provisioning

Portal supports `native` and `opencode` runtime markers. `ENABLED_RUNTIME_TYPES` controls which are offered for new assistants and runtime switching. Existing assistants keep their stored runtime choice.

| Setting | Default | Purpose |
| --- | --- | --- |
| `DEFAULT_RUNTIME_TYPE` | `native` | Default engine for new assistants, subject to enabled engines. |
| `DEFAULT_AGENT_IMAGE_REPO` | `ghcr.io/dvnuo/engineering-flow-platform` | Native runtime image repository. |
| `DEFAULT_AGENT_IMAGE_TAG` | `latest` | Native runtime image tag. |
| `DEFAULT_OPENCODE_RUNTIME_IMAGE_REPO` | `ghcr.io/dvnuo/efp-opencode-runtime` | OpenCode runtime image repository. |
| `DEFAULT_OPENCODE_RUNTIME_IMAGE_TAG` | `1.14.39` | OpenCode runtime image tag. |
| `DEFAULT_SKILL_REPO_URL` | `https://github.com/dvnuo/engineering-flow-platform-skills` | Skills assets, cloned into `/app/skills`. |
| `DEFAULT_AGENT_SETTINGS_REPO_URL` | `https://github.com/dvnuo/engineering-flow-platform-agents` | Workspace instructions and assistant settings assets. |

New assistants use `/workspace` for either runtime unless the creation request explicitly supplies `mount_path`. Although `DEFAULT_AGENT_MOUNT_PATH` exists in configuration, the current create API uses its `/workspace` constant rather than that setting. Portal mounts workspace data at the effective workspace path and uses it as the runtime container's working directory. Native and OpenCode have their own state/configuration wiring. Portal does not apply a runtime source overlay to assistant pods: it does not clone runtime source into `/app/src` or `/app/.git`. The Portal source clone described earlier belongs to the Portal application itself.

Runtime pods use the shared `efp-agents-efs-pvc`, with separate assistant subdirectories. Built-in tools, skill execution, loop control, context shaping, compaction, sessions, and permissions belong to the runtime. See the [Portal/runtime contract](../docs/PORTAL_RUNTIME_CONTRACT.md) for the detailed interface and profile configuration.

After creating an assistant in Portal, verify its deployment and readiness:

```bash
kubectl get deployments,pods,services -n efp-agents
kubectl get events -n efp-agents --sort-by=.metadata.creationTimestamp
```

## 7. Upgrade and troubleshoot

Back up the Portal database and persistent assistant data before an upgrade. Update the image/source references deliberately, then apply the selected manifest and wait for rollout. If only the remote branch changed and the manifest stayed identical, an administrator can restart Portal to rerun the clone:

```bash
kubectl rollout restart deployment/efp-portal-deployment -n default
kubectl rollout status deployment/efp-portal-deployment -n default --timeout=300s
```

Do not assume an application rollback reverses database migrations. Verify migration and startup logs, sign-in, assistant readiness, and a chat request after upgrading. For failed pods, wrong node addresses, proxy failures, storage issues, and long-running streams, use the [Kubernetes troubleshooting guide](../docs/K8S_TROUBLESHOOTING.md).

## 8. Trust between Portal, assistants and other sites

These checks are on by default. They matter most once pages or code that members wrote are served next to the Portal (a sibling subdomain, the same cluster), and they need a little care during an upgrade.

| Setting | Default | What it does |
| --- | --- | --- |
| `SECRET_KEY` | none; required | Signs sessions and derives each assistant's trust token. See section 3. |
| `ALLOW_INSECURE_SECRET_KEY` | `false` | `true` lets the Portal start with the built-in default key, with a warning at every start. |
| `ORIGIN_GUARD_ENABLED` | `true` | A POST, PUT, PATCH or DELETE that carries the session cookie is answered 403 unless the browser says it came from the Portal's own pages (`Sec-Fetch-Site`, else `Origin`). The events WebSocket checks its `Origin` the same way. Requests without the cookie, requests from non-browser clients, and the token-authenticated prefixes `/api/internal/`, `/api/app-sdk/` and `/api/app-host/` are not affected. |
| `INTERNAL_API_BLOCK_FORWARDED` | `true` | `/api/internal/` and `/api/app-host/` answer 404 to a request that carries `X-Forwarded-For` or `X-Real-IP`, that is, one that came through the ingress. Runtimes call `PORTAL_INTERNAL_BASE_URL` directly and are not affected. Set it to `false` only if your runtimes can reach the Portal solely through a proxy that adds one of those headers. |
| `INTERNAL_API_AUTH_REQUIRED` | `false` | `/api/internal/agents/{agent_id}/...` always rejects a wrong `X-Portal-Trust-Token`. With `true` it also rejects a call without one. |

**Session cookie.** When `BASE_URI` starts with `https://` and `SESSION_COOKIE_NAME` is left at its default, the session cookie is `__Host-portal_session` and is sent with `Secure`. Browsers accept such a cookie only from the Portal's own host over https, so another subdomain can neither read nor replace it. The cookie under the old name is no longer read: every member signs in once more after the upgrade. Without https, or with a custom cookie name, the cookie stays as it was and the Portal logs a warning at startup.

**Assistant trust token.** The Portal gives every assistant pod `EFP_PORTAL_TRUST_TOKEN` (derived from `SECRET_KEY` and the assistant's id) and sends the same value as `X-Portal-Trust-Token` on each request to that assistant's runtime. A runtime image that knows the token rejects requests that claim to come from the Portal without it, and sends it on its own calls to `/api/internal/`. Roll it out in this order:

1. Upgrade the Portal (with `SECRET_KEY` set). It sends the header from then on; older runtime images ignore it.
2. Upgrade the runtime images.
3. Restart each assistant, so its pod receives the token.
4. When the Portal log no longer reports internal API calls without a token, set `INTERNAL_API_AUTH_REQUIRED=true`.

Changing `SECRET_KEY` later changes every token: restart the assistants after the Portal, or their runtimes reject the Portal until they are restarted.

A Portal that is reachable without a reverse proxy in front of it (the NodePort example, a port-forward) receives no `X-Forwarded-For`, so `INTERNAL_API_BLOCK_FORWARDED` cannot tell outside callers from runtimes there. Only the token check protects `/api/internal/` in that setup: complete the four steps and turn `INTERNAL_API_AUTH_REQUIRED` on.

**Origin check behind a proxy that rewrites `Host`.** Current browsers state where a request came from (`Sec-Fetch-Site`) whenever the Portal is served over https or from localhost, and the check needs nothing else. Over plain http the Portal falls back to comparing `Origin` with the request's `Host` header and with `BASE_URI`; if your proxy replaces `Host`, set `BASE_URI` to the address members type, or their own saves are answered 403.

**Single sign-on.** `/login/sso` now sends a random `state` to the identity provider and `/auth` only accepts the callback that returns it to the same browser. Nothing has to be configured at the identity provider.
