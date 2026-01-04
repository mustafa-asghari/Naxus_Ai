from __future__ import annotations
import sys
from pathlib import Path
import asyncio
import logging
import os
import re
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

from core.models import ActionStep, Command, Result
from core.intent import Intent, Mode
from core.planner import parse_command, propose_memory_note
from core.router import Router
from core.safety import check_command

from skills.chat import handle_chat
from skills.system import close_app, open_app
from macos.running_apps import get_running_apps

from data.MCP.mcp_client import MCPMemoryClient


# ----------------------------
# Config / Helpers
# ----------------------------

WRITE_CONFIDENCE_AUTO = 0.85
WRITE_CONFIDENCE_ASK = 0.60

_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    r"password\s*[:=]\s*\S+",
]


def _configure_logging() -> logging.Logger:
    app_name = os.getenv("NEXUS_APP_NAME", "nexus")
    level_name = os.getenv("NEXUS_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return logging.getLogger(app_name)


def _normalize(text: str) -> str:
    return text.strip()


def _is_exit(text: str) -> bool:
    return text.lower() in {"quit", "exit", ":q"}


def ask_yes_no(prompt: str) -> bool:
    ans = _normalize(input(prompt)).lower()
    return ans in {"y", "yes"}


def redact(text: str) -> str:
    out = text
    for p in _SECRET_PATTERNS:
        out = re.sub(p, "[REDACTED]", out, flags=re.IGNORECASE)
    return out


def _expand_steps(cmd: Command) -> List[ActionStep]:
    """
    Turn CLOSE_ALL_APPS into multiple CLOSE_APP steps (deterministic),
    using your running apps query (macOS host only).
    """
    expanded: List[ActionStep] = []
    for step in cmd.steps:
        if step.intent == Intent.CLOSE_ALL_APPS:
            running = get_running_apps()
            for app in running:
                expanded.append(ActionStep(intent=Intent.CLOSE_APP, args={"app_name": app}))
        else:
            expanded.append(step)
    return expanded


def _format_plan_for_display(cmd: Command, expanded_steps: List[ActionStep]) -> str:
    lines = []
    if cmd.plan:
        lines.append(cmd.plan)
    lines.append("")
    lines.append("Steps:")
    for i, st in enumerate(expanded_steps, start=1):
        if st.intent in {Intent.OPEN_APP, Intent.CLOSE_APP}:
            app = st.args.get("app_name", "?")
            lines.append(f"{i}. {st.intent.value} → {app}")
        else:
            lines.append(f"{i}. {st.intent.value}")
    return "\n".join(lines)


def _summarize_results(results: List[Result]) -> str:
    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count
    return f"Completed: {ok_count} succeeded, {fail_count} failed."


def _validate_note_proposal(proposal: Dict[str, Any]) -> bool:
    """
    Strict validation so the model can’t break your storage.
    """
    if not isinstance(proposal, dict):
        return False
    if "should_store" not in proposal or not isinstance(proposal["should_store"], bool):
        return False
    if "confidence" in proposal and not isinstance(proposal["confidence"], (int, float)):
        return False

    if proposal.get("should_store"):
        note = proposal.get("note")
        if not isinstance(note, dict):
            return False
        content = note.get("content")
        if not isinstance(content, str) or not content.strip():
            return False
        dl = note.get("deadline")
        if dl is not None and dl != "" and not isinstance(dl, str):
            return False

    return True


# ----------------------------
# MCP Logging Helpers
# ----------------------------

async def log_event(
    mcp: MCPMemoryClient,
    *,
    kind: str,
    payload: Dict[str, Any],
    session_id: str,
    tags: Optional[list[str]] = None,
) -> Optional[str]:
    out = await mcp.call_tool("pg_append_event", {
        "kind": kind,
        "payload": payload,
        "session_id": session_id,
        "tags": tags or [],
    })
    return out.get("event_id")


async def maybe_store_note(
    mcp: MCPMemoryClient,
    *,
    session_id: str,
    user_text: str,
    source_event_id: Optional[str],
) -> Optional[str]:
    """
    OpenAI proposes note; Nexus gates; MCP writes ClickHouse note.
    Returns note_id if stored.
    """
    proposal = propose_memory_note(user_text)
    if not _validate_note_proposal(proposal):
        return None

    if not proposal.get("should_store"):
        return None

    conf = float(proposal.get("confidence") or 0.0)
    note = proposal.get("note") or {}

    allowed = False
    if conf >= WRITE_CONFIDENCE_AUTO:
        allowed = True
    elif conf >= WRITE_CONFIDENCE_ASK:
        allowed = ask_yes_no("This seems important. Save it to memory? (yes/no) ")
    else:
        allowed = False

    if not allowed:
        return None

    out = await mcp.call_tool("ch_insert_note", {
        "title": str(note.get("title") or ""),
        "content": redact(str(note.get("content") or user_text)),
        "deadline": note.get("deadline"),
        "plan": note.get("plan"),
        "status": str(note.get("status") or ""),
        "priority": int(note.get("priority") or 0),
        "tags": note.get("tags") or [],
        "confidence": conf if conf else 0.8,
        "source_event_id": source_event_id,
    })
    return out.get("note_id")


# ----------------------------
# MAIN
# ----------------------------

async def main() -> int:
    load_dotenv()
    log = _configure_logging()
    BASE_DIR = Path(__file__).resolve().parent
    SERVER_PATH = BASE_DIR / "data" / "MCP" / "mcp_server.py"   
    # Start MCP client and init schemas (Python-only)
    mcp = MCPMemoryClient(server_cmd=[sys.executable, str(SERVER_PATH)])
    await mcp.start()
    await mcp.init_schemas()

    router = Router()   
    router.register_chat(handle_chat)
    router.register_action(Intent.OPEN_APP, open_app)
    router.register_action(Intent.CLOSE_APP, close_app) 

    session_id = os.getenv("NEXUS_SESSION_ID", "default")

    log.info("Starting Nexus")
    print("Nexus started. Type anything. Use 'quit' to exit.")

    try:
        while True:
            try:
                raw = input("> ")
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                return 0

            text = _normalize(raw)
            if not text:
                continue
            if _is_exit(text):
                print("Bye.")
                return 0

            # --- 1) Log raw user message to Postgres (always)
            user_event_id = await log_event(
                mcp,
                kind="user_msg",
                payload={"text": redact(text)},
                session_id=session_id,
                tags=["user"],
            )

            # --- 2) Maybe store important memory note to ClickHouse
            await maybe_store_note(
                mcp,
                session_id=session_id,
                user_text=text,
                source_event_id=user_event_id,
            )

            # --- 3) Parse intent/plan (OpenAI planner)
            cmd = parse_command(text)

            # --- Chat path
            if cmd.mode == Mode.CHAT:
                res = router.dispatch_chat(cmd)
                print(res.message)

                # log assistant reply
                await log_event(
                    mcp,
                    kind="assistant_reply",
                    payload={"text": redact(res.message)},
                    session_id=session_id,
                    tags=["chat", "assistant"],
                )
                continue

            # --- Action path (safety gate)
            safety = check_command(cmd)
            if not safety.allowed:
                msg = safety.prompt or "Blocked."
                print(msg)

                await log_event(
                    mcp,
                    kind="blocked",
                    payload={
                        "raw": cmd.raw,
                        "plan": cmd.plan,
                        "steps": [{"intent": s.intent.value, "args": s.args} for s in cmd.steps],
                        "reason": msg,
                    },
                    session_id=session_id,
                    tags=["action", "blocked"],
                )
                continue

            expanded_steps = _expand_steps(cmd)

            print(_format_plan_for_display(cmd, expanded_steps))
            if not ask_yes_no("\nProceed? (yes/no) "):
                print("Cancelled.")

                await log_event(
                    mcp,
                    kind="cancelled",
                    payload={
                        "raw": cmd.raw,
                        "plan": cmd.plan,
                        "steps": [{"intent": s.intent.value, "args": s.args} for s in expanded_steps],
                    },
                    session_id=session_id,
                    tags=["action", "cancelled"],
                )
                continue

            # --- Execute deterministically
            results: List[Result] = []
            for step in expanded_steps:
                step_check = check_command(Command(raw=cmd.raw, mode=Mode.ACTION, plan=cmd.plan, steps=[step]))
                if not step_check.allowed:
                    results.append(Result(ok=False, message=step_check.prompt or "Blocked step."))
                    continue

                r = router.dispatch_step(step)
                results.append(r)

            # Print recap
            print("\nResult:")
            for r in results:
                prefix = "✅" if r.ok else "❌"
                print(f"{prefix} {r.message}")
            print(_summarize_results(results))

            # --- Log execution to Postgres
            await log_event(
                mcp,
                kind="execution",
                payload={
                    "raw": cmd.raw,
                    "plan": cmd.plan,
                    "steps": [{"intent": s.intent.value, "args": s.args} for s in expanded_steps],
                    "results": [{"ok": r.ok, "message": r.message, "data": r.data} for r in results],
                },
                session_id=session_id,
                tags=["action", "execution"],
            )

    finally:
        await mcp.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
