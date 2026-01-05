# core/planner.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from openai import OpenAI

from core.intent import Intent
from core.models import ActionStep


# ----------------------------
# TurnPlan schema (planner output)
# ----------------------------

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
    """
    One user message can propose:
    - memory_read (optional)  -> Nexus may call ch_search_notes_text
    - memory_write (optional) -> Nexus may call ch_insert_note (gated)
    - actions (optional list) -> Nexus may execute (gated/confirmed)
    """
    memory_read: Optional[MemoryRead]
    memory_write: Optional[MemoryWrite]
    actions: List[ActionStep]


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=api_key)
    return _client


# ----------------------------
# Strict validation helpers
# ----------------------------

def _validate_turn_plan_dict(d: Any) -> bool:
    if not isinstance(d, dict):
        return False

    # actions
    actions = d.get("actions")
    if actions is None:
        return False
    if not isinstance(actions, list):
        return False
    for a in actions:
        if not isinstance(a, dict):
            return False
        if "intent" not in a:
            return False
        if "args" in a and a["args"] is not None and not isinstance(a["args"], dict):
            return False

    # memory_read
    mr = d.get("memory_read")
    if mr is not None:
        if not isinstance(mr, dict):
            return False
        if not isinstance(mr.get("query"), str) or not mr["query"].strip():
            return False
        if "limit" in mr and not isinstance(mr["limit"], (int, float)):
            return False

    # memory_write
    mw = d.get("memory_write")
    if mw is not None:
        if not isinstance(mw, dict):
            return False
        if "should_store" not in mw or not isinstance(mw["should_store"], bool):
            return False
        if "confidence" in mw and not isinstance(mw["confidence"], (int, float)):
            return False
        if mw.get("should_store"):
            note = mw.get("note")
            if note is None or not isinstance(note, dict):
                return False
            content = note.get("content")
            if not isinstance(content, str) or not content.strip():
                return False
            dl = note.get("deadline")
            if dl is not None and dl != "" and not isinstance(dl, str):
                return False

    return True


def _coerce_action_steps(actions_raw: list[Any]) -> list[ActionStep]:
    steps: list[ActionStep] = []
    for s in actions_raw:
        if not isinstance(s, dict):
            continue
        intent_name = str(s.get("intent", "")).upper()
        intent = Intent[intent_name] if intent_name in Intent.__members__ else Intent.UNKNOWN
        args = s.get("args")
        if not isinstance(args, dict):
            args = {}
        steps.append(ActionStep(intent=intent, args=args))

    # strict: if CLOSE_ALL_APPS appears, keep ONLY that one step
    if any(st.intent == Intent.CLOSE_ALL_APPS for st in steps):
        first = next(st for st in steps if st.intent == Intent.CLOSE_ALL_APPS)
        return [first]

    return steps


# ----------------------------
# Combined planner (one call) -> TurnPlan
# ----------------------------

def plan_turn(user_text: str, context: str = "") -> TurnPlan:
    """
    Combined "turn planner" that replaces:
      - parse_command()
      - propose_memory_note()
    with ONE model call that can propose both actions and memory work.
    Nexus still gates + executes.
    - Use the provided [Context] to resolve fuzzy app names (e.g., "code" -> "Visual Studio Code").
    """
    client = _get_client()
    model = os.getenv("NEXUS_PLAN_MODEL", "gpt-4o-mini")

    system_prompt = """
You are Nexus Turn Planner for a macOS assistant.

Return ONLY valid JSON. No markdown. No extra keys.

Your job: propose what Nexus should do this turn.
Nexus (code) will validate/gate/execute. You NEVER execute anything.

You may propose:
1) memory_read:
   - Use when the user asks to recall past info ("what did I do", "my goals", "my deadline", "my exam score", etc.)
   - Format: { "query": "<string>", "limit": 5 }

2) memory_write:
   - Use when the user states important info to remember:
     goals, deadlines, plans, commitments, constraints, preferences
   - Format:
     {
       "should_store": boolean,
       "confidence": number,
       "note": {
         "title": string,
         "content": string,
         "deadline": "YYYY-MM-DD" | null,
         "plan": object | null,
         "status": string | null,
         "priority": integer | null,
         "tags": array<string> | null
       } | null
     }
   - If not important: should_store=false and note=null
   - Never include secrets in note.content.

3) actions: a list of action steps (can be empty)
   Supported intents:
   - OPEN_APP  args {"app_name": "<Application Name>"}
   - CLOSE_APP args {"app_name": "<Application Name>"}
   - CLOSE_ALL_APPS args {}   (IMPORTANT: if included, it MUST be the ONLY action step)
   - SEARCH_WEB: Search the internet for live information.
     Args: "query": "the search query string"
    
Rules:
- Do not output terminal commands.
- Do not claim you executed anything.
- If user combines requests (memory + open app), include both.
- actions MUST always be a list (possibly empty).
- memory_read and memory_write can be null if not needed.
- Use the provided [Context] to resolve fuzzy app names (e.g., "code" -> "Visual Studio Code").

Output schema exactly:
{
  "memory_read": { "query": "...", "limit": 5 } | null,
  "memory_write": { "should_store": true/false, "confidence": 0-1, "note": {...} | null } | null,
  "actions": [ { "intent": "...", "args": {...} }, ... ]
}
""".strip()

   # Prepare the user message with context
    user_content = user_text
    if context:
        user_content += f"\n\n[Context]\n{context}"

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    content = (resp.choices[0].message.content or "").strip()

    try:
        data: Dict[str, Any] = json.loads(content)
    except json.JSONDecodeError:
        # safe fallback: do nothing
        return TurnPlan(memory_read=None, memory_write=None, actions=[])

    if not _validate_turn_plan_dict(data):
        return TurnPlan(memory_read=None, memory_write=None, actions=[])

    # memory_read
    mr = data.get("memory_read")
    memory_read: Optional[MemoryRead] = None
    if isinstance(mr, dict):
        q = str(mr.get("query", "")).strip()
        lim = int(mr.get("limit") or 5)
        lim = max(1, min(20, lim))
        if q:
            memory_read = MemoryRead(query=q, limit=lim)

    # memory_write
    mw = data.get("memory_write")
    memory_write: Optional[MemoryWrite] = None
    if isinstance(mw, dict):
        should_store = bool(mw.get("should_store"))
        conf = float(mw.get("confidence") or 0.0)
        conf = max(0.0, min(1.0, conf))
        note = mw.get("note") if should_store else None
        memory_write = MemoryWrite(should_store=should_store, confidence=conf, note=note if isinstance(note, dict) else None)

    # actions
    actions_raw = data.get("actions") or []
    actions = _coerce_action_steps(actions_raw if isinstance(actions_raw, list) else [])

    return TurnPlan(memory_read=memory_read, memory_write=memory_write, actions=actions)
