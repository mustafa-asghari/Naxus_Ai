# ClickHouse Memory Fix — Root Cause Analysis

## Summary

The ClickHouse memory read/write was silently failing due to **three separate issues** in `data/MCP/mcp_server.py`.

---

## Issue 1: Wrong `.env` Path

**Problem:**  
The dotenv loader was hardcoded to a misspelled path:
```python
load_dotenv(dotenv_path="/Users/mustafaasghari/code/Naxus/.env")  # "Naxus" not "Nexus"
```

This caused environment variables (`CH_HOST`, `CH_PORT`, etc.) to not load, making ClickHouse connect with default/wrong credentials.

**Fix:**  
Derive the project root dynamically:
```python
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")
```

---

## Issue 2: MCP Parameter Type Mismatch

**Problem:**  
The `ch_insert_note` function had strict type hints:
```python
def ch_insert_note(
    status: str = "",
    priority: int = 0,
    ...
)
```

But `nexus.py` passes `None` for these fields when the planner doesn't provide values:
```python
"status": note.get("status"),  # Returns None, not ""
"priority": note.get("priority"),  # Returns None, not 0
```

MCP uses Pydantic validation, which **rejected the call before our code even ran** because `None` is not a valid `str` or `int`.

**Fix:**  
Changed all parameters to `Optional[...]` and coerce `None` to defaults inside the function:
```python
def ch_insert_note(
    status: Optional[str] = None,
    priority: Optional[int] = None,
    ...
) -> dict[str, Any]:
    status = status or ""
    priority = priority if priority is not None else 0
```

---

## Issue 3: Wrong Date Format for ClickHouse

**Problem:**  
The `deadline` was being converted to an ISO string before insert:
```python
dl.isoformat()  # Returns "2026-12-31" (string)
```

But ClickHouse's `Date` column type requires an actual Python `datetime.date` object when using `clickhouse-connect`.

**Error:**
```
TypeError: unsupported operand type(s) for -: 'str' and 'datetime.date'
```

**Fix:**  
Pass the `date` object directly:
```python
dl,  # Pass date object, not string
```

---

## Debugging Notes

- **MCP servers use stdout for JSON-RPC** — any `print()` statements break the protocol. Debug logging must go to `stderr`.
- The insert appeared to succeed (no exception raised) because MCP validation failures happen *before* the tool function runs, so our try/except never caught anything.
- Adding step-by-step logging to stderr revealed exactly where each failure occurred.

---

## Files Changed

- `data/MCP/mcp_server.py` — All three fixes applied

## Lessons Learned

1. Always use dynamic paths for env file loading
2. MCP tool parameters should use `Optional[T]` if callers might pass `None`
3. Check database driver docs for expected Python types (don't assume strings work everywhere)

