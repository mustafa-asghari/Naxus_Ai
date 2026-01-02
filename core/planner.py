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
            # Keep only the first CLOSE_ALL_APPS step and discard others (strict)
            steps = [close_all_steps[0]]

        if not plan:
            plan = "I will perform the requested actions."

    return Command(raw=raw, mode=mode, plan=plan, steps=steps)
