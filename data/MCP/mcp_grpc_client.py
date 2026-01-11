"""
gRPC Client for Nexus MCP - High-performance alternative to stdio MCP

Usage:
    from data.MCP.mcp_grpc_client import MCPGrpcClient
    client = MCPGrpcClient()
    result = await client.call("ch_insert_note", {"content": "Test"})
"""
import json
import os
from typing import Any, Dict

import grpc

# Import generated protobuf code
from data.MCP import mcp_pb2, mcp_pb2_grpc


class MCPGrpcClient:
    """High-performance gRPC client for MCP calls."""
    
    def __init__(self, host: str = None, port: str = None):
        self.host = host or os.getenv("NEXUS_GRPC_HOST", "localhost")
        self.port = port or os.getenv("NEXUS_GRPC_PORT", "50051")
        self._channel = None
        self._stub = None
    
    def _get_stub(self):
        """Lazy connection - connect on first use."""
        if self._stub is None:
            self._channel = grpc.insecure_channel(f"{self.host}:{self.port}")
            self._stub = mcp_pb2_grpc.NexusMCPStub(self._channel)
        return self._stub
    
    async def call(self, tool_name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call an MCP tool via gRPC.
        
        Args:
            tool_name: Name of the tool (e.g., "ch_insert_note")
            args: Tool arguments as dict
        
        Returns:
            Tool result as dict
        """
        stub = self._get_stub()
        
        # Use specific methods for common tools for better performance
        try:
            if tool_name == "pg_append_event":
                response = stub.AppendEvent(mcp_pb2.AppendEventRequest(
                    kind=args.get("kind", ""),
                    payload_json=json.dumps(args.get("payload", {})),
                    session_id=args.get("session_id", "default"),
                    tags=args.get("tags", []),
                ))
                return {"ok": response.ok, "event_id": response.event_id, "error": response.error}
            
            elif tool_name == "ch_insert_note":
                response = stub.InsertNote(mcp_pb2.InsertNoteRequest(
                    content=args.get("content", ""),
                    title=args.get("title", ""),
                    deadline=args.get("deadline", ""),
                    tags=args.get("tags", []),
                    confidence=args.get("confidence", 0.8),
                ))
                return {"ok": response.ok, "note_id": response.note_id, "error": response.error}
            
            elif tool_name == "ch_search_notes_text":
                response = stub.SearchNotes(mcp_pb2.SearchRequest(
                    query=args.get("query", ""),
                    limit=args.get("limit", 10),
                ))
                items = [{
                    "id": item.id,
                    "title": item.title,
                    "content": item.content,
                    "score": item.score,
                } for item in response.items]
                return {"count": response.count, "items": items}
            
            elif tool_name == "ch_delete_note":
                response = stub.DeleteNote(mcp_pb2.DeleteNoteRequest(
                    note_id=args.get("note_id", ""),
                ))
                return {"ok": response.ok, "deleted_id": response.deleted_id}
            
            elif tool_name == "ch_list_notes":
                response = stub.ListNotes(mcp_pb2.ListNotesRequest(
                    limit=args.get("limit", 20),
                ))
                notes = [{
                    "id": note.id,
                    "title": note.title,
                    "content": note.content,
                } for note in response.notes]
                return {"ok": response.ok, "count": response.count, "notes": notes}
            
            elif tool_name == "ch_search_history":
                response = stub.SearchHistory(mcp_pb2.SearchRequest(
                    query=args.get("query", ""),
                    limit=args.get("limit", 5),
                ))
                results = [{
                    "timestamp": r.timestamp,
                    "role": r.role,
                    "text": r.text,
                    "score": r.score,
                } for r in response.results]
                return {"results": results}
            
            elif tool_name == "pg_get_recent_history":
                response = stub.GetRecentHistory(mcp_pb2.GetHistoryRequest(
                    session_id=args.get("session_id", "default"),
                    limit=args.get("limit", 10),
                ))
                return {"history": list(response.history)}
            
            else:
                # Fallback to generic CallTool for any other tool
                response = stub.CallTool(mcp_pb2.ToolCallRequest(
                    tool_name=tool_name,
                    args_json=json.dumps(args),
                ))
                if response.ok:
                    return json.loads(response.result_json) if response.result_json else {}
                else:
                    return {"ok": False, "error": response.error}
        
        except grpc.RpcError as e:
            return {"ok": False, "error": f"gRPC error: {e.details()}"}
    
    async def start(self) -> None:
        """Start the gRPC connection (connects lazily on first call)."""
        # gRPC connects lazily, but we can pre-connect here
        self._get_stub()
        print(f"[gRPC] Connected to {self.host}:{self.port}")
    
    async def stop(self) -> None:
        """Close the gRPC channel."""
        self.close()
    
    async def init_schemas(self) -> None:
        """Initialize database schemas via gRPC."""
        await self.call("init_postgres_schema", {})
        await self.call("init_clickhouse_schema", {})
    
    def close(self):
        """Close the gRPC channel."""
        if self._channel:
            self._channel.close()
            self._channel = None
            self._stub = None
