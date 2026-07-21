import base64
import socket
from urllib.parse import urlparse

import httpx



class RuntimeProfileTestService:
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
        if target == "llm":
            return await self._test_llm(config, runtime_type=runtime_type)
        return False, f"Unsupported test target: {target}"

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
        provider = normalize_provider(llm_cfg.get("provider"))
        model = str(llm_cfg.get("model") or "").strip()
        _ = runtime_type

        if not model:
            return False, "LLM model is required."

        if provider == "ai_platform":
            return await self._test_ai_platform(llm_cfg, model)

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

    async def _test_ai_platform(self, llm_cfg: dict, model: str) -> tuple[bool, str]:
        ap = llm_cfg.get("ai_platform") if isinstance(llm_cfg.get("ai_platform"), dict) else {}
        chat = ap.get("chat") if isinstance(ap.get("chat"), dict) else {}
        ib2b = ap.get("ib2b") if isinstance(ap.get("ib2b"), dict) else {}
        auth = ap.get("auth") if isinstance(ap.get("auth"), dict) else {}
        chat_host = str(chat.get("host") or "").strip()
        chat_uri = str(chat.get("uri") or "/v1/api/v1/chat/completions").strip()
        ib2b_host = str(ib2b.get("host") or "").strip()
        ib2b_uri = str(ib2b.get("uri") or "").strip()
        username = str(auth.get("username") or "").strip()
        password = str(auth.get("password") or "").strip()
        usercase = str(auth.get("usercase") or "").strip()
        trust_header = str(auth.get("trust_token_header") or "X-XXXX-E2E-Trust-Token").strip()
        prefix = str(auth.get("tracking_prefix") or "EFP").strip()
        token = str(auth.get("token") or "").strip()

        if not chat_host:
            return False, "AI Platform chat host is required."

        # Exchange username/password for a short-lived JWT via iB2B unless a token
        # was supplied directly.
        if not token:
            if not (username and password and ib2b_host and ib2b_uri):
                return False, "AI Platform requires a token, or username/password plus iB2B host/uri."
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

        tracking = f"{prefix}-smoketest"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            trust_header: token,
            "x-correlation-id": tracking,
            "x-usersession-id": tracking,
        }
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_completion_tokens": 1,
        }
        if usercase:
            payload["user"] = usercase
        return await self._provider_request("ai_platform", model, self._join_url(chat_host, chat_uri), headers, payload)

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
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method=method, url=url, headers=headers, json=payload)
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
            return False, f"HTTP {response.status_code}: {detail}", data if isinstance(data, dict) else None

        return True, "ok", data if isinstance(data, dict) else None
