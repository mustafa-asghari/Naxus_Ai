from __future__ import annotations

import logging
import os
from typing import List

from dotenv import load_dotenv

from core.models import ActionStep, Command, Result
from core.intent import Intent, Mode
from core.memory import MemoryStore
from core.planner import parse_command
from core.router import Router
from core.safety import check_command
from skills.chat import handle_chat
from skills.system import close_app, open_app
from macos.running_apps import get_running_apps


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


def _expand_steps(cmd: Command) -> List[ActionStep]:
    """
    Turn a CLOSE_ALL_APPS request into individual CLOSE_APP steps,
    skipping Finder/Terminal/iTerm2/Nexus based on env config.
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
    """
    Build a friendly plan string, including any expanded close-all steps.
    """
    lines = []
    if cmd.plan:
        lines.append(cmd.plan)
    lines.append("")  # blank line
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


def main() -> int:
    load_dotenv()

    log = _configure_logging()
    db_path = os.getenv("NEXUS_DB_PATH", "./data/nexus.db")
    memory = MemoryStore(db_path=db_path)

    router = Router()
    router.register_chat(handle_chat)
    router.register_action(Intent.OPEN_APP, open_app)
    router.register_action(Intent.CLOSE_APP, close_app)

    log.info("Starting Nexus")
    print("Nexus started. Type anything. Use 'quit' to exit.")

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

        cmd = parse_command(text)

        # Chat path
        if cmd.mode == Mode.CHAT:
            res = router.dispatch_chat(cmd)
            print(res.message)
            # Store chat-only interactions (no steps)
            memory.log(cmd, [], [res])
            continue

        # Action path
        safety = check_command(cmd)
        if not safety.allowed:
            print(safety.prompt or "Blocked.")
            memory.log(cmd, cmd.steps, [Result(ok=False, message=safety.prompt or "Blocked")])
            continue

        expanded_steps = _expand_steps(cmd)

        # Show the plan (after expanding close-all) before asking to run it
        print(_format_plan_for_display(cmd, expanded_steps))
        print("\nProceed? (yes/no)")
        confirm = _normalize(input("> ")).lower()
        if confirm != "yes":
            print("Cancelled.")
            memory.log(cmd, expanded_steps, [Result(ok=False, message="Cancelled by user.")])
            continue

        # Run steps one by one; keep going even if one fails
        results: List[Result] = []
        for step in expanded_steps:
            # Re-check each step in case a single step has bad args
            step_check = check_command(Command(raw=cmd.raw, mode=Mode.ACTION, plan=cmd.plan, steps=[step]))
            if not step_check.allowed:
                results.append(Result(ok=False, message=step_check.prompt or "Blocked step."))
                continue

            result = router.dispatch_step(step)
            results.append(result)

        # Quick success/failure recap
        print("\nResult:")
        for r in results:
            prefix = "✅" if r.ok else "❌"
            print(f"{prefix} {r.message}")

        print(_summarize_results(results))

        # Persist what happened
        memory.log(cmd, expanded_steps, results)

    # unreachable
    # return 0


if __name__ == "__main__":
    raise SystemExit(main())
