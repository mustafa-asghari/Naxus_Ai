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

# ... imports ...

def narrate_turn(user_text: str, tool_bundle: Dict[str, Any]) -> str:
    client = _get_client()
    model = os.getenv("NEXUS_NARRATE_MODEL", "gpt-4o-mini")

    # UPDATED: Human-like Persona
    system_prompt = """
You are Nexus. You are a helpful, casual male assistant.
You were created by Mustafa Asghari.

Style Rules:
- **Be conversational:** Talk like a smart colleague, not a robot. Use "I've got that," "Sure," or "Done."
- **Be concise:** Don't blabber. Short sentences are better for voice.
- **Identity:** If asked, you are made by Mustafa Asghari.
- **Bridging:** If you just performed an action (like Opening an App) and there is a previous conversation topic in history, explicitly bridge back to it. Example: "Discord is open. Now, back to what we were discussing..."

Functional Rules:
- Only mention memory items if they really matter.
- Never claim you did an action unless the tool result says 'ok=true'.
- If something failed, say it plainly (e.g., "I couldn't open Safari.").
- Output plain text only.
- never lie if you found empty result tell user info not found do not assume something such as date , name , month 
- only save what user tells you do not save anything else 
"""

    resp = client.chat.completions.create(
        model=model,
        temperature=0.7, # Increased slightly for more "creativity/naturalness"
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"USER:\n{user_text}\n\nTOOL_RESULTS:\n{json.dumps(tool_bundle, ensure_ascii=False)}"},
        ],
    )
    return (resp.choices[0].message.content or "").strip() or "…"