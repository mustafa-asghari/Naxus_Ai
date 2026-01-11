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
    actions: List[ActionStep]
    memory_read: Optional[MemoryRead] = None
    memory_write: Optional[MemoryWrite] = None
    response_text: str = ""  # Conversational response from the planner


_client: OpenAI | None = None

# LLM Provider: "openai" (API, fast) or "local" (LM Studio)
LLM_PROVIDER = os.getenv("NEXUS_LLM_PROVIDER", "local")  # Using local LM Studio
LLM_LOCAL_BASE = os.getenv("NEXUS_LLM_LOCAL_BASE", "http://127.0.0.1:1234/v1")
LLM_LOCAL_MODEL = os.getenv("NEXUS_LLM_LOCAL_MODEL", "qwen/qwen3-vl-8b")
LLM_MAX_TOKENS = int(os.getenv("NEXUS_LLM_MAX_TOKENS", "256"))  # Limit tokens for speed


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if LLM_PROVIDER == "local":
            # LM Studio - OpenAI-compatible local API
            _client = OpenAI(
                base_url=LLM_LOCAL_BASE,
                api_key="lm-studio"  # LM Studio doesn't need real API key
            )
            print(f"[NEXUS] Using local LLM: {LLM_LOCAL_MODEL}")
        else:
            # OpenAI API
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

    # response_text (optional but recommended)
    if "response_text" in d and not isinstance(d["response_text"], str):
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


def plan_turn(user_text: str, history: str = "", context: str = "", on_speak: Optional[Callable[[str], None]] = None) -> TurnPlan:
    """
    Decodes voice commands using LLM phonetic reasoning with streaming.
    Detects 'SPEAK: "..."' line early to trigger TTS.
    """
    client = _get_client()
    
    # Select model based on provider
    if LLM_PROVIDER == "local":
        model = LLM_LOCAL_MODEL
    else:
        model = os.getenv("NEXUS_PLAN_MODEL", "gpt-4o-mini")
        
    from pathlib import Path
    prompt_file = Path(__file__).parent.parent / "prompts" / "planner_prompt.txt"
    system_prompt = prompt_file.read_text(encoding="utf-8")

    # Combine History + Context + User Input matches prompt expectations
    messages = [
        {"role": "system", "content": system_prompt},
    ]
    
    # Add chat history (naive parsing, ensuring correct role)
    # The history string passed in is usually just raw text blocks.
    # We'll just append it as context if simple, or try to be smarter.
    # For now, let's stick to the prompt structure expected:
    
    # Context Builder
    from datetime import datetime
    now = datetime.now()
    user_content = f"CURRENT DATE: {now.strftime('%Y-%m-%d (%A) %H:%M')}\n"
    user_content += f"USER AUDIO TRANSCRIPT: {user_text}\n"
    
    if history:
         user_content += f"\n[Chat History]\n{history}\n"
    if context:
         user_content += f"\n[System Context]\n{context}\n"

    messages.append({"role": "user", "content": user_content})

    try:
        stream = client.chat.completions.create(
            model=model,
            temperature=0,
            max_tokens=LLM_MAX_TOKENS,
            messages=messages,
            stream=True
        )
        
        full_content = ""
        line_buffer = ""
        speak_text_found = False
        captured_response_text = ""
        
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            full_content += delta
            line_buffer += delta
            
            if "\n" in line_buffer and not speak_text_found:
                lines = line_buffer.split("\n")
                # Process all complete lines
                for line in lines[:-1]:
                    clean_line = line.strip()
                    if clean_line.startswith("SPEAK:"):
                        # Found extracting speak text
                        raw_text = clean_line[6:].strip()
                        # Remove quotes if present
                        if raw_text.startswith('"') and raw_text.endswith('"'):
                            raw_text = raw_text[1:-1]
                        
                        captured_response_text = raw_text
                        speak_text_found = True
                        
                        if on_speak:
                            on_speak(captured_response_text)
                
                # Keep the last incomplete line
                line_buffer = lines[-1]

        # Post-processing: Extract JSON
        # If we found SPEAK line, the JSON should be after it.
        # But full_content has everything.
        
        valid_json_str = full_content
        
        # Heuristic: find start of JSON object
        brace_start = full_content.find('{')
        if brace_start != -1:
            valid_json_str = full_content[brace_start:]
            # And end
            brace_end = valid_json_str.rfind('}')
            if brace_end != -1:
                valid_json_str = valid_json_str[:brace_end+1]
        else:
            # Maybe no JSON? Just response?
            return TurnPlan(
                response_text=captured_response_text or full_content,
                actions=[]
            )

        data = json.loads(valid_json_str) 
        
        # If we didn't capture SPEAK from stream, maybe it's in JSON?
        if not captured_response_text:
             captured_response_text = str(data.get("response_text", "")).strip()

        # Parse memory/actions standard way
        mr = data.get("memory_read")
        memory_read: Optional[MemoryRead] = None
        if isinstance(mr, dict):
            memory_read = MemoryRead(query=str(mr.get("query", "")), limit=int(mr.get("limit") or 5))

        mw = data.get("memory_write")
        memory_write: Optional[MemoryWrite] = None
        if isinstance(mw, dict):
            should_store = bool(mw.get("should_store"))
            memory_write = MemoryWrite(should_store=should_store, confidence=float(mw.get("confidence") or 0.0), note=mw.get("note") if should_store else None)

        actions_raw = data.get("actions") or []
        actions = _coerce_action_steps(actions_raw if isinstance(actions_raw, list) else [])

        return TurnPlan(
            response_text=captured_response_text,
            memory_read=memory_read, 
            memory_write=memory_write, 
            actions=actions
        )

    except Exception as e:
        print(f"[Planner] Error: {e}")
        return TurnPlan(
            response_text=f"I encountered an error: {e}",
            actions=[]
        )