# core/clickhouse_memory.py

import os
import uuid
from datetime import datetime, date
from typing import Any, Optional

import clickhouse_connect

CLICKHOUSE_HOST = os.getenv("CLICKHOUSE_HOST", "localhost")
CLICKHOUSE_PORT = int(os.getenv("CLICKHOUSE_PORT", "8123"))
CLICKHOUSE_DB = os.getenv("CLICKHOUSE_DB", "default")
CLICKHOUSE_USER = os.getenv("CLICKHOUSE_USER", "default")
CLICKHOUSE_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")

def get_client():
    return clickhouse_connect.get_client(
        host=CLICKHOUSE_HOST,
        port=CLICKHOUSE_PORT,
        username=CLICKHOUSE_USER,
        password=CLICKHOUSE_PASSWORD,
        database=CLICKHOUSE_DB,
    )

def init_notes_table():
    client = get_client()
    client.command("""
        CREATE TABLE IF NOT EXISTS notes (
          id UUID,
          created_at DateTime64(3),
          source_event_id UUID,
          title String,
          content String,
          deadline Date,
          plan String,
          status String,
          priority UInt8,
          tags Array(String),
          confidence Float32
        )
        ENGINE = MergeTree
        ORDER BY (created_at)
    """)
