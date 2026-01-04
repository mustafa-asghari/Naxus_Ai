from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from openai import OpenAI

from core.models import ActionStep, Command
from core.intent import Intent, Mode


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=api_key)
    return _client


def parse_command(raw: str) -> Command:
    """
    Ask OpenAI whether this is just a chat or a request to do something.
    If it is an action, grab a short plan and a list of steps. Nothing runs here.
    """
    client = _get_client()
    model = os.getenv("NEXUS_INTENT_MODEL", "gpt-4o-mini")

    system_prompt = (
        "You are Nexus, an intent planner for a macOS assistant.\n"
        "Return ONLY valid JSON. No markdown.\n\n"
        "Decide if the user is chatting (CHAT) or requesting computer actions (ACTION).\n\n"
        "Supported ACTION intents:\n"
        "- OPEN_APP: args {\"app_name\": \"<Application Name>\"}\n"
        "- CLOSE_APP: args {\"app_name\": \"<Application Name>\"}\n"
        "- CLOSE_ALL_APPS: args {}  (IMPORTANT: must be a SINGLE step only)\n\n"
        "Rules:\n"
        "- Never claim you executed anything.\n"
        "- Never list running applications.\n"
        "- Never include terminal commands.\n"
        "- If unsure or unsupported, choose CHAT.\n\n"
        "Output JSON format:\n"
        "{\n"
        "  \"mode\": \"CHAT\" or \"ACTION\",\n"
        "  \"plan\": \"...\" (optional for CHAT, required for ACTION),\n"
        "  \"steps\": [\n"
        "     {\"intent\": \"OPEN_APP|CLOSE_APP|CLOSE_ALL_APPS\", \"args\": {...}},\n"
        "     ...\n"
        "  ]\n"
        "}"
    )

    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": raw},
        ],
    )

    content = (resp.choices[0].message.content or "").strip()

    # If the model returns junk JSON, treat it as a chat and move on
    try:
        data: Dict[str, Any] = json.loads(content)
    except json.JSONDecodeError:
        return Command(raw=raw, mode=Mode.CHAT, plan=None, steps=[])

    mode_str = str(data.get("mode", "CHAT")).upper()
    mode = Mode.ACTION if mode_str == "ACTION" else Mode.CHAT

    plan = data.get("plan")
    steps_raw = data.get("steps") or []

    steps: List[ActionStep] = []
    if mode == Mode.ACTION:
        for s in steps_raw:
            intent_name = str(s.get("intent", "UNKNOWN")).upper()
            intent = Intent[intent_name] if intent_name in Intent.__members__ else Intent.UNKNOWN
            args = s.get("args") or {}
            if not isinstance(args, dict):
                args = {}

            steps.append(ActionStep(intent=intent, args=args))

        # If it claims ACTION but gives no usable steps, fall back to chat
        if not steps or all(st.intent == Intent.UNKNOWN for st in steps):
            return Command(raw=raw, mode=Mode.CHAT, plan=None, steps=[])

        # CLOSE_ALL_APPS should be a single step; drop any extras to stay safe
        close_all_steps = [st for st in steps if st.intent == Intent.CLOSE_ALL_APPS]
        if close_all_steps:
            steps = [close_all_steps[0]]

        if not plan:
            plan = "I will perform the requested actions."

    return Command(raw=raw, mode=mode, plan=plan, steps=steps)


def propose_memory_note(raw: str) -> Dict[str, Any]:
    """
    Ask OpenAI if the user's message contains IMPORTANT memory worth storing as a NOTE.

    Returns a proposal dict:
    {
      "should_store": bool,
      "confidence": 0.0-1.0,
      "note": {
        "title": str,
        "content": str,
        "deadline": "YYYY-MM-DD" | null,
        "plan": object | null,
        "status": str | null,
        "priority": int | null,
        "tags": [str] | null
      } | null
    }
    """
    client = _get_client()
    model = os.getenv("NEXUS_MEMORY_MODEL", os.getenv("NEXUS_INTENT_MODEL", "gpt-4o-mini"))

    # Optional: provide "today" anchor to resolve "12th January" correctly.
    # If unset, the extractor still works but dates may be less precise.
    today_iso = os.getenv("NEXUS_TODAY_ISO", "")  # e.g. "2026-01-04"

    system_prompt = (
        "You are Nexus Memory Extractor.\n"
        "Return ONLY valid JSON. No markdown.\n\n"
        "Task: Decide if the user's message contains IMPORTANT long-term info worth saving.\n\n"
        "Save as NOTE if it contains:\n"
        "- goals, deadlines, plans, commitments\n"
        "- project requirements, constraints, preferences\n"
        "- info Nexus should remember for future help\n\n"
        "Do NOT save if it is:\n"
        "- casual chat, jokes, greetings\n"
        "- one-off question with no lasting value\n\n"
        "Rules:\n"
        "- Never store secrets (passwords, API keys, private keys). If present, exclude them from note.content.\n"
        "- If a date is mentioned (e.g. '12th January'), infer year using today's date if provided.\n"
        "- If unsure: should_store=false.\n\n"
        "Output JSON schema:\n"
        "{\n"
        "  \"should_store\": true|false,\n"
        "  \"confidence\": 0.0-1.0,\n"
        "  \"note\": {\n"
        "    \"title\": \"...\",\n"
        "    \"content\": \"...\",\n"
        "    \"deadline\": \"YYYY-MM-DD\" or null,\n"
        "    \"plan\": object or null,\n"
        "    \"status\": \"active|done|paused\" or null,\n"
        "    \"priority\": 1-5 or null,\n"
        "    \"tags\": [\"...\"] or null\n"
        "  } or null\n"
        "}\n"
        "If should_store=false, set note=null.\n"
    )

    user_content = raw
    if today_iso:
        user_content = f"(Today: {today_iso})\nUser message: {raw}"

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
        return {"should_store": False, "confidence": 0.0, "note": None}

    # Defensive normalization (avoid crashes / weird shapes)
    should_store = bool(data.get("should_store", False))
    conf = data.get("confidence", 0.0)
    if not isinstance(conf, (int, float)):
        conf = 0.0
    conf = max(0.0, min(1.0, float(conf)))

    note = data.get("note", None)

    if should_store:
        if not isinstance(note, dict):
            return {"should_store": False, "confidence": 0.0, "note": None}

        # Ensure minimum fields exist
        title = note.get("title")
        content_field = note.get("content")

        if not isinstance(content_field, str) or not content_field.strip():
            # fallback to raw message
            note["content"] = raw

        if not isinstance(title, str) or not title.strip():
            note["title"] = "Important note"

        # Deadline can be null or string; leave as-is if missing
        return {"should_store": True, "confidence": conf, "note": note}

    return {"should_store": False, "confidence": conf, "note": None}
