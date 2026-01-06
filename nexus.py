from __future__ import annotations

import sys
import asyncio
import logging
import os
from skills.wake_word import wait_for_wake_word
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

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
    # Add this to silence the API request logs
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

async def main() -> int:
    load_dotenv()
    log = configure_logging()

    base_dir = Path(__file__).resolve().parent
    server_path = base_dir / "data" / "MCP" / "mcp_server.py"

    # MCP client initialization
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
    
    # Initialize short-term memory list
    chat_history = []

    try:
        while True:
            # 1. WAKE WORD (Blocks here until you say "Hey Nexus")
            # If this fails/returns False, we loop to try again instead of crashing
            if not wait_for_wake_word():
                continue 

            # 2. LISTENING (The "Big Brain")
            # Play a sound here if your wake_word.py doesn't do it
            raw = listen_to_user() 
            if not raw:
                continue
            # --- CLEANUP 2: No Manual Exit Logic Here ---
            # We removed the "if raw in EXIT_WORDS" check.
            # We now trust the Planner (Step 2) to tell us when to quit.

            # -------------------------------------------------
            # 1) LOG USER EVENT
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
            # 2) PLAN TURN (With Memory)
            # -------------------------------------------------
            app_list = get_running_apps()
            
            # Convert the last 4 messages into a text block for context
            history_text = "\n".join(chat_history[-4:]) 
            
            # Pass history to the planner so it understands "them", "it", etc.
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
            # 3) MEMORY READ
            # -------------------------------------------------
            if plan.memory_read:
                res = await mcp.call("ch_search_notes_text", {
                    "query": plan.memory_read.query,
                    "limit": plan.memory_read.limit,
                })
                tool_bundle["memory_read"] = res
                
            # -------------------------------------------------
            # 4) MEMORY WRITE
            # -------------------------------------------------
            if plan.memory_write and plan.memory_write.should_store:
                conf = float(plan.memory_write.confidence or 0.0)
                note = plan.memory_write.note or {}
                
                allowed = False
                if conf >= WRITE_CONFIDENCE_AUTO:
                    allowed = True
                elif conf >= WRITE_CONFIDENCE_ASK:
                    allowed = ask_yes_no("Save this to memory? ")

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
            # 5) ACTIONS (EXECUTION)
            # -------------------------------------------------
            expanded_actions = expand_steps(plan.actions)
            
            # --- SMART EXIT CHECK ---
            # This is where the AI actually tells us to quit
            for step in expanded_actions:
                if step.intent == Intent.EXIT:
                    print("Nexus: Goodbye!")
                    speak_text("Goodbye, sir.")
                    return 0
            # ------------------------

            if expanded_actions:
                cmd_obj = Command(raw=raw, plan="(turn_plan)", steps=expanded_actions)
                safety = check_command(cmd_obj)
                
                should_run = False
                if not safety.allowed:
                    msg = safety.prompt or "I cannot do that."
                    print(f"🛑 {msg}")
                    speak_text(msg)
                    tool_bundle["actions"].append({"intent": "BLOCKED", "ok": False, "message": msg})
                
                elif safety.requires_confirmation:
                    warning_msg = safety.prompt or "This action requires confirmation."
                    speak_text(warning_msg)
                    
                    if ask_yes_no(f"\n⚠️ {warning_msg} Proceed? (yes/no) "):
                        should_run = True
                    else:
                        print("Aborted.")
                        speak_text("Okay, I won't do it.")
                else:
                    should_run = True

                if should_run:
                    for step in expanded_actions:
                        result = router.dispatch_step(step)
                        tool_bundle["actions"].append({
                            "intent": step.intent.value,
                            "ok": result.ok,
                            "message": result.message,
                        })

            # -------------------------------------------------
            # 6) NARRATE & SPEAK
            # -------------------------------------------------
            reply = narrate_turn(raw, tool_bundle)
            print(f"Nexus: {reply}")
            speak_text(reply) 

            # Log reply
            await mcp.call("pg_append_event", {
                "kind": "assistant_reply",
                "payload": {"text": redact(reply), "tools": tool_bundle},
                "session_id": session_id,
                "tags": ["assistant"],
            })
            
            # Update Short-Term Memory
            chat_history.append(f"User: {raw}")
            chat_history.append(f"Nexus: {reply}")

    finally:
        await mcp.stop()
if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
