import os, uuid, json, sys, time
from datetime import datetime, date, UTC    
from typing import Any, Optional
from openai import OpenAI

import psycopg
from psycopg.types.json import Jsonb
import clickhouse_connect   
from mcp.server.fastmcp import FastMCP
from pathlib import Path
from dotenv import load_dotenv

# Load env from the project root
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

_CH_SCHEMA_READY = False

def _ensure_ch_schema() -> None:
    global _CH_SCHEMA_READY
    if _CH_SCHEMA_READY:
        return
    init_clickhouse_schema()
    _CH_SCHEMA_READY = True

# ------------------------------------------------------------------
# SCHEMAS (NOW WITH HNSW CHAT HISTORY)
# ------------------------------------------------------------------

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
    
    # Enable vector search
    c.command("SET allow_experimental_vector_similarity_index = 1") 

    # 1. NOTES TABLE (Existing)
    c.command("""
        CREATE TABLE IF NOT EXISTS notes_v2 (
          id UUID,
          created_at DateTime64(3),
          source_event_id UUID,
          title String,
          content String,
          embedding Array(Float32),
          deadline Date,
          plan String,
          status String,
          priority UInt8,
          tags Array(String),
          confidence Float32,
          INDEX idx_embedding embedding TYPE vector_similarity('hnsw', 'cosineDistance', 1536) GRANULARITY 1
        )
        ENGINE = MergeTree
        ORDER BY (created_at)
    """)

    # 2. NEW: CHAT HISTORY VECTOR TABLE
    # This stores every chat message with an HNSW index for instant retrieval
    c.command("""
        CREATE TABLE IF NOT EXISTS chat_history_vec (
          id UUID,
          created_at DateTime64(3),
          session_id String,
          role String,  -- 'user' or 'assistant'
          content String,
          embedding Array(Float32),
          
          -- HNSW INDEX for Lightning Fast Search
          INDEX idx_chat_embed embedding TYPE vector_similarity('hnsw', 'cosineDistance', 1536) GRANULARITY 1
        )
        ENGINE = MergeTree
        ORDER BY (created_at)
    """)
    
    return {"ok": True}

# ------------------------------------------------------------------
# CORE TOOLS
# ------------------------------------------------------------------

@mcp.tool()
def pg_append_event(
    kind: str,
    payload: dict[str, Any],
    session_id: str = "default",
    tags: Optional[list[str]] = None,
    ts_iso: Optional[str] = None,
) -> dict[str, Any]:
    """
    Saves the event to Postgres AND automatically vectorizes it into ClickHouse.
    """
    _ensure_ch_schema()
    tags = tags or []
    ts = datetime.fromisoformat(ts_iso) if ts_iso else datetime.now(UTC)
    
    # 1. Save to Postgres (The reliable log)
    sql = """
      INSERT INTO events (ts, session_id, kind, payload, tags)
      VALUES (%s, %s, %s, %s::jsonb, %s)
      RETURNING id;
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (ts, session_id, kind, Jsonb(payload), tags))
        event_id = cur.fetchone()[0]

    # 2. AUTO-VECTORIZE (The "Smart" Memory)
    # Only vectorize if it's actual text conversation
    text_content = payload.get("text")
    if text_content and kind in ["user_msg", "assistant_reply"]:
        try:
            # Generate Embedding
            emb_resp = client.embeddings.create(
                input=text_content,
                model="text-embedding-3-small"
            )
            vector = emb_resp.data[0].embedding
            
            # Map kind to role
            role = "user" if kind == "user_msg" else "assistant"

            # Insert into ClickHouse Vector Table
            c = ch_client()
            c.insert("chat_history_vec", [[
                str(event_id),
                ts,
                session_id,
                role,
                text_content,
                vector
            ]], column_names=["id", "created_at", "session_id", "role", "content", "embedding"])
            
            # Print to stderr for debugging (won't break MCP)
            sys.stderr.write(f"MCP: Vectorized chat event {event_id}\n")
            
        except Exception as e:
            sys.stderr.write(f"MCP: Vectorization failed (non-critical): {e}\n")

    return {"ok": True, "event_id": str(event_id)}

@mcp.tool()
def ch_search_history(query: str, limit: int = 5) -> dict[str, Any]:
    """
    Semantic Search for CHAT HISTORY.
    Finds past conversations about a topic, even if they were weeks ago.
    """
    _ensure_ch_schema()
    limit = max(1, min(20, int(limit)))

    # 1. Vectorize the search query
    response = client.embeddings.create(
        input=query,
        model="text-embedding-3-small"
    )
    query_vector = response.data[0].embedding

    # 2. HNSW Search in ClickHouse
    c = ch_client()
    res = c.query(
        """
        SELECT created_at, role, content,
               cosineDistance(embedding, {query_vector:Array(Float32)}) as score
        FROM chat_history_vec
        ORDER BY score ASC
        LIMIT {limit:UInt32}
        """,
        parameters={"query_vector": query_vector, "limit": limit},
    )

    items = []
    for r in res.result_rows:
        items.append({
            "timestamp": str(r[0]),
            "role": r[1],
            "text": r[2],
            "score": float(r[3]),
        })
        
    return {"results": items}

# ------------------------------------------------------------------
# (EXISTING TOOLS - KEPT AS IS)
# ------------------------------------------------------------------

@mcp.tool()
def pg_get_recent_history(session_id: str = "default", limit: int = 10) -> dict[str, Any]:
    # Standard "Short Term Memory" (Last N messages)
    sql = """
      SELECT kind, payload 
      FROM events 
      WHERE session_id = %s AND kind IN ('user_msg', 'assistant_reply')
      ORDER BY ts DESC 
      LIMIT %s;
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (session_id, limit))
        rows = cur.fetchall()
    
    history = []
    for kind, payload in reversed(rows):
        text = payload.get("text", "")
        if kind == "user_msg":
            history.append(f"User: {text}")
        else:
            history.append(f"Nexus: {text}")
            
    return {"history": history}

@mcp.tool()
def ch_insert_note(
    content: str,
    title: Optional[str] = None,
    deadline: Optional[str] = None,
    plan: Optional[dict[str, Any]] = None,
    status: Optional[str] = None,
    priority: Optional[int] = None,
    tags: Optional[list[str]] = None,
    confidence: Optional[float] = None,
    source_event_id: Optional[str] = None,
) -> dict[str, Any]:
    _ensure_ch_schema()
    # (Existing note logic...)
    title = title or ""
    status = status or ""
    priority = priority if priority is not None else 0
    tags = tags or []
    confidence = confidence if confidence is not None else 0.8
    note_id = uuid.uuid4()
    dl = date.fromisoformat(deadline) if deadline and deadline.strip() else date(1970, 1, 1)
    src = uuid.UUID(source_event_id) if source_event_id else uuid.UUID(int=0)

    response = client.embeddings.create(input=content, model="text-embedding-3-small")
    embedding_vector = response.data[0].embedding

    row_data = [
        str(note_id), datetime.now(UTC), str(src), title, content, embedding_vector,
        dl, json.dumps(plan) if plan else "", status, int(max(0, min(255, priority))),
        tags, float(confidence),
    ]
    c = ch_client()
    c.insert("notes_v2", [row_data], column_names=[
        "id", "created_at", "source_event_id", "title", "content",
        "embedding", "deadline", "plan", "status", "priority", "tags", "confidence"
    ])
    return {"ok": True, "note_id": str(note_id)}

@mcp.tool()
def ch_search_notes_text(query: str, limit: int = 10) -> dict[str, Any]:
    _ensure_ch_schema()
    response = client.embeddings.create(input=query, model="text-embedding-3-small")
    query_vector = response.data[0].embedding
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
            "id": str(r[0]), "created_at": str(r[1]), "title": r[2], "content": r[3],
            "deadline": str(r[4]), "tags": r[5], "confidence": float(r[6]), "score": float(r[7]),
        })
    return {"count": len(items), "items": items}

@mcp.tool()
def pg_upsert_setting(key: str, value: dict[str, Any]) -> dict[str, Any]:
    sql = """
      INSERT INTO settings (key, value, updated_at) VALUES (%s, %s::jsonb, now())
      ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now();
    """
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute(sql, (key, Jsonb(value)))
    return {"ok": True}

@mcp.tool()
def ch_clear_notes() -> dict[str, Any]:
    c = ch_client()
    c.command("TRUNCATE TABLE notes_v2")
    return {"ok": True}

def wait_for_databases():
    sys.stderr.write("MCP: Waiting for databases to spin up...\n")
    retries = 30
    for i in range(retries):
        try:
            with pg_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
            c = ch_client()
            c.command("SELECT 1")
            sys.stderr.write("MCP: Databases are UP and READY! Starting server.\n")
            return True
        except Exception as e:
            sys.stderr.write(f"MCP: Database not ready yet ({i+1}/{retries})...\n")
            time.sleep(2)
    sys.stderr.write("MCP: Critical Error - Databases never came online.\n")
    return False

if __name__ == "__main__":
    if wait_for_databases():
        try:
            init_clickhouse_schema()
            init_postgres_schema() # Don't forget to init Postgres too!
        except Exception as e:
            sys.stderr.write(f"MCP: Schema init failed, but continuing: {e}\n")
        mcp.run(transport="stdio")  
    else:
        sys.exit(1)