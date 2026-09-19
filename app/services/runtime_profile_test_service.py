import base64
import socket
from urllib.parse import urlparse

import httpx

from app.schemas.runtime_profile import PGSQL_DEFAULT_PORT
from app.services.ai_platform_config import materialize_ai_platform_llm_config


class RuntimeProfileTestService:
    def __init__(self, settings=None) -> None:
        self.settings = settings

    @staticmethod
    def _llm_base_url(llm_cfg: dict, default: str) -> str:
        for key in ("base_url", "api_base", "baseURL", "endpoint"):
            value = str(llm_cfg.get(key) or "").strip()
            if value:
                return value.rstrip("/")
        return default
    async def run_test(self, target: str, config: dict, runtime_type: str | None = None) -> tuple[bool, str]:
        if target == "proxy":
            return await self._test_proxy(config)
        if target == "github":
            return await self._test_github(config)
        if target == "jira":
            return await self._test_jira(config)
        if target == "confluence":
            return await self._test_confluence(config)
        if target == "jenkins":
            return await self._test_jenkins(config)
        if target == "nexus":
            return await self._test_nexus(config)
        if target == "splunk":
            return await self._test_splunk(config)
        if target == "appd":
            return await self._test_appd(config)
        if target == "pgsql":
            return await self._test_pgsql(config)
        if target == "llm":
            return await self._test_llm(config, runtime_type=runtime_type)
        return False, f"Unsupported test target: {target}"

    # ------------------------------------------------------------------
    # Instance sections tested against their default instance
    # ------------------------------------------------------------------

    @staticmethod
    def _default_instance(section_cfg: dict) -> dict | None:
        """The instance a test runs against: the named default, else the first.

        Mirrors the CLI, which uses the section's default_instance when a
        command carries no --instance. A disabled default is skipped, since
        the assistant could not use it either.
        """
        instances = [item for item in (section_cfg.get("instances") or []) if isinstance(item, dict)]
        usable = [item for item in instances if item.get("enabled") is not False]
        wanted = str(section_cfg.get("default_instance") or "").strip().lower()
        if wanted:
            for item in usable:
                if str(item.get("name") or "").strip().lower() == wanted:
                    return item
        return usable[0] if usable else (instances[0] if instances else None)

    @staticmethod
    def _basic_auth_header(username: str, secret: str) -> dict:
        encoded = base64.b64encode(f"{username}:{secret}".encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}

    @staticmethod
    def _instance_label(instance: dict, fallback: str) -> str:
        return str(instance.get("name") or fallback)

    async def _test_jenkins(self, config: dict) -> tuple[bool, str]:
        jenkins_cfg = config.get("jenkins") if isinstance(config.get("jenkins"), dict) else {}
        if not bool(jenkins_cfg.get("enabled")):
            return False, "Jenkins test requires jenkins.enabled=true."
        instance = self._default_instance(jenkins_cfg)
        base_url = str((instance or {}).get("url") or "").strip().rstrip("/")
        if not instance or not base_url:
            return False, "No usable Jenkins instance found. Provide a URL, and a username with an API token or password."

        username = str(instance.get("username") or "").strip()
        secret = str(instance.get("token") or instance.get("password") or "").strip()
        headers = self._basic_auth_header(username, secret) if username and secret else {}
        ok, message, data = await self._http_request(
            method="GET",
            url=f"{base_url}/whoAmI/api/json",
            headers={**headers, "Accept": "application/json"},
            timeout=15.0,
        )
        if not ok:
            return False, message
        who = data if isinstance(data, dict) else {}
        name = self._instance_label(instance, base_url)
        # whoAmI answers 200 for anonymous callers too; say which it was so a
        # rejected credential that Jenkins quietly downgraded is not read as OK.
        if who.get("authenticated") is True:
            return True, f"Jenkins connection OK for {name} as {who.get('name') or username or 'unknown user'}."
        return True, f"Jenkins reachable for {name}, but the request was not authenticated (anonymous access)."

    async def _test_nexus(self, config: dict) -> tuple[bool, str]:
        nexus_cfg = config.get("nexus") if isinstance(config.get("nexus"), dict) else {}
        if not bool(nexus_cfg.get("enabled")):
            return False, "Nexus test requires nexus.enabled=true."
        instance = self._default_instance(nexus_cfg)
        base_url = str((instance or {}).get("url") or "").strip().rstrip("/")
        if not instance or not base_url:
            return False, "No usable Nexus instance found. Provide a URL; add a username with a token or password unless anonymous reads are allowed."

        username = str(instance.get("username") or "").strip()
        secret = str(instance.get("token") or instance.get("password") or "").strip()
        headers = self._basic_auth_header(username, secret) if username and secret else {}
        ok, message, data = await self._http_request(
            method="GET",
            url=f"{base_url}/service/rest/v1/repositories",
            headers={**headers, "Accept": "application/json"},
            timeout=15.0,
        )
        if not ok:
            return False, message
        name = self._instance_label(instance, base_url)
        count = len(data) if isinstance(data, list) else 0
        mode = "authenticated" if headers else "anonymous"
        return True, f"Nexus connection OK for {name} ({mode}): {count} repositories visible."

    async def _test_splunk(self, config: dict) -> tuple[bool, str]:
        splunk_cfg = config.get("splunk") if isinstance(config.get("splunk"), dict) else {}
        if not bool(splunk_cfg.get("enabled")):
            return False, "Splunk test requires splunk.enabled=true."
        instance = self._default_instance(splunk_cfg)
        base_url = str((instance or {}).get("url") or "").strip().rstrip("/")
        if not instance or not base_url:
            return False, "No usable Splunk instance found. Provide the management API URL (port 8089) and a token or username+password."

        token = str(instance.get("token") or "").strip()
        username = str(instance.get("username") or "").strip()
        password = str(instance.get("password") or "").strip()
        if token:
            headers = {"Authorization": f"Bearer {token}"}
        elif username and password:
            headers = self._basic_auth_header(username, password)
        else:
            return False, "Splunk test needs an authentication token, or a username and password."
        ok, message, data = await self._http_request(
            method="GET",
            url=f"{base_url}/services/authentication/current-context?output_mode=json",
            headers={**headers, "Accept": "application/json"},
            timeout=15.0,
        )
        if not ok:
            return False, message
        name = self._instance_label(instance, base_url)
        entries = (data or {}).get("entry") if isinstance(data, dict) else None
        who = ""
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            content = entries[0].get("content") if isinstance(entries[0].get("content"), dict) else {}
            who = str(content.get("username") or entries[0].get("name") or "").strip()
        return True, f"Splunk connection OK for {name} as {who or username or 'token user'}."

    async def _test_appd(self, config: dict) -> tuple[bool, str]:
        appd_cfg = config.get("appd") if isinstance(config.get("appd"), dict) else {}
        if not bool(appd_cfg.get("enabled")):
            return False, "AppDynamics test requires appd.enabled=true."
        instance = self._default_instance(appd_cfg)
        base_url = str((instance or {}).get("url") or "").strip().rstrip("/")
        if not instance or not base_url:
            return False, "No usable AppDynamics instance found. Provide the controller URL, account, and an API client or user."

        account = str(instance.get("account") or "").strip()
        username = str(instance.get("username") or "").strip()
        if not account or not username:
            return False, "AppDynamics test needs the account name and an API client name or username."
        name = self._instance_label(instance, base_url)
        auth_type = str(instance.get("auth_type") or "api_client").strip().lower()
        applications_url = f"{base_url}/controller/rest/applications?output=JSON"

        if auth_type == "basic_password":
            password = str(instance.get("password") or "").strip()
            if not password:
                return False, "AppDynamics basic sign-in needs a password."
            login = username if "@" in username else f"{username}@{account}"
            headers = self._basic_auth_header(login, password)
        else:
            secret = str(instance.get("token") or "").strip()
            if not secret:
                return False, "AppDynamics API client sign-in needs the client secret (stored as the token)."
            ok, message, data = await self._http_request(
                method="POST",
                url=f"{base_url}/controller/api/oauth/access_token",
                headers={"Accept": "application/json"},
                form_payload={
                    "grant_type": "client_credentials",
                    "client_id": username if "@" in username else f"{username}@{account}",
                    "client_secret": secret,
                },
                timeout=15.0,
            )
            if not ok:
                return False, f"AppDynamics API client sign-in failed: {message}"
            access_token = str((data or {}).get("access_token") or "").strip() if isinstance(data, dict) else ""
            if not access_token:
                return False, "AppDynamics API client sign-in did not return an access token."
            headers = {"Authorization": f"Bearer {access_token}"}

        ok, message, data = await self._http_request(
            method="GET",
            url=applications_url,
            headers={**headers, "Accept": "application/json"},
            timeout=15.0,
        )
        if not ok:
            return False, message
        count = len(data) if isinstance(data, list) else 0
        return True, f"AppDynamics connection OK for {name}: {count} applications visible."

    async def _test_pgsql(self, config: dict) -> tuple[bool, str]:
        """Reachability only: the Portal often cannot see the database at all,
        and the credentials are exercised where they are used, inside the
        runtime, by `pgsql auth test`."""
        pgsql_cfg = config.get("pgsql") if isinstance(config.get("pgsql"), dict) else {}
        if not bool(pgsql_cfg.get("enabled")):
            return False, "PostgreSQL test requires pgsql.enabled=true."
        instance = self._default_instance(pgsql_cfg)
        host = str((instance or {}).get("host") or "").strip()
        if not instance or not host:
            return False, "No usable PostgreSQL instance found. Provide a host, database and username."
        try:
            port = int(instance.get("port") or PGSQL_DEFAULT_PORT)
        except (TypeError, ValueError):
            port = PGSQL_DEFAULT_PORT
        name = self._instance_label(instance, host)
        try:
            with socket.create_connection((host, port), timeout=5):
                pass
        except OSError as exc:
            return False, f"PostgreSQL connection failed for {name} at {host}:{port}: {exc}"
        return (
            True,
            f"PostgreSQL TCP reachability OK for {name}: {host}:{port}. "
            "Credentials are verified inside the runtime by `pgsql auth test`.",
        )

    async def _test_proxy(self, config: dict) -> tuple[bool, str]:
        proxy_cfg = config.get("proxy") if isinstance(config.get("proxy"), dict) else {}
        if not bool(proxy_cfg.get("enabled")):
            return False, "Proxy test requires proxy.enabled=true."

        proxy_url = str(proxy_cfg.get("url") or "").strip()
        if not proxy_url:
            return False, "Proxy URL is required."

        parsed = urlparse(proxy_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False, "Proxy URL must be a valid http(s) URL with a hostname."

        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        try:
            with socket.create_connection((host, port), timeout=5):
                pass
            return True, f"Proxy TCP reachability OK: {host}:{port}."
        except OSError as exc:
            return False, f"Proxy connection failed for {host}:{port}: {exc}"

    async def _test_github(self, config: dict) -> tuple[bool, str]:
        github_cfg = config.get("github") if isinstance(config.get("github"), dict) else {}
        if not bool(github_cfg.get("enabled")):
            return False, "GitHub test requires github.enabled=true."

        token = str(github_cfg.get("api_token") or "").strip()
        if not token:
            return False, "GitHub API token is required."

        base_url = str(github_cfg.get("base_url") or "https://api.github.com").strip().rstrip("/")
        endpoint = f"{base_url}/user"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }
        return await self._http_json_smoke(
            method="GET",
            url=endpoint,
            headers=headers,
            payload=None,
            timeout=15.0,
            success_message_builder=lambda data: f"GitHub connection OK as {data.get('login') or 'unknown user'}.",
        )

    async def _test_jira(self, config: dict) -> tuple[bool, str]:
        jira_cfg = config.get("jira") if isinstance(config.get("jira"), dict) else {}
        if not bool(jira_cfg.get("enabled")):
            return False, "Jira test requires jira.enabled=true."

        instance = self._first_auth_instance(jira_cfg.get("instances") or [])
        if not instance:
            return False, "No usable Jira instance found. Provide URL plus one of: username+token, token-only, or username+password."

        base_url = str(instance.get("url") or "").strip().rstrip("/")
        endpoint = f"{base_url}/rest/api/2/myself"
        headers = self._build_auth(instance)
        ok, message, data = await self._http_json_request(
            method="GET",
            url=endpoint,
            headers=headers,
            payload=None,
            timeout=15.0,
        )
        if not ok:
            return False, message
        display = (data or {}).get("displayName") or (data or {}).get("accountId") or "unknown"
        name = str(instance.get("name") or base_url)
        return True, f"Jira connection OK for {name} as {display}."

    async def _test_confluence(self, config: dict) -> tuple[bool, str]:
        confluence_cfg = config.get("confluence") if isinstance(config.get("confluence"), dict) else {}
        if not bool(confluence_cfg.get("enabled")):
            return False, "Confluence test requires confluence.enabled=true."

        instance = self._first_auth_instance(confluence_cfg.get("instances") or [])
        if not instance:
            return False, "No usable Confluence instance found. Provide URL plus one of: username+token, token-only, or username+password."

        base_url = str(instance.get("url") or "").strip().rstrip("/")
        endpoint = f"{base_url}/rest/api/space?limit=1"
        headers = self._build_auth(instance)
        ok, message, _data = await self._http_json_request(
            method="GET",
            url=endpoint,
            headers=headers,
            payload=None,
            timeout=15.0,
        )
        if not ok:
            return False, message
        name = str(instance.get("name") or base_url)
        return True, f"Confluence connection OK for {name}."

    async def _test_llm(self, config: dict, runtime_type: str | None = None) -> tuple[bool, str]:
        from app.contracts.llm_catalog import normalize_provider

        llm_cfg = config.get("llm") if isinstance(config.get("llm"), dict) else {}
        llm_cfg = materialize_ai_platform_llm_config(llm_cfg, settings=self.settings)
        provider = normalize_provider(llm_cfg.get("provider"))
        model = str(llm_cfg.get("model") or "").strip()

        if not model:
            return False, "LLM model is required."

        if provider == "ai_platform":
            return await self._test_ai_platform(llm_cfg, model, runtime_type=runtime_type)

        api_key = str(llm_cfg.get("api_key") or "").strip()
        if not api_key:
            return False, "LLM API key is required."

        api_base = self._llm_base_url(llm_cfg, "https://api.githubcopilot.com")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2023-06-01",
            "Accept": "application/vnd.github.copilot-chat-preview+json",
            "copilot-integration-id": "vscode-chat",
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
        }
        return await self._provider_request(provider, model, f"{api_base}/chat/completions", headers, payload)

    async def _provider_request(self, provider: str, model: str, endpoint: str, headers: dict, payload: dict) -> tuple[bool, str]:
        ok, message, _data = await self._http_json_request(
            method="POST",
            url=endpoint,
            headers=headers,
            payload=payload,
            timeout=20.0,
        )
        if not ok:
            return False, f"{provider}/{model} test failed: {message}"
        return True, f"LLM smoke test OK: {provider}/{model}."

    @staticmethod
    def _ai_platform_endpoint_is_responses(endpoint: str) -> bool:
        path = urlparse(endpoint).path.rstrip("/").lower()
        return path.endswith("/responses") or path.endswith("/responses/compact")

    async def _test_ai_platform(
        self,
        llm_cfg: dict,
        model: str,
        *,
        runtime_type: str | None = None,
    ) -> tuple[bool, str]:
        ap = llm_cfg.get("ai_platform") if isinstance(llm_cfg.get("ai_platform"), dict) else {}
        chat = ap.get("chat") if isinstance(ap.get("chat"), dict) else {}
        responses = ap.get("responses") if isinstance(ap.get("responses"), dict) else {}
        ib2b = ap.get("ib2b") if isinstance(ap.get("ib2b"), dict) else {}
        auth = ap.get("auth") if isinstance(ap.get("auth"), dict) else {}
        # Mirror the runtimes: native prefers the Responses endpoint when one is
        # configured, the OpenCode adapter only speaks chat/completions.
        use_responses = (
            str(runtime_type or "native").strip().lower() != "opencode"
            and bool(str(responses.get("uri") or "").strip())
        )
        endpoint_config = responses if use_responses else chat
        chat_host = str(endpoint_config.get("host") or chat.get("host") or "").strip()
        chat_uri = str(endpoint_config.get("uri") or "/v1/api/v1/chat/completions").strip()
        ib2b_host = str(ib2b.get("host") or "").strip()
        ib2b_uri = str(ib2b.get("uri") or "").strip()
        username = str(auth.get("username") or "").strip()
        password = str(auth.get("password") or "").strip()
        usercase = str(auth.get("usercase") or "").strip()
        trust_header = str(auth.get("trust_token_header") or "X-XXXX-E2E-Trust-Token").strip()
        prefix = str(auth.get("tracking_prefix") or "EFP").strip()
        token = str(auth.get("token") or "").strip()

        if not chat_host:
            return False, "AI Platform chat host is not configured in Portal."

        # Exchange username/password for a short-lived JWT via iB2B unless a token
        # was supplied directly.
        if not token:
            if not (username and password and usercase):
                return False, "AI Platform username, password, and usercase are required."
            if not (ib2b_host and ib2b_uri):
                return False, "AI Platform iB2B host/URI are not configured in Portal."
            ok, message, data = await self._http_json_request(
                method="POST",
                url=self._join_url(ib2b_host, ib2b_uri),
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                payload={
                    "input_token_state": {"token_type": "CREDENTIAL", "username": username, "password": password},
                    "output_token_state": {"token_type": "JWT"},
                },
                timeout=20.0,
            )
            if not ok:
                return False, f"AI Platform token exchange failed: {message}"
            token = str((data or {}).get("issued_token") or "").strip()
            if not token:
                return False, "AI Platform token exchange did not return issued_token."

        endpoint = self._join_url(chat_host, chat_uri)
        tracking = f"{prefix}-smoketest"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            trust_header: token,
            "x-correlation-id": tracking,
            "x-usersession-id": tracking,
        }
        if self._ai_platform_endpoint_is_responses(endpoint):
            # The Responses gateway also expects the JWT as a Bearer token and
            # routes the use case through the URL rather than a ``user`` field.
            headers["Authorization"] = f"Bearer {token}"
            payload: dict = {"model": model, "input": "ping"}
        else:
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_completion_tokens": 1,
            }
            if usercase:
                payload["user"] = usercase
        return await self._provider_request("ai_platform", model, endpoint, headers, payload)

    @staticmethod
    def _join_url(host: str, uri: str) -> str:
        host = host.rstrip("/")
        uri = uri.strip()
        if uri.startswith("http://") or uri.startswith("https://"):
            return uri
        if not uri.startswith("/"):
            uri = "/" + uri
        return host + uri

    @staticmethod
    def _first_auth_instance(instances: list) -> dict | None:
        for item in instances:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            token = str(item.get("token") or "").strip()
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "").strip()
            has_username_token = bool(username and token)
            has_token_only = bool(token and not username)
            has_username_password = bool(username and password)
            if url and (has_username_token or has_token_only or has_username_password):
                return item
        return None

    @staticmethod
    def _build_auth(instance: dict) -> dict:
        username = str(instance.get("username") or "").strip()
        token = str(instance.get("token") or "").strip()
        if username and token:
            encoded = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        if token:
            return {"Authorization": f"Bearer {token}"}
        password = str(instance.get("password") or "").strip()
        if username and password:
            encoded = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}
        return {}

    async def _http_json_smoke(self, method: str, url: str, headers: dict, payload: dict | None, timeout: float, success_message_builder):
        ok, message, data = await self._http_json_request(
            method=method,
            url=url,
            headers=headers,
            payload=payload,
            timeout=timeout,
        )
        if not ok:
            return False, message
        return True, success_message_builder(data or {})

    async def _http_json_request(
        self,
        method: str,
        url: str,
        headers: dict,
        payload: dict | None,
        timeout: float,
    ) -> tuple[bool, str, dict | None]:
        ok, message, data = await self._http_request(
            method=method,
            url=url,
            headers=headers,
            json_payload=payload,
            timeout=timeout,
        )
        return ok, message, data if isinstance(data, dict) else None

    async def _http_request(
        self,
        method: str,
        url: str,
        headers: dict,
        timeout: float,
        json_payload: dict | None = None,
        form_payload: dict | None = None,
    ):
        """One request; returns (ok, message, parsed JSON of any shape or None).

        Nexus and AppDynamics answer with JSON lists, and the AppDynamics OAuth
        step posts a form rather than JSON, which is why this sits under the
        dict-only helper the older tests use. The failure message carries the
        status and the server's own error text, never the request headers.
        """
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    json=json_payload,
                    data=form_payload,
                )
        except Exception as exc:
            return False, f"Request failed: {exc}", None

        data = None
        try:
            data = response.json()
        except Exception:
            data = None

        if response.status_code >= 400:
            detail = ""
            if isinstance(data, dict):
                detail = str(data.get("error") or data.get("message") or data.get("detail") or "")
            if not detail:
                detail = response.text[:240]
            return False, f"HTTP {response.status_code}: {detail}", data

        return True, "ok", data
