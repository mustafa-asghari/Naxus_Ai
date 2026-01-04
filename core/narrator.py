import json, os
from typing import Any, Dict
from openai import OpenAI

_client: OpenAI | None = None

def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = OpenAI(api_key=api_key)
    return _client

def narrate_turn(user_text: str, tool_bundle: Dict[str, Any]) -> str:
    client = _get_client()
    model = os.getenv("NEXUS_NARRATE_MODEL", "gpt-4o-mini")

    system_prompt = """
You are Nexus Narrator.
Write ONE concise helpful response to the user.

You are given:
- the user's message
- the results from memory tools and action execution

Rules:
- Never say you did something unless the tool result says ok=true.
- If memory read returned items, summarize them briefly and answer the question.
- If nothing found, say you couldn't find it.
- Mention actions performed and their success/failure.
- Do NOT output JSON. Output plain text only.
"""

    resp = client.chat.completions.create(
        model=model,
        temperature=0.4,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"USER:\n{user_text}\n\nTOOL_RESULTS:\n{json.dumps(tool_bundle, ensure_ascii=False)}"},
        ],
    )
    return (resp.choices[0].message.content or "").strip() or "…"
