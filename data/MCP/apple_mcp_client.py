import os
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


class AppleMCPClient:
    """
    MCP client for the Node/Bun-based apple-mcp server in `apple_mcp/apple-mcp/`.
    """

    def __init__(self, server_cmd: list[str], cwd: Optional[str] = None):
        self.server_cmd = server_cmd
        self.cwd = cwd
        self._session: Optional[ClientSession] = None
        self._ctx = None
        self._read = None
        self._write = None

    async def start(self) -> None:
        params = StdioServerParameters(
            command=self.server_cmd[0],
            args=self.server_cmd[1:],
            env=os.environ.copy(),
            cwd=self.cwd,
        )
        self._ctx = stdio_client(params)
        self._read, self._write = await self._ctx.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def stop(self) -> None:
        if self._session:
            await self._session.__aexit__(None, None, None)
            self._session = None
        if self._ctx:
            await self._ctx.__aexit__(None, None, None)
            self._ctx = None

    async def call(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not self._session:
            raise RuntimeError("AppleMCPClient not started")
        res = await self._session.call_tool(tool_name, args)
        out: dict[str, Any] = dict(res.structuredContent or {})

        # apple-mcp typically returns `content: [{type:"text", text:"..."}]` (not structuredContent)
        try:
            content = getattr(res, "content", None)
            if content and isinstance(content, list) and "text" not in out:
                texts: list[str] = []
                for item in content:
                    try:
                        t = getattr(item, "text", None) if not isinstance(item, dict) else item.get("text")
                        if t:
                            texts.append(str(t))
                    except Exception:
                        continue
                if texts:
                    out["text"] = "\n".join(texts).strip()
        except Exception:
            pass

        # best-effort isError passthrough
        try:
            is_error = getattr(res, "isError", None)
            if is_error is not None and "isError" not in out:
                out["isError"] = bool(is_error)
        except Exception:
            pass

        return out


