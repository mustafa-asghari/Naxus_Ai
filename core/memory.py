from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import psycopg  # psycopg v3

from core.models import ActionStep, Command, Result


@dataclass(frozen=True)
class MemoryEvent:
    ts: float
    raw: str
    mode: str
    plan: Optional[str]
    steps_json: str
    results_json: str


class MemoryStore:
    """
    Postgres-backed memory log.

    Uses JSONB columns for steps/results because they are naturally structured data.
    """

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._init_db()

    def _connect(self) -> psycopg.Connection:
        # autocommit=False by default; we'll commit explicitly
        return psycopg.connect(self.database_url)

    def _init_db(self) -> None:
        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id BIGSERIAL PRIMARY KEY,
                        ts DOUBLE PRECISION NOT NULL,
                        raw TEXT NOT NULL,
                        mode TEXT NOT NULL,
                        plan TEXT,
                        steps JSONB NOT NULL,
                        results JSONB NOT NULL
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_events_ts
                    ON events (ts DESC);
                    """
                )
            con.commit()

    def log(self, cmd: Command, steps: List[ActionStep], results: List[Result]) -> None:
        ts = time.time()

        steps_payload: List[Dict[str, Any]] = [
            {"intent": s.intent.value, "args": s.args} for s in steps
        ]

        results_payload: List[Dict[str, Any]] = [
            {"ok": r.ok, "message": r.message, "data": r.data} for r in results
        ]

        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO events (ts, raw, mode, plan, steps, results)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb)
                    """,
                    (
                        ts,
                        cmd.raw,
                        cmd.mode.value,
                        cmd.plan,
                        json.dumps(steps_payload, ensure_ascii=False),
                        json.dumps(results_payload, ensure_ascii=False),
                    ),
                )
            con.commit()

    def recent(self, seconds: int = 1200) -> List[MemoryEvent]:
        cutoff = time.time() - seconds

        with self._connect() as con:
            with con.cursor() as cur:
                cur.execute(
                    """
                    SELECT ts, raw, mode, plan, steps::text, results::text
                    FROM events
                    WHERE ts >= %s
                    ORDER BY ts DESC
                    LIMIT 50
                    """,
                    (cutoff,),
                )
                rows = cur.fetchall()

        return [
            MemoryEvent(
                ts=r[0],
                raw=r[1],
                mode=r[2],
                plan=r[3],
                steps_json=r[4],
                results_json=r[5],
            )
            for r in rows
        ]
