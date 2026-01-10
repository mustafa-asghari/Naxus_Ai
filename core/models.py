from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TypeVar, Generic, Union, Callable

from core.intent import Intent


# ═══════════════════════════════════════════════════════════════════════════════
# GENERIC RESULT PATTERN - For Composable Error Handling
# ═══════════════════════════════════════════════════════════════════════════════

T = TypeVar('T')
U = TypeVar('U')


@dataclass(frozen=True)
class Success(Generic[T]):
    """Represents a successful operation containing a value."""
    value: T
    
    @property
    def is_success(self) -> bool:
        return True
    
    @property
    def is_failure(self) -> bool:
        return False
    
    def unwrap(self) -> T:
        """Extract the value. Safe to call on Success."""
        return self.value
    
    def unwrap_or(self, default: T) -> T:
        return self.value
    
    def map(self, fn: Callable[[T], U]) -> "Outcome[U]":
        """Transform the value if successful."""
        try:
            return Success(fn(self.value))
        except Exception as e:
            return Failure(str(e))


@dataclass(frozen=True)
class Failure(Generic[T]):
    """Represents a failed operation containing an error message."""
    error: str
    
    @property
    def is_success(self) -> bool:
        return False
    
    @property
    def is_failure(self) -> bool:
        return True
    
    def unwrap(self) -> T:
        raise ValueError(f"Cannot unwrap Failure: {self.error}")
    
    def unwrap_or(self, default: T) -> T:
        return default
    
    def map(self, fn: Callable[[T], U]) -> "Outcome[U]":
        return Failure(self.error)


# Type alias for Result - either Success or Failure
Outcome = Union[Success[T], Failure[T]]


def ok(value: T) -> Outcome[T]:
    """Create a successful outcome."""
    return Success(value)


def fail(error: str) -> Outcome[T]:
    """Create a failed outcome."""
    return Failure(error)


# ═══════════════════════════════════════════════════════════════════════════════
# DOMAIN ENTITIES
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class ActionStep:
    """A single executable action with its intent and arguments."""
    intent: Intent
    args: Dict[str, Any] = field(default_factory=dict)
    
    def get_arg(self, key: str, default: Any = None) -> Any:
        """Safely get an argument value."""
        return (self.args or {}).get(key, default)


@dataclass(frozen=True)
class Command:
    """Represents a user command with its raw text and planned steps."""
    raw: str        
    plan: Optional[str] = None
    steps: List[ActionStep] = field(default_factory=list)


@dataclass(frozen=True)
class Result:
    """
    Simple result for skill handlers (backward compatible).
    For new code, prefer Outcome[T] (Success/Failure) pattern.
    """
    ok: bool
    message: str
    data: Optional[Dict[str, Any]] = None
    
    @classmethod
    def success(cls, message: str, data: Optional[Dict[str, Any]] = None) -> "Result":
        return cls(ok=True, message=message, data=data)
    
    @classmethod
    def failure(cls, message: str) -> "Result":
        return cls(ok=False, message=message)

