import json, os, re
from typing import Any, Dict, Generator
from openai import OpenAI

_client: OpenAI | None = None

# LLM Provider config
LLM_PROVIDER = os.getenv("NEXUS_LLM_PROVIDER", "local")
LLM_LOCAL_BASE = os.getenv("NEXUS_LLM_LOCAL_BASE", "http://127.0.0.1:1234/v1")
LLM_LOCAL_MODEL = os.getenv("NEXUS_LLM_LOCAL_MODEL", "qwen/qwen3-vl-8b")
LLM_MAX_TOKENS = int(os.getenv("NEXUS_LLM_MAX_TOKENS", "150"))  # Short for narrator


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        if LLM_PROVIDER == "local":
            _client = OpenAI(
                base_url=LLM_LOCAL_BASE,
                api_key="lm-studio"
            )
        else:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not set")
            _client = OpenAI(api_key=api_key)
    return _client


NARRATOR_PROMPT = """
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
- Some Apple app actions (Contacts/Notes/Messages/Mail/Reminders/Calendar/Maps) are executed via Apple MCP tools.
- Output plain text only.
- never lie if you found empty result tell user info not found do not assume something such as date , name , month 
- only save what user tells you do not save anything else 
"""


def narrate_turn(user_text: str, tool_bundle: Dict[str, Any]) -> str:
    """Non-streaming narrator - returns full response."""
    client = _get_client()
    
    if LLM_PROVIDER == "local":
        model = LLM_LOCAL_MODEL
    else:
        model = os.getenv("NEXUS_NARRATE_MODEL", "gpt-4o-mini")

    resp = client.chat.completions.create(
        model=model,
        temperature=0.7,
        max_tokens=LLM_MAX_TOKENS,  # Limit for speed
        messages=[
            {"role": "system", "content": NARRATOR_PROMPT},
            {"role": "user", "content": f"USER:\n{user_text}\n\nTOOL_RESULTS:\n{json.dumps(tool_bundle, ensure_ascii=False)}"},
        ],
    )
    return (resp.choices[0].message.content or "").strip() or "…"


def narrate_turn_streaming(user_text: str, tool_bundle: Dict[str, Any]) -> Generator[str, None, None]:
    """
    Streaming narrator - yields sentences as they're generated.
    Use this for real-time TTS (speak while generating).
    """
    client = _get_client()
    
    if LLM_PROVIDER == "local":
        model = LLM_LOCAL_MODEL
    else:
        model = os.getenv("NEXUS_NARRATE_MODEL", "gpt-4o-mini")

    stream = client.chat.completions.create(
        model=model,
        temperature=0.7,
        stream=True,  # Enable streaming
        messages=[
            {"role": "system", "content": NARRATOR_PROMPT},
            {"role": "user", "content": f"USER:\n{user_text}\n\nTOOL_RESULTS:\n{json.dumps(tool_bundle, ensure_ascii=False)}"},
        ],
    )
    
    # Buffer to accumulate text until we have a complete sentence
    buffer = ""
    sentence_endings = re.compile(r'[.!?]\s*')
    
    for chunk in stream:
        if chunk.choices[0].delta.content:
            buffer += chunk.choices[0].delta.content
            
            # Check if we have a complete sentence
            matches = list(sentence_endings.finditer(buffer))
            if matches:
                last_match = matches[-1]
                sentence = buffer[:last_match.end()].strip()
                buffer = buffer[last_match.end():]
                if sentence:
                    yield sentence
    
    # Yield any remaining text
    if buffer.strip():
        yield buffer.strip()