from __future__ import annotations
import subprocess
import sys
import asyncio
import logging
import os
from skills.wake_word import wait_for_wake_word
import re
from pathlib import Path
from typing import List, Dict, Any, Optional
from openai import OpenAI
from dotenv import load_dotenv
from skills.voice import listen_to_user , speak_text

# Core
from core.models import ActionStep, Command, Result
from core.intent import Intent
from core.safety import check_command
from core.router import Router

# New Plan-B components
from core.planner import plan_turn
from core.narrator import narrate_turn

# Skills
from skills.system import open_app, close_app
from macos.running_apps import get_running_apps
from skills.note import create_note
from skills.web_search import search_web
from skills.browser import open_url
from skills.discord import send_discord_message
from skills.message import send_imessage, search_contacts, read_messages
from skills.discord import read_active_window

# MCP
from data.MCP.mcp_client import MCPMemoryClient



IS_RUNNING = True
WRITE_CONFIDENCE_AUTO = 0.65
WRITE_CONFIDENCE_ASK = 0.60

_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    r"password\s*[:=]\s*\S+",
]


def redact(text: str) -> str:
    out = text
    for p in _SECRET_PATTERNS:
        out = re.sub(p, "[REDACTED]", out, flags=re.IGNORECASE)
    return out


def ask_yes_no(prompt: str) -> bool:
    while True:
        ans = input(prompt).strip().lower()
        if ans in {"y", "yes"}:
            return True
        if ans in {"n", "no"}:
            return False
        print("Please answer yes or no.")

def configure_logging() -> logging.Logger:
    level = getattr(logging, os.getenv("NEXUS_LOG_LEVEL", "INFO").upper())
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING) 
    logging.getLogger("primp").setLevel(logging.WARNING)
    
    return logging.getLogger("nexus")


def expand_steps(actions: list[ActionStep]) -> list[ActionStep]:
    expanded: list[ActionStep] = []
    for step in actions:
        if step.intent == Intent.CLOSE_ALL_APPS:
            for app in get_running_apps():
                expanded.append(ActionStep(Intent.CLOSE_APP, {"app_name": app}))
        else:
            expanded.append(step)
    return expanded


def _resolve_contact_name(query: str) -> tuple[Optional[str], list[str], Optional[str]]:
    """
    Resolve a possibly-misheard contact string into a concrete contact name.
    Returns (resolved_name_or_none, candidates, error).
    """
    q = (query or "").strip()
    if not q:
        return None, [], None
    if q.lower() in {"me", "myself"}:
        return "me", ["me"], None
    # Allow direct handles (phone/email) without Contacts lookup
    if re.fullmatch(r"[+\d][\d\s().-]{6,}", q) or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", q):
        return q, [q], None
    # Try Contacts app lookup
    candidates, err = search_contacts(q, limit=5)
    if err:
        return None, [], err
    if len(candidates) == 1:
        return candidates[0], candidates, None
    return None, candidates, None


# -------------------------------------------------
# MAIN LOOP
# -------------------------------------------------
def is_confirmation_positive(user_text: str) -> bool:
    """
    Determines if the user's response means YES or NO.
    Uses fast keyword matching first, then AI for complex phrases.
    """
    if not user_text:
        return False

    clean = user_text.lower().strip()
    print(f"Checking confirmation for: '{clean}'")

    # Fast positive matches
    positive_words = {
        "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright",
        "do it", "confirm", "confirmed", "go ahead", "proceed", "affirmative",
        "absolutely", "definitely", "of course", "for sure", "go for it",
        "please", "please do", "yes please", "that's right", "correct",
        "right", "uh huh", "mm hmm", "yea", "ya", "yas"
    }

    # Fast negative matches
    negative_words = {
        "no", "nah", "nope", "cancel", "stop", "don't", "abort",
        "never", "negative", "no way", "not now", "wait", "hold on",
        "actually no", "no thanks", "nope nope"
    }

    # Check exact matches first
    if clean in positive_words:
        print("→ Fast match: POSITIVE")
        return True
    if clean in negative_words:
        print("→ Fast match: NEGATIVE")
        return False

    # Check if any positive phrase is contained
    for phrase in positive_words:
        if phrase in clean and len(phrase) > 2:
            print(f"→ Contains positive phrase: '{phrase}'")
            return True

    # Check if any negative phrase is contained
    for phrase in negative_words:
        if phrase in clean and len(phrase) > 2:
            print(f"→ Contains negative phrase: '{phrase}'")
            return False

    # AI fallback for complex responses like "I'm pretty sure", "I guess so"
    try:
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": """You are a YES/NO classifier. The user was asked to confirm an action.
Determine if their response means YES (proceed) or NO (cancel).

Examples of YES: "I'm sure", "I'm pretty sure", "go for it", "I guess so", "why not", "let's do it"
Examples of NO: "wait", "hold on", "not yet", "I changed my mind", "actually no"

Output ONLY the word YES or NO."""
                },
                {"role": "user", "content": f"User response: '{user_text}'"}
            ],
            temperature=0,
            max_tokens=3
        )
        decision = (resp.choices[0].message.content or "").strip().upper()
        result = "YES" in decision
        print(f"→ AI decision: {decision} → {'POSITIVE' if result else 'NEGATIVE'}")
        return result
    except Exception as e:
        print(f"AI Check failed: {e}")
        # Default to NO for safety if AI fails
        return False
    
def stop_nexus_program():
    """
    Stops the python script. 
    """
    print("Terminating Nexus Program...")
    speak_text("Shutting down. Goodbye.")
    # This kills the current python process
    os._exit(0)

def restart_nexus_program():
    """
    Restarts the current python script.
    """
    print("Restarting Nexus Program...")
    speak_text("Restarting myself now.")
    # This command replaces the current process with a new instance of itself
    os.execv(sys.executable, ['python'] + sys.argv)

async def main() -> int:
    load_dotenv()
    log = configure_logging()

    base_dir = Path(__file__).resolve().parent
    server_path = base_dir / "data" / "MCP" / "mcp_server.py"

    mcp = MCPMemoryClient(server_cmd=[sys.executable, str(server_path)])
    await mcp.start()
    await mcp.init_schemas()

    router = Router()
    router.register_action(Intent.OPEN_APP, open_app)
    router.register_action(Intent.CLOSE_APP, close_app)
    router.register_action(Intent.SEARCH_WEB, search_web)
    router.register_action(Intent.CREATE_NOTE, create_note)
    router.register_action(Intent.OPEN_URL, open_url)
    router.register_action(Intent.SEND_MESSAGE, send_imessage)
    router.register_action(Intent.TYPE_TEXT, send_discord_message)
    router.register_action(Intent.READ_SCREEN, read_active_window)
    router.register_action(Intent.READ_MESSAGES, read_messages)
        
        


    session_id = os.getenv("NEXUS_SESSION_ID", "default")
    
    print("Nexus started. Listening... (Say 'quit' to exit)")
    
    try:
        # --- OUTER LOOP: THE GUARD (Sleep Mode) ---
        while True:
            # 1. Wait here silently until you say the wake word ("Nexus" or "Jarvis")
            if not wait_for_wake_word():
                continue 

            # --- INNER LOOP: THE CONVERSATION (Awake Mode) ---
            print("--- ENTERING CONVERSATION MODE ---")
            
            # 1. LOAD HISTORY FROM DATABASE
            hist_data = await mcp.call("pg_get_recent_history", {"session_id": session_id, "limit": 10})
            chat_history = hist_data.get("history", [])
            
            # 2. DECIDE GREETING BASED ON CONTEXT
            if chat_history:
                last_msg = chat_history[-1]
                print(f"[RESTORED MEMORY] Context: {last_msg[:50]}...")
                # If we have history, say something contextual
                speak_text("I'm back. As we were saying...")
            else:
                speak_text("I'm listening whats on your mind.")

            conversation_active = True
            
            while conversation_active:
                
                # 2. Listen for command
                raw = listen_to_user() 
                
                # --- FIX 1: STRICT INPUT FILTER ---
                # If raw is None, empty, or just whitespace -> Skip
                if not raw or not raw.strip():
                    continue

                # If the input is too short (less than 4 letters), it's likely noise -> Skip
                # This stops "ah", "um", "ok" from triggering complex actions
                if len(raw.strip()) < 4:
                    print(f"Ignored noise: '{raw}'")
                    continue

                # -------------------------------------------------
                # LOG USER EVENT
                # -------------------------------------------------
                user_event = await mcp.call(
                    "pg_append_event",
                    {
                        "kind": "user_msg",
                        "payload": {"text": redact(raw)},
                        "session_id": session_id,
                        "tags": ["user"],
                    },
                )
                user_event_id = user_event.get("event_id")

                # -------------------------------------------------
                # PLAN TURN
                # -------------------------------------------------
                app_list = get_running_apps()
                history_text = "\n".join(chat_history[-4:]) 
                
                plan = plan_turn(
                    raw, 
                    history=history_text, 
                    context=f"Running Apps: {', '.join(app_list)}"
                )
                
                tool_bundle: Dict[str, Any] = {
                    "memory_read": None,
                    "memory_write": None,
                    "actions": [],
                }

                # -------------------------------------------------
                # MEMORY OPS
                # -------------------------------------------------
                if plan.memory_read:
                    res = await mcp.call("ch_search_notes_text", {
                        "query": plan.memory_read.query,
                        "limit": plan.memory_read.limit,
                    })
                    tool_bundle["memory_read"] = res
                    
                if plan.memory_write and plan.memory_write.should_store:
                    conf = float(plan.memory_write.confidence or 0.0)
                    note = plan.memory_write.note or {}
                    
                    allowed = False
                    if conf >= WRITE_CONFIDENCE_AUTO:
                        allowed = True
                    elif conf >= WRITE_CONFIDENCE_ASK:
                        # ASK FOR CONFIRMATION (Voice)
                        speak_text("Should I save that to memory?")
                        conf_ans = listen_to_user()
                        if conf_ans and "yes" in conf_ans.lower():
                            allowed = True

                    if allowed:
                        res = await mcp.call("ch_insert_note", {
                            "title": note.get("title", ""),
                            "content": redact(str(note.get("content") or raw)),
                            "deadline": note.get("deadline"),
                            "plan": note.get("plan"),
                            "status": note.get("status", ""),
                            "priority": int(note.get("priority") or 0),
                            "tags": note.get("tags") or [],
                            "confidence": conf,
                            "source_event_id": user_event_id,
                        })
                        tool_bundle["memory_write"] = {"stored": True, **res}

                # -------------------------------------------------
                # ACTIONS (EXECUTION)
                # -------------------------------------------------
                expanded_actions = expand_steps(plan.actions)

                # -------------------------------------------------
                # CONTACT RESOLUTION (for iMessage actions)
                # -------------------------------------------------
                # If the model outputs a fuzzy recipient like "this guy" / "mi" / partial name,
                # resolve it against Contacts and (if ambiguous) ask the user to choose.
                contact_resolution_block: Optional[str] = None
                for idx, step in enumerate(expanded_actions):
                    if step.intent in {Intent.SEND_MESSAGE, Intent.READ_MESSAGES}:
                        key = "recipient" if step.intent == Intent.SEND_MESSAGE else "contact"
                        raw_target = str((step.args or {}).get(key) or "")
                        if not raw_target.strip():
                            continue

                        resolved, candidates, err = _resolve_contact_name(raw_target)
                        if err:
                            contact_resolution_block = (
                                "I couldn't access your Contacts. Please allow Nexus to access Contacts in "
                                "System Settings → Privacy & Security → Contacts (and Automation), then try again."
                            )
                            break
                        if resolved:
                            expanded_actions[idx] = ActionStep(step.intent, {**(step.args or {}), key: resolved})
                            continue

                        # If we found multiple candidates, ask the user to pick one
                        if len(candidates) > 1:
                            options = ", ".join(candidates[:4])
                            speak_text(f"I found multiple contacts: {options}. Who did you mean?")
                            choice = listen_to_user() or ""
                            # try number selection
                            m = re.search(r"\d+", choice)
                            picked = None
                            if m:
                                n = int(m.group(0))
                                if 1 <= n <= len(candidates):
                                    picked = candidates[n - 1]
                            else:
                                # try text contains
                                for c in candidates:
                                    if c.lower() in choice.lower():
                                        picked = c
                                        break
                            if picked:
                                expanded_actions[idx] = ActionStep(step.intent, {**(step.args or {}), key: picked})
                            else:
                                contact_resolution_block = "Okay, cancelled."
                                expanded_actions.clear()
                                break
                        else:
                            # No matches at all. Don't send to arbitrary strings like "Spotlight".
                            # Ask the user to clarify a real contact (or a phone/email).
                            contact_resolution_block = f"I couldn't find a contact named {raw_target}."
                            expanded_actions = []
                            break

                if contact_resolution_block:
                    tool_bundle["actions"].append({"intent": "BLOCKED", "ok": False, "message": contact_resolution_block})
                
                # CHECK FOR EXIT / SLEE P COMMANDS
                should_sleep = False
                for step in expanded_actions:
                    if step.intent == Intent.EXIT:
                        print("Nexus: Going to sleep.")
                        speak_text("Going to sleep.")
                        should_sleep = True
                        break
                
                if should_sleep:
                    conversation_active = False
                    break 

                if expanded_actions:
                    cmd_obj = Command(raw=raw, plan="(turn_plan)", steps=expanded_actions)
                    safety = check_command(cmd_obj)

                    # DEFAULT: Don't run unless we prove it's safe
                    should_run = False

                    # CASE 1: BLOCKED
                    if not safety.allowed:
                        msg = safety.prompt or "I cannot do that."
                        print(f"🛑 {msg}")
                        speak_text(msg)
                        tool_bundle["actions"].append({"intent": "BLOCKED", "ok": False, "message": msg})
                    
                    # CASE 2: REQUIRES CONFIRMATION
                    elif safety.requires_confirmation:
                        warning_msg = safety.prompt or "This requires confirmation."
                        speak_text(warning_msg)
                        print(f"⚠️ {warning_msg} Waiting for voice confirmation...")

                        # Listen for your answer
                        confirmation = listen_to_user()

                        if is_confirmation_positive(confirmation):
                            should_run = True
                            speak_text("Confirmed.")
                        else:
                            print(f"Aborted. (User said: {confirmation})")
                            speak_text("Okay, cancelled.")
                    
                    # CASE 3: SAFE (THE MISSING PIECE!)
                    else:
                        should_run = True

                    # EXECUTE IF GREEN LIGHT
                    if should_run:
                        for step in expanded_actions:
                            # Handle Nexus control separately (they don't return)
                            if step.intent == Intent.STOP_NEXUS:
                                stop_nexus_program()
                            elif step.intent == Intent.RESTART_NEXUS:
                                restart_nexus_program()
                            else:
                                # All other actions go through the router
                                result = router.dispatch_step(step)
                                tool_bundle["actions"].append({
                                    "intent": step.intent.value,
                                    "ok": result.ok,
                                    "message": result.message,
                                })

                # -------------------------------------------------
                # NARRATE & SPEAK
                # -------------------------------------------------
                reply = narrate_turn(raw, tool_bundle)
                print(f"Nexus: {reply}")
                speak_text(reply) 

                await mcp.call("pg_append_event", {
                    "kind": "assistant_reply",
                    "payload": {"text": redact(reply), "tools": tool_bundle},
                    "session_id": session_id,
                    "tags": ["assistant"],
                })
                
                chat_history.append(f"User: {raw}")
                chat_history.append(f"Nexus: {reply}")

    finally:
        await mcp.stop()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass