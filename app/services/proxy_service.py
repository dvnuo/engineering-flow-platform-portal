from urllib.parse import urlencode

import httpx


class ProxyService:
    def build_robot_base_url(self, robot) -> str:
        return f"http://{robot.service_name}.{robot.namespace}.svc.cluster.local"

    async def forward(
        self,
        robot,
        method: str,
        subpath: str,
        query_params: dict[str, str],
        body: bytes | None,
        headers: dict[str, str],
    ) -> tuple[int, bytes, str]:
        base = self.build_robot_base_url(robot).rstrip("/")
        path = f"/{subpath}" if subpath else "/"
        query = urlencode(query_params)
        url = f"{base}{path}"
        if query:
            url = f"{url}?{query}"

        outbound_headers = {
            "content-type": headers.get("content-type", "application/json"),
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method=method, url=url, content=body, headers=outbound_headers)
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.status_code, resp.content, content_type
