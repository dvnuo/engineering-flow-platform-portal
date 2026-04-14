import socket
from urllib.parse import urlparse

import httpx


class RuntimeProfileTestService:
    async def run_test(self, target: str, config: dict) -> tuple[bool, str]:
        if target == "proxy":
            return await self._test_proxy(config)
        if target == "github":
            return await self._test_github(config)
        if target == "jira":
            return await self._test_jira(config)
        if target == "confluence":
            return await self._test_confluence(config)
        if target == "llm":
            return await self._test_llm(config)
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
            return False, "No usable Jira instance found. Provide URL plus token or username/password."

        base_url = str(instance.get("url") or "").strip().rstrip("/")
        endpoint = f"{base_url}/rest/api/2/myself"
        headers, auth = self._build_auth(instance)
        ok, message, data = await self._http_json_request(
            method="GET",
            url=endpoint,
            headers=headers,
            auth=auth,
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
            return False, "No usable Confluence instance found. Provide URL plus token or username/password."

        base_url = str(instance.get("url") or "").strip().rstrip("/")
        endpoint = f"{base_url}/rest/api/space?limit=1"
        headers, auth = self._build_auth(instance)
        ok, message, _data = await self._http_json_request(
            method="GET",
            url=endpoint,
            headers=headers,
            auth=auth,
            payload=None,
            timeout=15.0,
        )
        if not ok:
            return False, message
        name = str(instance.get("name") or base_url)
        return True, f"Confluence connection OK for {name}."

    async def _test_llm(self, config: dict) -> tuple[bool, str]:
        llm_cfg = config.get("llm") if isinstance(config.get("llm"), dict) else {}
        provider = str(llm_cfg.get("provider") or "").strip()
        model = str(llm_cfg.get("model") or "").strip()
        api_key = str(llm_cfg.get("api_key") or "").strip()

        if provider not in {"openai", "anthropic", "github_copilot"}:
            return False, f"Unsupported LLM provider: {provider or 'empty'}"
        if not model:
            return False, "LLM model is required."
        if not api_key:
            return False, "LLM API key is required."

        if provider == "openai":
            api_base = str(llm_cfg.get("api_base") or "https://api.openai.com/v1").strip().rstrip("/")
            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
            }
            if not model.startswith("gpt-5"):
                payload["temperature"] = 0
                payload["max_tokens"] = 1
            else:
                payload["max_completion_tokens"] = 1
            return await self._provider_request(provider, model, f"{api_base}/chat/completions", headers, payload)

        if provider == "anthropic":
            api_base = str(llm_cfg.get("api_base") or "https://api.anthropic.com").strip().rstrip("/")
            headers = {
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "temperature": 0,
            }
            return await self._provider_request(provider, model, f"{api_base}/messages", headers, payload)

        api_base = str(llm_cfg.get("api_base") or "https://api.githubcopilot.com").strip().rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2023-06-01",
            "Accept": "application/vnd.github.copilot-chat-preview+json",
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
    def _first_auth_instance(instances: list) -> dict | None:
        for item in instances:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            token = str(item.get("token") or "").strip()
            username = str(item.get("username") or "").strip()
            password = str(item.get("password") or "").strip()
            if url and (token or (username and password)):
                return item
        return None

    @staticmethod
    def _build_auth(instance: dict) -> tuple[dict, tuple[str, str] | None]:
        token = str(instance.get("token") or "").strip()
        if token:
            return {"Authorization": f"Bearer {token}"}, None
        username = str(instance.get("username") or "").strip()
        password = str(instance.get("password") or "").strip()
        return {}, (username, password)

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
        auth: tuple[str, str] | None = None,
    ) -> tuple[bool, str, dict | None]:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method=method, url=url, headers=headers, json=payload, auth=auth)
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
