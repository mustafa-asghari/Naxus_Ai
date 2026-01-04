import os, uuid, json
from datetime import datetime, date ,UTC
from typing import Any, Optional

from dotenv import load_dotenv
import psycopg
from psycopg.types.json import Jsonb
import clickhouse_connect   
from mcp.server.fastmcp import FastMCP
from pathlib import Path


load_dotenv(dotenv_path="/Users/mustafaasghari/code/Naxus/.env")



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
    c.command("""
        CREATE TABLE IF NOT EXISTS notes (
          id UUID,
          created_at DateTime64(3),
          source_event_id UUID,

          title String,
          content String,

          deadline Date,        -- 1970-01-01 if unknown
          plan String,          -- JSON string or empty
          status String,        -- empty if unknown
          priority UInt8,       -- 0 if unknown

          tags Array(String),
          confidence Float32
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
def ch_insert_note(
    content: str,
    title: str = "",
    deadline: Optional[str] = None,        # YYYY-MM-DD or null
    plan: Optional[dict[str, Any]] = None, # JSON or null
    status: str = "",
    priority: int = 0,
    tags: Optional[list[str]] = None,
    confidence: float = 0.8,
    source_event_id: Optional[str] = None,
) -> dict[str, Any]:
    tags = tags or []
    note_id = uuid.uuid4()
    dl = date.fromisoformat(deadline) if deadline else date(1970, 1, 1)
    src = uuid.UUID(source_event_id) if source_event_id else uuid.UUID(int=0)

    row = {
        "id": note_id,
        "created_at": datetime.now(UTC),
        "source_event_id": src,
        "title": title or "",
        "content": content,
        "deadline": dl,
        "plan": json.dumps(plan) if plan else "",
        "status": status or "",
        "priority": int(max(0, min(255, priority))),
        "tags": tags,
        "confidence": float(confidence),
    }

    c = ch_client()
    c.insert("notes", [row], column_names=list(row.keys()))
    return {"ok": True, "note_id": str(note_id)}

@mcp.tool()
def ch_search_notes_text(query: str, limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(100, int(limit)))

    # ClickHouse does NOT support ILIKE.
    # Do: lowerUTF8(field) LIKE lowerUTF8(pattern)
    pattern = f"%{query}%"

    c = ch_client()
    res = c.query(
        """
        SELECT id, created_at, title, content, deadline, tags, confidence
        FROM notes
        WHERE lowerUTF8(content) LIKE lowerUTF8({pattern:String})
           OR lowerUTF8(title)   LIKE lowerUTF8({pattern:String})
        ORDER BY created_at DESC
        LIMIT {limit:UInt32}
        """,
        parameters={"pattern": pattern, "limit": limit},
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
        })
    return {"count": len(items), "items": items}

@mcp.tool()
def ch_recent_notes(limit: int = 10) -> dict[str, Any]:
    limit = max(1, min(50, int(limit)))
    c = ch_client()
    res = c.query(
        """
        SELECT id, created_at, title, content
        FROM notes
        ORDER BY created_at DESC
        LIMIT {limit:UInt32}
        """,
        parameters={"limit": limit},
    )
    items = [{"id": str(r[0]), "created_at": str(r[1]), "title": r[2], "content": r[3]} for r in res.result_rows]
    return {"count": len(items), "items": items}
    
if __name__ == "__main__":
    # simplest local mode: stdio (Nexus spawns this server)
    mcp.run(transport="stdio")
                