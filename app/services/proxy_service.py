from collections.abc import Iterable

import httpx
from typing import Optional


class ProxyService:
    def build_robot_base_url(self, robot) -> str:
        return f"http://{robot.service_name}.{robot.namespace}.svc.cluster.local"

    async def forward(
        self,
        robot,
        method: str,
        subpath: str,
        query_items: Iterable[tuple[str, str]],
        body: Optional[bytes],
        headers: dict[str, str],
    ) -> tuple[int, bytes, str]:
        base = self.build_robot_base_url(robot).rstrip("/")
        path = f"/{subpath}" if subpath else "/"
        url = f"{base}{path}"

        outbound_headers = {}
        if headers.get("content-type"):
            outbound_headers["content-type"] = headers["content-type"]

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=method,
                url=url,
                params=list(query_items),
                content=body,
                headers=outbound_headers,
            )
        content_type = resp.headers.get("content-type", "application/octet-stream")
        return resp.status_code, resp.content, content_type
