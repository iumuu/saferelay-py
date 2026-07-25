"""HTTP 模块 — 封装 httpx.AsyncClient。"""

import httpx
from typing import Any, Dict, Optional


class HttpClient:
    """异步 HTTP 客户端，封装 httpx。"""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None

    async def _ensure(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def get(self, url: str, **kwargs) -> Dict[str, Any]:
        """执行 GET 请求。"""
        client = await self._ensure()
        resp = await client.get(url, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post(self, url: str, json: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]:
        """执行 POST 请求。"""
        client = await self._ensure()
        resp = await client.post(url, json=json, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def post_form(self, url: str, data: Dict[str, Any], **kwargs) -> Dict[str, Any]:
        """Send an application/x-www-form-urlencoded POST request."""
        client = await self._ensure()
        resp = await client.post(url, data=data, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client:
            await self._client.aclose()
            self._client = None
