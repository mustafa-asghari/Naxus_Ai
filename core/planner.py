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
# TurnPlan schema
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
    if actions is None or not isinstance(actions, list):
        return False
    for a in actions:
        if not isinstance(a, dict) or "intent" not in a:
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

    # memory_write
    mw = d.get("memory_write")
    if mw is not None:
        if not isinstance(mw, dict):
            return False
        if "should_store" not in mw or not isinstance(mw["should_store"], bool):
            return False
        if mw.get("should_store"):
            note = mw.get("note")
            if note is None or not isinstance(note, dict):
                return False
            content = note.get("content")
            if not isinstance(content, str) or not content.strip():
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

    if any(st.intent == Intent.CLOSE_ALL_APPS for st in steps):
        first = next(st for st in steps if st.intent == Intent.CLOSE_ALL_APPS)
        return [first]

    return steps


# ----------------------------
# Combined planner
# ----------------------------

def plan_turn(user_text: str, history: str = "", context: str = "") -> TurnPlan:
    """
    Decodes voice commands using LLM phonetic reasoning.
    """
    client = _get_client()
    model = os.getenv("NEXUS_PLAN_MODEL", "gpt-4o-mini")

    # SMART PROMPT: No hardcoded lists. Just "Principles".
    system_prompt = """
You are Nexus, an intelligent operating system assistant.
You are created by Mustafa Asghari.

**Your Input:** Raw transcription from a speech-to-text engine. It may contain phonetic errors (homophones) or misheard words.

**Your Goal:**
Infer the user's *true intent* by correcting phonetic errors based on the context of a desktop assistant.

**Reasoning Rules (Do not hardcode, THINK):**
1. **Phonetic Matching:** If the input sounds like a valid command, assume the valid command.
   - Example Context: "Tell me about my plant" -> "Plant" makes no sense here. "Plan" makes perfect sense. -> Action: Read Plans.
   - Example Context: "Ride a note" -> "Ride" is impossible. "Write" is a core feature. -> Action: Create Note.

1. **Resume Context:** If the user asks to "continue" or "go back", look at the [Chat History]. If the last Assistant message was interrupted or unfinished, the next action should be to finish that explanation.
2. **Action Chaining:** If the user says "Open Discord and tell me about the movie", you must generate a JSON with specific actions AND a clear memory of the topic.

**Available Tools (The valid commands):**
1) memory_read: Use for questions about past info, goals, or plans. { "query": "string", "limit": 5 }
2) memory_write: Use for saving new info. { "should_store": bool, "confidence": float, "note": {...} }
3) actions: list of { "intent": "...", "args": {...} }

**Supported Action Intents:**
- OPEN_APP {"app_name": "..."}
- CLOSE_APP {"app_name": "..."}
- CLOSE_ALL_APPS {}
- CREATE_NOTE {"content": "...", "folder": "..."}
- SEARCH_WEB {"query": "..."}
- EXIT {} (For quit/goodbye/stop)

Return ONLY valid JSON.
Output schema exactly:
{
  "memory_read": ... | null,
  "memory_write": ... | null,
  "actions": [...]
}
"""

    # Combine History + Context + User Input
    user_content = f"USER AUDIO TRANSCRIPT: {user_text}\n"
    
    if history:
        user_content += f"\n[Chat History]\n{history}\n"
        
    if context:
        user_content += f"\n[System Context (Running Apps)]\n{context}\n"

    resp = client.chat.completions.create(
        model=model,
        temperature=0, # Keep temp low for strict JSON, but the model will still do the reasoning
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    )
    content = (resp.choices[0].message.content or "").strip()

    try:
        data: Dict[str, Any] = json.loads(content)
    except json.JSONDecodeError:
        return TurnPlan(memory_read=None, memory_write=None, actions=[])

    if not _validate_turn_plan_dict(data):
        return TurnPlan(memory_read=None, memory_write=None, actions=[])

    # Parse memory_read
    mr = data.get("memory_read")
    memory_read: Optional[MemoryRead] = None
    if isinstance(mr, dict):
        q = str(mr.get("query", "")).strip()
        lim = int(mr.get("limit") or 5)
        if q:
            memory_read = MemoryRead(query=q, limit=lim)

    # Parse memory_write
    mw = data.get("memory_write")
    memory_write: Optional[MemoryWrite] = None
    if isinstance(mw, dict):
        should_store = bool(mw.get("should_store"))
        conf = float(mw.get("confidence") or 0.0)
        note = mw.get("note") if should_store else None
        memory_write = MemoryWrite(should_store=should_store, confidence=conf, note=note)

    # Parse actions
    actions_raw = data.get("actions") or []
    actions = _coerce_action_steps(actions_raw if isinstance(actions_raw, list) else [])

    return TurnPlan(memory_read=memory_read, memory_write=memory_write, actions=actions)