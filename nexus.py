from __future__ import annotations

import sys
import asyncio
import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

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
    ans = input(prompt).strip().lower()
    return ans in {"y", "yes"}


def configure_logging() -> logging.Logger:
    level = getattr(logging, os.getenv("NEXUS_LOG_LEVEL", "INFO").upper())
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
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

    # MCP client
    mcp = MCPMemoryClient(server_cmd=[sys.executable, str(server_path)])
    await mcp.start()
    await mcp.init_schemas()

    router = Router()
    router.register_action(Intent.OPEN_APP, open_app)
    router.register_action(Intent.CLOSE_APP, close_app)

    session_id = os.getenv("NEXUS_SESSION_ID", "default")

    print("Nexus started. Type anything. Use 'quit' to exit.")

    try:
        while True:
            try:
                raw = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                return 0

            if not raw:
                continue
            if raw.lower() in {"quit", "exit", ":q"}:
                print("Bye.")
                return 0

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
            # 2) PLAN TURN (AI reasoning)
            # -------------------------------------------------
            plan = plan_turn(raw)

            tool_bundle: Dict[str, Any] = {
                "memory_read": None,
                "memory_write": None,
                "actions": [],
            }

            # -------------------------------------------------
            # 3) MEMORY READ
            # -------------------------------------------------
            if plan.memory_read:
                res = await mcp.call(
                    "ch_search_notes_text",
                    {
                        "query": plan.memory_read.query,
                        "limit": plan.memory_read.limit,
                    },
                )
                tool_bundle["memory_read"] = res

            # -------------------------------------------------
            # 4) MEMORY WRITE
            # -------------------------------------------------
            # -------------------------------------------------
# 4) MEMORY WRITE (improved: auto-store goals)
# -------------------------------------------------
            if plan.memory_write and plan.memory_write.should_store:
                conf = float(plan.memory_write.confidence or 0.0)
                note = plan.memory_write.note or {}

                # Heuristic: treat these as "always store"
                allowed = False
                # Trust the AI's confidence score
                if conf >= WRITE_CONFIDENCE_AUTO:
                    allowed = True
                elif conf >= WRITE_CONFIDENCE_ASK:
                    allowed = ask_yes_no("This seems important. Save it to memory? (yes/no) ")

                if allowed:
                    res = await mcp.call(
                        "ch_insert_note",
                        {
                            "title": note.get("title", ""),
                            "content": redact(str(note.get("content") or raw)),
                            "deadline": note.get("deadline"),
                            "plan": note.get("plan"),
                            "status": note.get("status", ""),
                            "priority": int(note.get("priority") or 0),
                            "tags": note.get("tags") or [],
                            "confidence": conf if conf else 0.85,
                            "source_event_id": user_event_id,
                        },
                    )
                    tool_bundle["memory_write"] = {"stored": True, **res}
                else:
                    tool_bundle["memory_write"] = {"stored": False, "reason": "gated"}


            # -------------------------------------------------
            # 5) ACTIONS
            # -------------------------------------------------
            expanded_actions = expand_steps(plan.actions)

            if expanded_actions:
                print("\nPlanned actions:")
                for i, a in enumerate(expanded_actions, 1):
                    print(f"{i}. {a.intent.value} {a.args}")

                if ask_yes_no("Proceed? (yes/no) "):
                    for step in expanded_actions:
                        safety = check_command(
                            Command(raw=raw, plan="(turn_plan)", steps=[step])
                            )
                        if not safety.allowed:  
                            tool_bundle["actions"].append(
                                {
                                    "intent": step.intent.value,
                                    "args": step.args,
                                    "ok": False,
                                    "message": safety.prompt,
                                }
                            )
                            continue

                        result = router.dispatch_step(step)
                        tool_bundle["actions"].append(
                            {
                                "intent": step.intent.value,
                                "args": step.args,
                                "ok": result.ok,
                                "message": result.message,
                            }
                        )

            # -------------------------------------------------
            # 6) NARRATE (AI final response)
            # -------------------------------------------------
            reply = narrate_turn(raw, tool_bundle)
            print(reply)

            await mcp.call(
                "pg_append_event",
                {
                    "kind": "assistant_reply",
                    "payload": {"text": redact(reply), "tools": tool_bundle},
                    "session_id": session_id,
                    "tags": ["assistant"],
                },
            )

    finally:
        await mcp.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
