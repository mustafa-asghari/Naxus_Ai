from __future__ import annotations

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
# MCP
from data.MCP.mcp_client import MCPMemoryClient


# -------------------------------------------------
# Config / helpers
# -------------------------------------------------

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


# -------------------------------------------------
# MAIN LOOP
# -------------------------------------------------
def is_confirmation_positive(user_text: str) -> bool:
    """
    Uses AI to decide if the user said 'Yes' or 'No'.
    Handles variations like 'Yeah', 'Sure', 'Go ahead', 'Do it'.
    """
    if not user_text: 
        return False

    print(f"Checking confirmation for: '{user_text}'")

    # 1. Fast check for obvious words to save time
    clean = user_text.lower().strip()
    if clean in ["yes", "yeah", "yep", "sure", "ok", "okay", "do it", "confirm"]:
        return True
    if clean in ["no", "nah", "nope", "cancel", "stop", "don't"]:
        return False

    # 2. Smart check with OpenAI (for complex answers)
    try:
        client = OpenAI()
        resp = client.chat.completions.create(
            model="gpt-4o-mini", # Fast model
            messages=[
                {"role": "system", "content": "Classify the user's response to a confirmation request. Output ONLY 'YES' or 'NO'."},
                {"role": "user", "content": f"User said: '{user_text}'"}
            ],
            temperature=0,
            max_tokens=5
        )
        decision = (resp.choices[0].message.content or "").strip().upper()
        return "YES" in decision
    except Exception as e:
        print(f"AI Check failed: {e}")
        return False
    
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
                speak_text("I'm listening.")

            conversation_active = True
            
            while conversation_active:
                
                # 2. Listen for command (Instant, no wake word needed here)
                raw = listen_to_user() 
                
                # If you stop talking or it hears silence/noise, just listen again
                if not raw:
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
                
                # CHECK FOR EXIT / SLEEP COMMANDS
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