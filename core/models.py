from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.intent import Intent


@dataclass(frozen=True)
class ActionStep:
    intent: Intent
    args: Dict[str, Any]


@dataclass(frozen=True)
class Command:
    raw: str        
    plan: Optional[str]
    steps: List[ActionStep]


@dataclass(frozen=True)
class Result:
    ok: bool
    message: str
    data: Optional[Dict[str, Any]] = None
