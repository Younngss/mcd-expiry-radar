"""Small read-only MCP Streamable HTTP client; Python standard library only."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://mcp.mcd.cn"
READ_ONLY_TOOLS = frozenset({
    "now-time-info", "query-my-coupons", "query-my-account",
    "campaign-calendar", "available-coupons", "mall-points-products",
})


class MCPError(RuntimeError):
    pass


def read_token(root: Path) -> str:
    token = os.environ.get("MCD_MCP_TOKEN", "").strip()
    env_file = root / ".env"
    if not token and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "MCD_MCP_TOKEN":
                token = value.strip().strip("\"'")
                break
    if not token:
        raise MCPError("请在本地 .env 或环境变量中设置 MCD_MCP_TOKEN。")
    return token


def decode_response(text: str, request_id: int) -> dict:
    """Accept JSON or an SSE stream, including multiline data events."""
    try:
        messages = [json.loads(text)]
    except json.JSONDecodeError:
        messages = []
        for block in text.replace("\r\n", "\n").split("\n\n"):
            data = "\n".join(line[5:].lstrip(" ") for line in block.splitlines()
                             if line.startswith("data:"))
            if data and data != "[DONE]":
                try:
                    messages.append(json.loads(data))
                except json.JSONDecodeError:
                    continue
    for message in messages:
        if isinstance(message, dict) and message.get("id") == request_id:
            if "error" in message:
                raise MCPError("MCP 返回协议错误，请核对服务状态和参数。")
            if isinstance(message.get("result"), dict):
                return message["result"]
    raise MCPError("MCP 响应缺少匹配的结果。")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise MCPError("官方接口返回重定向，已停止，避免转发凭据。")


class MCPClient:
    def __init__(self, token: str, timeout: int = 30):
        self._token = token
        self.timeout = timeout
        self.session = None
        self.version = "2024-11-05"
        self._id = 0
        self.tools: dict[str, dict] = {}
        self.opener = urllib.request.build_opener(NoRedirect())

    def _request(self, method: str, params=None, notification=False):
        self._id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._id
        if params is not None:
            payload["params"] = params
        headers = {
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.version,
        }
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        for attempt in range(2):
            req = urllib.request.Request(ENDPOINT, json.dumps(payload).encode(), headers)
            try:
                with self.opener.open(req, timeout=self.timeout) as response:
                    self.session = response.headers.get("Mcp-Session-Id", self.session)
                    # Tool calls are expected to return finite SSE responses.
                    raw = response.read(12_000_001)
                    if len(raw) > 12_000_000:
                        raise MCPError("MCP 响应过大，已停止读取。")
                    if notification:
                        return None
                    return decode_response(raw.decode("utf-8"), self._id)
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt == 0:
                    try:
                        delay = min(30, max(1, int(exc.headers.get("Retry-After", "2"))))
                    except ValueError:
                        delay = 2
                    time.sleep(delay)
                    continue
                if exc.code in (401, 403):
                    raise MCPError("认证失败，请在官方控制台检查或重新申请 Token。") from None
                raise MCPError(f"官方 MCP 暂不可用（HTTP {exc.code}）。") from None
            except (urllib.error.URLError, TimeoutError, OSError):
                raise MCPError("连接官方 MCP 失败，请检查网络后重试。") from None
        raise MCPError("请求未完成。")

    def connect(self):
        result = self._request("initialize", {
            "protocolVersion": self.version, "capabilities": {},
            "clientInfo": {"name": "mcd-expiry-radar", "version": "1.0.0"},
        })
        self.version = result.get("protocolVersion", self.version)
        self._request("notifications/initialized", notification=True)
        cursor = None
        for _ in range(20):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            for tool in result.get("tools", []):
                self.tools[tool["name"]] = tool
            cursor = result.get("nextCursor")
            if not cursor:
                return self
        raise MCPError("工具列表分页超限。")

    def call(self, name: str, arguments: dict | None = None) -> dict:
        if name not in READ_ONLY_TOOLS:
            raise MCPError("此客户端只允许权益雷达的六个只读工具。")
        if name not in self.tools:
            raise MCPError(f"服务未提供 {name}，无法读取该项数据。")
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise MCPError(f"{name} 执行失败。")
        data = result.get("structuredContent")
        if not isinstance(data, dict):
            data = None
            for item in result.get("content", []):
                if item.get("type") == "text":
                    try:
                        candidate = json.loads(item.get("text", ""))
                    except json.JSONDecodeError:
                        continue
                    if isinstance(candidate, dict):
                        data = candidate
                        break
        if not isinstance(data, dict):
            raise MCPError(f"{name} 返回了无法识别的数据结构。")
        if "code" in data and str(data["code"]) != "200":
            raise MCPError(f"{name} 业务查询失败，请在官方渠道核验。")
        return data
