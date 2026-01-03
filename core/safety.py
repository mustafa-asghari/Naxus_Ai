from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.models import ActionStep, Command
from core.intent import Intent, Mode


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str
    requires_confirmation: bool
    prompt: Optional[str] = None


def check_step(step: ActionStep) -> SafetyDecision:
    # Block unknown
    if step.intent == Intent.UNKNOWN:
        return SafetyDecision(False, "unknown intent", False, "Blocked: unknown action intent.")

    # OPEN_APP validation
    if step.intent == Intent.OPEN_APP:
        app = step.args.get("app_name")
        if not isinstance(app, str) or not app.strip():
            return SafetyDecision(False, "missing app_name", False, "Blocked: OPEN_APP requires 'app_name'.")
        return SafetyDecision(True, "ok", False)

    # CLOSE_APP validation (graceful quit)
    if step.intent == Intent.CLOSE_APP:
        app = step.args.get("app_name")
        if not isinstance(app, str) or not app.strip():
            return SafetyDecision(False, "missing app_name", False, "Blocked: CLOSE_APP requires 'app_name'.")
        return SafetyDecision(True, "ok", True)

    # CLOSE_ALL_APPS validation (deterministic expansion later)
    if step.intent == Intent.CLOSE_ALL_APPS:
        if step.args:
            return SafetyDecision(False, "unexpected args", False, "Blocked: CLOSE_ALL_APPS takes no args.")
        return SafetyDecision(True, "ok", True)

    # Default: block anything not handled
    return SafetyDecision(False, "no policy", False, f"Blocked: no safety policy for {step.intent.value}.")


def check_command(cmd: Command) -> SafetyDecision:
    # CHAT is always allowed
    if cmd.mode == Mode.CHAT:
        return SafetyDecision(True, "chat", False, None)

    # ACTION must have steps
    if cmd.mode != Mode.ACTION:
        return SafetyDecision(False, "invalid mode", False, "Blocked: invalid mode.")

    if not cmd.steps:
        return SafetyDecision(False, "no steps", False, "Blocked: empty action plan.")

    # Determine if any step is risky (still one confirmation in nexus.py)
    risky = any(step.intent in {Intent.CLOSE_APP, Intent.CLOSE_ALL_APPS} for step in cmd.steps)

    # Validate each step
    for step in cmd.steps:
        d = check_step(step)
        if not d.allowed:
            return d

    return SafetyDecision(True, "ok", risky, None)
