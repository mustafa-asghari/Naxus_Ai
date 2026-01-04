import os
from typing import Any, Optional

from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters


class MCPMemoryClient:
    def __init__(self, server_cmd: list[str]):
        self.server_cmd = server_cmd
        self._session: Optional[ClientSession] = None
        self._ctx = None
        self._read = None
        self._write = None

    async def start(self) -> None:
        params = StdioServerParameters(
            command=self.server_cmd[0],
            args=self.server_cmd[1:],
            env=os.environ.copy(), 
            
 # ✅ pass env so PG_DSN/CH_* exist in server
        )
        print("MCP spawning:", params.command, params.args)
        print("MCP env has PG_DSN:", "PG_DSN" in (params.env or {}))

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
            raise RuntimeError("MCPMemoryClient not started")
        res = await self._session.call_tool(tool_name, args)
        return dict(res.structuredContent or {})

    async def call_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        return await self.call(tool_name, args)

    async def init_schemas(self) -> None:
        await self.call("init_postgres_schema", {})
        await self.call("init_clickhouse_schema", {})
