"""
gRPC Server for Nexus MCP - High-performance alternative to stdio MCP

Run with: python3 mcp_grpc_server.py
Default port: 50051
"""
import json
import os
import sys
from concurrent import futures
from pathlib import Path

import grpc

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dotenv import load_dotenv

# Load env from project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

# Import generated protobuf code
import mcp_pb2
import mcp_pb2_grpc

# Import existing MCP server functions
from mcp_server import (
    pg_append_event,
    pg_get_recent_history,
    ch_insert_note,
    ch_search_notes_text,
    ch_delete_note,
    ch_list_notes,
    ch_search_history,
    ch_update_note,
    ch_get_note_by_id,
    pg_delete_session_events,
    init_postgres_schema,
    init_clickhouse_schema,
    wait_for_databases,
)


class NexusMCPServicer(mcp_pb2_grpc.NexusMCPServicer):
    """gRPC service implementation wrapping existing MCP tools."""
    
    def AppendEvent(self, request, context):
        """Append an event to PostgreSQL."""
        try:
            payload = json.loads(request.payload_json) if request.payload_json else {}
            result = pg_append_event(
                kind=request.kind,
                payload=payload,
                session_id=request.session_id or "default",
                tags=list(request.tags) if request.tags else None,
            )
            return mcp_pb2.AppendEventResponse(
                ok=result.get("ok", False),
                event_id=result.get("event_id", ""),
            )
        except Exception as e:
            return mcp_pb2.AppendEventResponse(ok=False, error=str(e))
    
    def GetRecentHistory(self, request, context):
        """Get recent chat history."""
        try:
            result = pg_get_recent_history(
                session_id=request.session_id or "default",
                limit=request.limit or 10,
            )
            return mcp_pb2.GetHistoryResponse(history=result.get("history", []))
        except Exception as e:
            context.set_details(str(e))
            return mcp_pb2.GetHistoryResponse()
    
    def InsertNote(self, request, context):
        """Insert a new note."""
        try:
            result = ch_insert_note(
                content=request.content,
                title=request.title or None,
                deadline=request.deadline or None,
                tags=list(request.tags) if request.tags else None,
                confidence=request.confidence or 0.8,
            )
            return mcp_pb2.NoteResponse(
                ok=result.get("ok", False),
                note_id=result.get("note_id", ""),
            )
        except Exception as e:
            return mcp_pb2.NoteResponse(ok=False, error=str(e))
    
    def SearchNotes(self, request, context):
        """Search notes semantically."""
        try:
            result = ch_search_notes_text(
                query=request.query,
                limit=request.limit or 10,
            )
            items = []
            for item in result.get("items", []):
                items.append(mcp_pb2.NoteItem(
                    id=item.get("id", ""),
                    title=item.get("title", ""),
                    content=item.get("content", ""),
                    deadline=str(item.get("deadline", "")),
                    tags=item.get("tags", []),
                    confidence=item.get("confidence", 0.0),
                    score=item.get("score", 0.0),
                ))
            return mcp_pb2.SearchNotesResponse(
                count=result.get("count", len(items)),
                items=items,
            )
        except Exception as e:
            context.set_details(str(e))
            return mcp_pb2.SearchNotesResponse()
    
    def DeleteNote(self, request, context):
        """Delete a note by ID."""
        try:
            result = ch_delete_note(note_id=request.note_id)
            return mcp_pb2.DeleteResponse(
                ok=result.get("ok", False),
                deleted_id=result.get("deleted_id", request.note_id),
            )
        except Exception as e:
            return mcp_pb2.DeleteResponse(ok=False, error=str(e))
    
    def ListNotes(self, request, context):
        """List all notes."""
        try:
            result = ch_list_notes(limit=request.limit or 20)
            notes = []
            for note in result.get("notes", []):
                notes.append(mcp_pb2.NoteItem(
                    id=note.get("id", ""),
                    title=note.get("title", ""),
                    content=note.get("content", ""),
                    deadline=str(note.get("deadline", "")),
                    tags=note.get("tags", []),
                ))
            return mcp_pb2.ListNotesResponse(
                ok=result.get("ok", False),
                count=result.get("count", len(notes)),
                notes=notes,
            )
        except Exception as e:
            return mcp_pb2.ListNotesResponse(ok=False)
    
    def SearchHistory(self, request, context):
        """Search chat history semantically."""
        try:
            result = ch_search_history(
                query=request.query,
                limit=request.limit or 5,
            )
            results = []
            for r in result.get("results", []):
                results.append(mcp_pb2.HistoryItem(
                    timestamp=str(r.get("timestamp", "")),
                    role=r.get("role", ""),
                    text=r.get("text", ""),
                    score=r.get("score", 0.0),
                ))
            return mcp_pb2.SearchHistoryResponse(results=results)
        except Exception as e:
            context.set_details(str(e))
            return mcp_pb2.SearchHistoryResponse()
    
    def CallTool(self, request, context):
        """Generic tool call for any MCP tool."""
        try:
            args = json.loads(request.args_json) if request.args_json else {}
            
            # Map tool names to functions
            tools = {
                "pg_append_event": pg_append_event,
                "pg_get_recent_history": pg_get_recent_history,
                "ch_insert_note": ch_insert_note,
                "ch_search_notes_text": ch_search_notes_text,
                "ch_delete_note": ch_delete_note,
                "ch_list_notes": ch_list_notes,
                "ch_search_history": ch_search_history,
                "ch_update_note": ch_update_note,
                "ch_get_note_by_id": ch_get_note_by_id,
                "pg_delete_session_events": pg_delete_session_events,
            }
            
            if request.tool_name not in tools:
                return mcp_pb2.ToolCallResponse(
                    ok=False,
                    error=f"Unknown tool: {request.tool_name}",
                )
            
            result = tools[request.tool_name](**args)
            return mcp_pb2.ToolCallResponse(
                ok=True,
                result_json=json.dumps(result),
            )
        except Exception as e:
            return mcp_pb2.ToolCallResponse(ok=False, error=str(e))


def serve():
    """Start the gRPC server."""
    port = os.getenv("NEXUS_GRPC_PORT", "50051")
    
    # Wait for databases
    if not wait_for_databases():
        print("gRPC: Failed to connect to databases")
        sys.exit(1)
    
    # Initialize schemas
    try:
        init_clickhouse_schema()
        init_postgres_schema()
    except Exception as e:
        print(f"gRPC: Schema init failed: {e}")
    
    # Create server with thread pool
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    mcp_pb2_grpc.add_NexusMCPServicer_to_server(NexusMCPServicer(), server)
    server.add_insecure_port(f"[::]:{port}")
    
    print(f"gRPC MCP Server starting on port {port}...")
    server.start()
    print(f"gRPC MCP Server ready on port {port}")
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
