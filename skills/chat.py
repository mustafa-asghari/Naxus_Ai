from __future__ import annotations

import os
from openai import OpenAI

from core.models import Command, Result


_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=api_key)
    return _client


def handle_chat(cmd: Command) -> Result:
    """
    Chat-only lane: give a reply, never run anything on the machine.
    """
    client = _get_client()
    model = os.getenv("NEXUS_CHAT_MODEL", "gpt-4o-mini")

    system_prompt = (

     """    
        You are Nexus, a polite macOS personal assistant.\n
        Be concise.\n
        Never claim you executed actions.\n
        If user asks for computer actions, say you can plan and ask for confirmation.\n
        
     """
        
    )

    resp = client.chat.completions.create(
        model=model,
        temperature=0.7,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": cmd.raw},
        ],
    )

    text = (resp.choices[0].message.content or "").strip()
    return Result(ok=True, message=text if text else "…")
