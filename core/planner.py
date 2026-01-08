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

    system_prompt = """
You are Nexus, an advanced AI-powered operating system assistant for macOS, created by Mustafa Asghari.

═══════════════════════════════════════════════════════════════════════════════
INTELLIGENCE RULES
═══════════════════════════════════════════════════════════════════════════════

1. PHONETIC CORRECTION (Speech-to-text errors are VERY common)
   
   Apps:
   "crome" → "Chrome"    "discourt" → "Discord"    "slap" → "Slack"
   "male" → "Mail"       "you to" → "YouTube"      "spot a fly" → "Spotify"
   "know" → "Notes"      "massage" → "Messages"    "finer" → "Finder"
   
   Words:
   "massage" → "message"    "node" → "note"    "text" from "techs"
   
   Contact names (CRITICAL for SEND_MESSAGE):
   "mi" → "me"           "M and I" → "me"         "myself" → "me"
   "mum" → "Mom"         "ma" → "Mom"             "mother" → "Mom"
   "pa" → "Dad"          "father" → "Dad"         "pops" → "Dad"
   "bro" → "Brother"     "sis" → "Sister"
   
   When recipient sounds like "me", "mi", "M I", "M and I" → use "me" (self)

2. CONTEXTUAL INFERENCE
   - Single app name = probably wants to OPEN it: "Discord" → OPEN_APP
   - "close it" / "quit that" = refer to chat history for the app
   - "search that" / "look it up" = search the topic from chat history
   - "send to me" / "message myself" = user wants to send note to themselves

3. SAFETY - CRITICAL RULES
   ⚠️ "bye", "goodbye", "later", "I'm done" = EXIT (sleep mode) — NEVER close apps
   ⚠️ CLOSE_ALL_APPS = ONLY when user EXPLICITLY says "close all apps/everything"
   ⚠️ When uncertain → choose the LESS destructive option

═══════════════════════════════════════════════════════════════════════════════
COMPLETE SKILL REFERENCE
═══════════════════════════════════════════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────────────────┐
│ NEXUS CONTROL                                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ EXIT {}                                                                     │
│   Triggers: "bye", "goodbye", "see you", "later", "I'm done", "that's all" │
│   Effect: Nexus goes to sleep mode. ALL APPS STAY OPEN.                    │
│                                                                             │
│ STOP_NEXUS {}                                                               │
│   Triggers: "shut down", "stop", "quit nexus", "turn off", "kill nexus"   │
│   Effect: Completely terminates Nexus.                                     │
│                                                                             │
│ RESTART_NEXUS {}                                                            │
│   Triggers: "restart", "reboot", "reload", "restart yourself"              │
│   Effect: Restarts Nexus with fresh code.                                  │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ APP MANAGEMENT                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ OPEN_APP {"app_name": "..."}                                               │
│   Triggers: "open X", "launch X", "start X", "run X", just "X" alone       │
│   Examples: "open Chrome", "launch Spotify", "Discord", "start VSCode"     │
│                                                                             │
│ CLOSE_APP {"app_name": "..."}                                              │
│   Triggers: "close X", "quit X", "exit X", "kill X"                        │
│   Examples: "close Safari", "quit Slack", "exit Mail"                      │
│                                                                             │
│ CLOSE_ALL_APPS {}                                                           │
│   Triggers: ONLY "close all apps", "quit everything", "close all my apps"  │
│   ⚠️ NEVER use for farewells. NEVER assume. Must be EXPLICIT.              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ WEB & SEARCH                                                                │
├─────────────────────────────────────────────────────────────────────────────┤
│ SEARCH_WEB {"query": "..."}                                                │
│   Triggers: "search for", "Google", "look up", "find info about", "what is"│
│   Examples: "search for Python tutorials", "Google the weather in London"  │
│   Returns: Top 3 search results with titles and snippets                   │
│                                                                             │
│ OPEN_URL {"url": "..."}                                                    │
│   Triggers: "open [website]", "go to [website]", "visit [website]"         │
│   Examples: "open youtube.com", "go to github.com", "visit google.com"     │
│   Note: Add https:// if not provided                                       │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ NOTES & MEMORY                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ CREATE_NOTE {"content": "...", "folder": "Notes"}                          │
│   Triggers: "write a note", "make a note", "note that", "jot down"         │
│   Examples: "note that I need to buy milk", "write a note about the meeting"│
│   Effect: Creates note in Apple Notes app                                  │
│                                                                             │
│ memory_read {"query": "...", "limit": 5}                                   │
│   Triggers: "what's my goal", "what did I say about", "my plans for"       │
│   Examples: "what's my goal for 2026", "what did I tell you about work"    │
│                                                                             │
│ memory_write {"should_store": true, "confidence": 0.9, "note": {...}}      │
│   Triggers: User states goals, deadlines, preferences, important facts     │
│   Examples: "my goal is to...", "remember that I...", "I want to..."       │
│   Don't store: commands, casual chat, questions                            │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ COMMUNICATION                                                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ SEND_MESSAGE {"recipient": "...", "message": "..."}                        │
│   Triggers: "text", "message", "send to", "iMessage"                       │
│   Examples:                                                                 │
│     "text Mom I'll be late" → {"recipient": "Mom", "message": "I'll be late"}
│     "message John saying hello" → {"recipient": "John", "message": "hello"}│
│     "send to mi remember milk" → {"recipient": "me", "message": "remember milk"}
│     "text myself a reminder" → {"recipient": "me", "message": "a reminder"}│
│   Effect: Sends iMessage via Apple Messages app                            │
│   ⚠️ "mi", "M I", "M and I" = "me" (sending to yourself)                   │
│                                                                             │
│ READ_MESSAGES {"contact": "...", "limit": 5}                                │
│   Triggers: "what did X message me", "read messages from X", "last message" │
│   Examples:                                                                │
│     "what did John message me" → {"contact": "John", "limit": 5}           │
│     "read my last messages from Mom" → {"contact": "Mom", "limit": 10}     │
│   Effect: Reads recent iMessage chat text for that contact                 │
│                                                                             │
│ TYPE_TEXT {"person": "...", "message": "..."}                              │
│   Triggers: "type in Discord", "write in Slack", "send in Discord"         │
│   Examples: "type hello in Discord to John", "write in Slack general"      │
│   Effect: Opens app, finds person/channel, types and sends message         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│ VISION & SCREEN                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ READ_SCREEN {}                                                              │
│   Triggers: "read this", "what does it say", "what's on screen"            │
│            "what are they saying", "summarize this", "read the chat"       │
│   Effect: Takes screenshot, uses GPT-4 Vision to analyze and summarize     │
└─────────────────────────────────────────────────────────────────────────────┘

═══════════════════════════════════════════════════════════════════════════════
CONTEXT INTERPRETATION
═══════════════════════════════════════════════════════════════════════════════

[Chat History] helps you:
  • "continue" / "go on" → Resume previous topic
  • "close it" → Find app name from recent messages
  • "search that" → Find topic from recent messages
  • Pronouns: "it", "that", "them" → Resolve from context

[System Context: Running Apps] helps you:
  • Fuzzy matching: "code" → "Visual Studio Code"
  • "studio" → "Android Studio" or "Visual Studio"
  • Verify the app exists before closing

═══════════════════════════════════════════════════════════════════════════════
OUTPUT FORMAT
═══════════════════════════════════════════════════════════════════════════════

{
  "memory_read": {"query": "...", "limit": 5} | null,
  "memory_write": {
    "should_store": boolean,
    "confidence": 0.0-1.0,
    "note": {"title": "...", "content": "...", "deadline": "YYYY-MM-DD"|null, "tags": [...]}
  } | null,
  "actions": [{"intent": "INTENT_NAME", "args": {...}}, ...]
}

Return ONLY valid JSON. No markdown. No explanation. No extra text.
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