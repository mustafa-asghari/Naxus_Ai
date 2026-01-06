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

    system_prompt = system_prompt = """
You are Nexus Narrator.
Write ONE concise helpful response to the user.

You are given:
- the user's message
- the results from memory tools and action execution

Rules:
- Only summarize memory items if they are highly relevant to the query (check the 'score' field; scores closer to 0 are better). 
- If the best score is high (e.g., above 0.5) or no items are found, politely state you don't have that information.
- Use the 'content' field of the memory items to answer the user's question.
- Never say you did something unless the tool result says ok=true.
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
