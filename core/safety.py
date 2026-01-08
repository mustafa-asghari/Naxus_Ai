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
    """
    Safety gate for all actions. Returns whether an action is allowed,
    and whether it requires user confirmation.
    """
    intent = step.intent
    args = step.args or {}

    # ═══════════════════════════════════════════════════════════════════════
    # BLOCKED INTENTS
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.UNKNOWN:
        return SafetyDecision(False, "unknown intent", False, "I don't understand that command.")

    # ═══════════════════════════════════════════════════════════════════════
    # APP MANAGEMENT
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.OPEN_APP:
        app = args.get("app_name", "")
        if not app or not app.strip():
            return SafetyDecision(False, "missing app_name", False, "Which app should I open?")
        if app.strip() == "System":
            return SafetyDecision(False, "protected", False, "I can't open that system process.")
        return SafetyDecision(True, "ok", False)

    if intent == Intent.CLOSE_APP:
        app = args.get("app_name", "")
        if not app or not app.strip():
            return SafetyDecision(False, "missing app_name", False, "Which app should I close?")
        if app.strip() in PROTECTED_APPS:
            return SafetyDecision(False, "protected", False, f"I can't close {app} - it's a protected system app.")
        return SafetyDecision(True, "ok", True, f"I'll close {app}. Confirm?")

    if intent == Intent.CLOSE_ALL_APPS:
        return SafetyDecision(True, "ok", True, "I'm about to close ALL running applications. Are you sure?")

    # ═══════════════════════════════════════════════════════════════════════
    # WEB & SEARCH (Safe - no confirmation needed)
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.SEARCH_WEB:
        query = args.get("query", "")
        if not query:
            return SafetyDecision(False, "missing query", False, "What should I search for?")
        return SafetyDecision(True, "ok", False)

    if intent == Intent.OPEN_URL:
        url = args.get("url", "")
        if not url:
            return SafetyDecision(False, "missing url", False, "Which URL should I open?")
        return SafetyDecision(True, "ok", False)

    # ═══════════════════════════════════════════════════════════════════════
    # NOTES & MEMORY (Safe - no confirmation needed)
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.CREATE_NOTE:
        content = args.get("content", "")
        if not content:
            return SafetyDecision(False, "missing content", False, "What should the note say?")
        return SafetyDecision(True, "ok", False)

    if intent == Intent.QUERY_ACTIVITY:
        return SafetyDecision(True, "ok", False)

    # ═══════════════════════════════════════════════════════════════════════
    # COMMUNICATION (Requires confirmation - sends messages to people)
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.SEND_MESSAGE:
        recipient = args.get("recipient", "")
        message = args.get("message", "")
        if not recipient:
            return SafetyDecision(False, "missing recipient", False, "Who should I send the message to?")
        if not message:
            return SafetyDecision(False, "missing message", False, "What message should I send?")
        return SafetyDecision(True, "ok", True, f"I'll send '{message}' to {recipient}. Confirm?")

    if intent == Intent.TYPE_TEXT:
        person = args.get("person", "")
        message = args.get("message", "")
        if not message:
            return SafetyDecision(False, "missing message", False, "What should I type?")
        target = person if person else "the active window"
        preview = message[:40] + "..." if len(message) > 40 else message
        return SafetyDecision(True, "ok", True, f"I'll type '{preview}' in {target}. Confirm?")

    if intent == Intent.READ_MESSAGES:
        contact = args.get("contact") or args.get("recipient") or ""
        if not str(contact).strip():
            return SafetyDecision(False, "missing contact", False, "Which contact should I read messages for?")
        return SafetyDecision(True, "ok", False)

    # ═══════════════════════════════════════════════════════════════════════
    # VISION (Safe - just reads screen)
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.READ_SCREEN:
        return SafetyDecision(True, "ok", False)

    # ═══════════════════════════════════════════════════════════════════════
    # NEXUS CONTROL (Requires confirmation for destructive actions)
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.EXIT:
        # Going to sleep - safe, no confirmation needed
        return SafetyDecision(True, "ok", False)

    if intent == Intent.STOP_NEXUS:
        return SafetyDecision(True, "ok", True, "I'm about to shut down completely. Are you sure?")

    if intent == Intent.RESTART_NEXUS:
        return SafetyDecision(True, "ok", True, "I'm about to restart myself. Confirm?")

    # ═══════════════════════════════════════════════════════════════════════
    # DEFAULT: Block unknown intents for safety
    # ═══════════════════════════════════════════════════════════════════════

    return SafetyDecision(False, "no policy", False, f"I don't have a safety policy for {intent.value}. Blocked for safety.")


def check_command(cmd: Command) -> SafetyDecision:  
    if not cmd.steps:
        return SafetyDecision(True, "chat", False, None)

    requires_confirmation = False
    custom_prompt = None

    for step in cmd.steps:
        d = check_step(step)
        if not d.allowed:
            return d
        
        if d.requires_confirmation:
            requires_confirmation = True
            if d.prompt:
                custom_prompt = d.prompt

    return SafetyDecision(True, "ok", requires_confirmation, custom_prompt)


