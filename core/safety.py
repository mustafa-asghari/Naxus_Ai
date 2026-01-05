from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from core.models import ActionStep, Command
from core.intent import Intent


# Apps/processes you should never try to quit
PROTECTED_APPS = {
    "System",
    "System Settings",
    "SystemUIServer",
    "WindowServer",
    "ControlCenter",
    "NotificationCenter",
    "Finder",
    "Dock",
    "loginwindow",
    "Terminal",
    "iTerm2",
    "Nexus",
}


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
        # Opening protected apps is fine, but "System" is ambiguous — block it to avoid nonsense
        if app.strip() in {"System"}:
            return SafetyDecision(False, "protected app", False, "Blocked: invalid/unsafe app name.")
        return SafetyDecision(True, "ok", False)

    # CLOSE_APP validation (graceful quit)
    if step.intent == Intent.CLOSE_APP:
        app = step.args.get("app_name")
        if not isinstance(app, str) or not app.strip():
            return SafetyDecision(False, "missing app_name", False, "Blocked: CLOSE_APP requires 'app_name'.")
        if app.strip() in PROTECTED_APPS:
            return SafetyDecision(False, "protected app", False, f"Blocked: refusing to close protected app: {app}.")
        return SafetyDecision(True, "ok", True)

    # CLOSE_ALL_APPS validation (deterministic expansion later)
    if step.intent == Intent.CLOSE_ALL_APPS:
        if step.args:
            return SafetyDecision(False, "unexpected args", False, "Blocked: CLOSE_ALL_APPS takes no args.")
        return SafetyDecision(True, "ok", True)

    # Default: block anything not handled
    return SafetyDecision(False, "no policy", False, f"Blocked: no safety policy for {step.intent.value}.")


def check_command(cmd: Command) -> SafetyDecision:
    # If there are no steps, it's just chat/memory -> ALWAYS ALLOWED
  def check_command(cmd: Command) -> SafetyDecision:
    # If there are no steps, it's just chat/memory -> ALWAYS ALLOWED
    if not cmd.steps:
        return SafetyDecision(True, "chat", False, None)

    # Determine if any step is risky...
    risky = any(step.intent in {Intent.CLOSE_APP, Intent.CLOSE_ALL_APPS} for step in cmd.steps)

    # Validate each step
    for step in cmd.steps:
        d = check_step(step)
        if not d.allowed:
            return d

    return SafetyDecision(True, "ok", risky, None)


