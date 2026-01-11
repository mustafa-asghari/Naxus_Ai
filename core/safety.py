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
    """Result of a safety check on an action."""
    allowed: bool
    reason: str
    requires_confirmation: bool
    prompt: Optional[str] = None
    
    @classmethod
    def allow(cls, reason: str = "ok") -> "SafetyDecision":
        """Create a decision that allows the action without confirmation."""
        return cls(allowed=True, reason=reason, requires_confirmation=False)
    
    @classmethod
    def allow_with_confirmation(cls, prompt: str, reason: str = "ok") -> "SafetyDecision":
        """Create a decision that allows the action but requires confirmation."""
        return cls(allowed=True, reason=reason, requires_confirmation=True, prompt=prompt)
    
    @classmethod
    def block(cls, reason: str, message: str) -> "SafetyDecision":
        """Create a decision that blocks the action."""
        return cls(allowed=False, reason=reason, requires_confirmation=False, prompt=message)


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

    if intent == Intent.CONTACTS:
        # Safe: read-only
        return SafetyDecision(True, "ok", False)

    if intent == Intent.CALENDAR:
        # Create/open/search calendar can be sensitive; require confirmation for create
        op = str(args.get("operation", "") or "").lower()
        if op == "create":
            title = str(args.get("title", "") or "").strip()
            date = str(args.get("date", "") or args.get("startDate", "") or "").strip()
            time = str(args.get("time", "") or args.get("startTime", "") or "").strip()
            
            # Build descriptive confirmation
            desc_parts = []
            if title:
                desc_parts.append(f'"{title}"')
            if date:
                desc_parts.append(f"on {date}")
            if time:
                desc_parts.append(f"at {time}")
            
            if desc_parts:
                desc = " ".join(desc_parts)
            else:
                desc = "a new event"
            
            return SafetyDecision(True, "ok", True, f"I'll create {desc}. Confirm?")
        return SafetyDecision(True, "ok", False)

    if intent == Intent.REMINDERS:
        op = str(args.get("operation", "") or "").lower()
        if op == "create":
            name = str(args.get("name", "") or "").strip() or "a reminder"
            return SafetyDecision(True, "ok", True, f"I'll create a reminder ({name}). Confirm?")
        return SafetyDecision(True, "ok", False)

    if intent == Intent.MAPS:
        # Maps is safe (search/directions/pin/save)
        return SafetyDecision(True, "ok", False)

    if intent == Intent.MAIL:
        op = str(args.get("operation", "") or "").lower()
        if op == "send":
            to = str(args.get("to", "") or "").strip()
            subject = str(args.get("subject", "") or "").strip()
            return SafetyDecision(True, "ok", True, f"I'll send an email to {to} ({subject}). Confirm?")
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
    # MEMORY CRUD (Safe - user explicitly controls their own memory)
    # ═══════════════════════════════════════════════════════════════════════

    if intent == Intent.REMEMBER_THIS:
        return SafetyDecision(True, "ok", False)  # Safe to store

    if intent == Intent.RECALL_MEMORY:
        return SafetyDecision(True, "ok", False)  # Safe to search

    if intent == Intent.UPDATE_MEMORY:
        return SafetyDecision(True, "ok", False)  # Safe to update

    if intent == Intent.FORGET_THIS:
        query = (args.get("query", "") or "").strip()
        return SafetyDecision(True, "ok", False)  # User controls their own memory

    if intent == Intent.LIST_MEMORIES:
        return SafetyDecision(True, "ok", False)  # Safe to list

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
    close_targets: list[str] = []
    other_confirmations: list[str] = []

    for step in cmd.steps:
        d = check_step(step)
        if not d.allowed:
            return d
        
        if d.requires_confirmation:
            requires_confirmation = True
            
            # Collect close targets for combined prompt
            if step.intent == Intent.CLOSE_APP:
                app = (step.args or {}).get("app_name", "")
                if app:
                    close_targets.append(app)
            elif d.prompt:
                other_confirmations.append(d.prompt)

    # Build combined prompt
    if close_targets:
        if len(close_targets) == 1:
            close_msg = f"I'll close {close_targets[0]}. Confirm?"
        elif len(close_targets) == 2:
            close_msg = f"I'll close {close_targets[0]} and {close_targets[1]}. Confirm?"
        else:
            all_but_last = ", ".join(close_targets[:-1])
            close_msg = f"I'll close {all_but_last}, and {close_targets[-1]}. Confirm?"
        
        if other_confirmations:
            custom_prompt = close_msg + " Also: " + " ".join(other_confirmations)
        else:
            custom_prompt = close_msg
    elif other_confirmations:
        custom_prompt = other_confirmations[0]

    return SafetyDecision(True, "ok", requires_confirmation, custom_prompt)


