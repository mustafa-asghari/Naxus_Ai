from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Optional

from core.models import ActionStep

@dataclass
class MemoryRead:
    query: str
    limit: int = 5

@dataclass
class MemoryWrite:
    should_store: bool
    confidence: float = 0.0
    note: Optional[dict[str, Any]] = None

@dataclass
class TurnPlan:
    memory_read: Optional[MemoryRead]
    memory_write: Optional[MemoryWrite]
    actions: list[ActionStep]
