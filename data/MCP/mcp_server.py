import os, uuid, json
from datetime import datetime, date ,UTC
from typing import Any, Optional
from openai import OpenAI

import psycopg
from psycopg.types.json import Jsonb
import clickhouse_connect   
from mcp.server.fastmcp import FastMCP
from pathlib import Path
from dotenv import load_dotenv


# Load env from the project root (fixes incorrect hardcoded path)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")

client = OpenAI()   

mcp = FastMCP("nexus-memory", json_response=True)

PG_DSN = os.getenv("PG_DSN")
if not PG_DSN:
    raise RuntimeError("PG_DSN missing in .env")

CH_HOST = os.getenv("CH_HOST", "localhost")
CH_PORT = int(os.getenv("CH_PORT", "8123"))
CH_DB = os.getenv("CH_DB", "nexus")
CH_USER = os.getenv("CH_USER", "default")
CH_PASSWORD = os.getenv("CH_PASSWORD", "")

def pg_conn():
    return psycopg.connect(PG_DSN, autocommit=True)

def ch_client():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT,
        username=CH_USER, password=CH_PASSWORD,
        database=CH_DB,
    )

# Simple guard so we don't attempt inserts/queries before the table exists.
_CH_SCHEMA_READY = False


def _ensure_ch_schema() -> None:
    """
    Make sure the ClickHouse database/table exist before reads or writes.
    Safe to call multiple times; runs DDL only once per process.
    """
    global _CH_SCHEMA_READY
    if _CH_SCHEMA_READY:
        return
    init_clickhouse_schema()
    _CH_SCHEMA_READY = True

@mcp.tool()
def init_postgres_schema() -> dict[str, Any]:
    ddl = """
    CREATE EXTENSION IF NOT EXISTS pgcrypto;

    CREATE TABLE IF NOT EXISTS events (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      ts TIMESTAMPTZ NOT NULL DEFAULT now(),
      session_id TEXT NOT NULL DEFAULT 'default',
      kind TEXT NOT NULL,
      payload JSONB NOT NULL,
      tags TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[]
    );

    CREATE INDEX IF NOT EXISTS idx_events_ts ON events (ts DESC);
    CREATE INDEX IF NOT EXISTS idx_events_kind ON events (kind);
    CREATE INDEX IF NOT EXISTS idx_events_payload_gin ON events USING GIN (payload);

    CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY,
      value JSONB NOT NULL,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(ddl)
    return {"ok": True}

@mcp.tool()
def init_clickhouse_schema() -> dict[str, Any]:
    c = ch_client()
    c.command(f"CREATE DATABASE IF NOT EXISTS {CH_DB}")
    
    # Enable vector search features
    c.command("SET allow_experimental_vector_similarity_index = 1") 

    c.command("""
        CREATE TABLE IF NOT EXISTS notes_v2 (
          id UUID,
          created_at DateTime64(3),
          source_event_id UUID,

          title String,
          content String,

          -- The Vector Column
          embedding Array(Float32),

          deadline Date,
          plan String,
          status String,
          priority UInt8,

          tags Array(String),
          confidence Float32,

          -- UPDATED INDEX TYPE HERE:
             INDEX idx_embedding embedding TYPE vector_similarity('hnsw', 'cosineDistance', 1536) GRANULARITY 1
        )
        ENGINE = MergeTree
        ORDER BY (created_at)
    """)
    return {"ok": True}

@mcp.tool()
def pg_append_event(
    kind: str,
    payload: dict[str, Any],
    session_id: str = "default",
    tags: Optional[list[str]] = None,
    ts_iso: Optional[str] = None,
) -> dict[str, Any]:
    tags = tags or []
    ts = datetime.fromisoformat(ts_iso) if ts_iso else datetime.now(UTC)
    sql = """
      INSERT INTO events (ts, session_id, kind, payload, tags)
      VALUES (%s, %s, %s, %s::jsonb, %s)
      RETURNING id;
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (ts, session_id, kind, Jsonb(payload), tags))
        event_id = cur.fetchone()[0]
    return {"ok": True, "event_id": str(event_id)}

@mcp.tool()
def pg_upsert_setting(key: str, value: dict[str, Any]) -> dict[str, Any]:
    sql = """
      INSERT INTO settings (key, value, updated_at)
      VALUES (%s, %s::jsonb, now())
      ON CONFLICT (key)
      DO UPDATE SET value = EXCLUDED.value, updated_at = now();
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (key, Jsonb(value)))
    return {"ok": True}
@mcp.tool()
def ch_clear_notes() -> dict[str, Any]:
    """
    Clears all notes from the notes_v2 table.
    Use this to start fresh or remove corrupted vector data.
    """
    c = ch_client()
    # TRUNCATE is a fast way to delete all rows in ClickHouse
    c.command("TRUNCATE TABLE notes_v2")
    return {"ok": True, "message": "All notes have been cleared from memory."}

@mcp.tool()
def ch_insert_note(
    content: str,
    title: Optional[str] = None,
    deadline: Optional[str] = None,        # YYYY-MM-DD or null
    plan: Optional[dict[str, Any]] = None, # JSON or null
    status: Optional[str] = None,
    priority: Optional[int] = None,
    tags: Optional[list[str]] = None,
    confidence: Optional[float] = None,
    source_event_id: Optional[str] = None,
) -> dict[str, Any]:
    _ensure_ch_schema()

    # Coerce None to safe defaults
    title = title or ""
    status = status or ""
    priority = priority if priority is not None else 0
    tags = tags or []
    confidence = confidence if confidence is not None else 0.8

    note_id = uuid.uuid4()
    dl = date.fromisoformat(deadline) if deadline and deadline.strip() else date(1970, 1, 1)
    src = uuid.UUID(source_event_id) if source_event_id else uuid.UUID(int=0)

    response = client.embeddings.create(
        input=content,
        model="text-embedding-3-small"
    )
    embedding_vector = response.data[0].embedding

    row_data = [
        str(note_id),                                  # id -> UUID
        datetime.now(UTC),                             # created_at -> DateTime64
        str(src),                                      # source_event_id -> UUID
        title,                                         # title -> String
        content,                                       # content -> String
        embedding_vector,                              # embedding -> Array(Float32)
        dl,                                            # deadline -> Date
        json.dumps(plan) if plan else "",              # plan -> String
        status,                                        # status -> String
        int(max(0, min(255, priority))),               # priority -> UInt8
        tags,                                          # tags -> Array(String)
        float(confidence),                             # confidence -> Float32
    ]

    column_names = [
        "id", "created_at", "source_event_id", "title", "content",
        "embedding", "deadline", "plan", "status", "priority", "tags", "confidence"
    ]

    c = ch_client()
    c.insert("notes_v2", [row_data], column_names=column_names)
    return {"ok": True, "note_id": str(note_id)}

@mcp.tool()
def ch_search_notes_text(query: str, limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(100, int(limit)))

    _ensure_ch_schema()

    # 1. Convert the user's search query into a vector (numbers)
    response = client.embeddings.create(
        input=query,
        model="text-embedding-3-small"
    )
    query_vector = response.data[0].embedding

    # 2. Ask the database for the notes "closest" to this vector
    # cosineDistance calculates how similar the meanings are (lower is better)
    c = ch_client()
    res = c.query(
        """
        SELECT id, created_at, title, content, deadline, tags, confidence,
               cosineDistance(embedding, {query_vector:Array(Float32)}) as score
        FROM notes_v2
        ORDER BY score ASC
        LIMIT {limit:UInt32}
        """,
        parameters={"query_vector": query_vector, "limit": limit},
    )

    items = []
    for r in res.result_rows:
        items.append({
            "id": str(r[0]),
            "created_at": str(r[1]),
            "title": r[2],
            "content": r[3],
            "deadline": str(r[4]),
            "tags": r[5],
            "confidence": float(r[6]),
            "score": float(r[7]),
        })
    return {"count": len(items), "items": items}

@mcp.tool()
def ch_recent_notes(limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(50, int(limit)))
    c = ch_client()
    res = c.query(
        """
        SELECT id, created_at, title, content
        FROM notes_v2
        ORDER BY created_at DESC
        LIMIT {limit:UInt32}
        """,
        parameters={"limit": limit},
    )
    items = [{"id": str(r[0]), "created_at": str(r[1]), "title": r[2], "content": r[3]} for r in res.result_rows]
    return {"count": len(items), "items": items}
    
if __name__ == "__main__":
    # simplest local mode: stdio (Nexus spawns this server)
    init_clickhouse_schema()
    mcp.run(transport="stdio")
                