import re
from datetime import date
from typing import Any, Optional

WRITE_CONFIDENCE_AUTO = 0.85
WRITE_CONFIDENCE_ASK = 0.60

# simple redaction: prevents accidentally storing obvious secrets
_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",           # OpenAI-ish keys
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    r"password\s*[:=]\s*\S+",
]
def redact(text: str) -> str:
    out = text
    for p in _SECRET_PATTERNS:
        out = re.sub(p, "[REDACTED]", out, flags=re.IGNORECASE)
    return out

def validate_note_proposal(proposal: dict[str, Any]) -> tuple[bool, str]:
    # Must be shaped correctly
    if not isinstance(proposal, dict):
        return False, "proposal not a dict"
    if "should_store" not in proposal:
        return False, "missing should_store"
    if not isinstance(proposal.get("should_store"), bool):
        return False, "should_store not bool"
    if "confidence" in proposal and not isinstance(proposal["confidence"], (int, float)):
        return False, "confidence not number"
    if proposal.get("should_store"):
        note = proposal.get("note")
        if not isinstance(note, dict):
            return False, "missing note object"
        if not isinstance(note.get("content", ""), str) or not note.get("content"):
            return False, "note.content missing/empty"
        # optional deadline format if provided
        dl = note.get("deadline")
        if dl is not None and dl != "" and not isinstance(dl, str):
            return False, "deadline must be string or null"
    return True, "ok"

async def process_user_message_memory(
    *,
    mcp,
    session_id: str,
    user_text: str,
    proposal: dict[str, Any],
    ask_user_fn,  # function: (prompt:str)->bool
) -> dict[str, Optional[str]]:
    """
    1) Always log raw user text to Postgres events (via MCP).
    2) If LLM proposes a note, validate & gate it.
    3) If allowed, insert note into ClickHouse (via MCP).
    """
    safe_text = redact(user_text)

    event_id = await mcp.append_event(
        kind="user_msg",
        payload={"text": safe_text},
        session_id=session_id,
        tags=["user"],
    )

    ok, reason = validate_note_proposal(proposal)
    if not ok:
        # still fine: we logged raw text, but we won't store note
        return {"event_id": event_id, "note_id": None}

    should_store = proposal.get("should_store", False)
    conf = float(proposal.get("confidence", 0.0) or 0.0)

    if not should_store:
        return {"event_id": event_id, "note_id": None}

    note = proposal.get("note", {})
    title = str(note.get("title") or "")
    content = redact(str(note.get("content") or safe_text))
    deadline = note.get("deadline")  # expects YYYY-MM-DD or null
    plan = note.get("plan")
    status = str(note.get("status") or "")
    priority = int(note.get("priority") or 0)
    tags = note.get("tags") or []

    # Gate writes
    if conf >= WRITE_CONFIDENCE_AUTO:
        allowed = True
    elif conf >= WRITE_CONFIDENCE_ASK:
        allowed = ask_user_fn("This seems important. Save it to memory? (yes/no) ")
    else:
        allowed = False

    if not allowed:
        return {"event_id": event_id, "note_id": None}

    note_id = await mcp.insert_note(
        title=title,
        content=content,
        deadline=deadline,
        plan=plan,
        status=status,
        priority=priority,
        tags=tags,
        confidence=conf if conf else 0.8,
        source_event_id=event_id,
    )

    return {"event_id": event_id, "note_id": note_id}
