from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any, Callable

from core.models import ActionStep, Command
from core.intent import Intent


# Apps/processes you should never try to quit
PROTECTED_APPS = {
    "System", "System Settings", "SystemUIServer", "WindowServer",
    "ControlCenter", "NotificationCenter", "Finder", "Dock",
    "loginwindow", "Terminal", "iTerm2", "Nexus",
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


# ═══════════════════════════════════════════════════════════════════════════════
# HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════

def _handle_unknown(args: Dict[str, Any]) -> SafetyDecision:
    return SafetyDecision.block("unknown intent", "I don't understand that command.")

def _handle_open_app(args: Dict[str, Any]) -> SafetyDecision:
    app = args.get("app_name", "")
    if not app or not app.strip():
        return SafetyDecision.block("missing app_name", "Which app should I open?")
    if app.strip() == "System":
        return SafetyDecision.block("protected", "I can't open that system process.")
    return SafetyDecision.allow()

def _handle_close_app(args: Dict[str, Any]) -> SafetyDecision:
    app = args.get("app_name", "")
    if not app or not app.strip():
        return SafetyDecision.block("missing app_name", "Which app should I close?")
    if app.strip() in PROTECTED_APPS:
        return SafetyDecision.block("protected", f"I can't close {app} - it's a protected system app.")
    return SafetyDecision.allow_with_confirmation(f"I'll close {app}. Confirm?")

def _handle_close_all(args: Dict[str, Any]) -> SafetyDecision:
    return SafetyDecision.allow_with_confirmation("I'm about to close ALL running applications. Are you sure?")

def _handle_search_web(args: Dict[str, Any]) -> SafetyDecision:
    if not args.get("query"):
        return SafetyDecision.block("missing query", "What should I search for?")
    return SafetyDecision.allow()

def _handle_open_url(args: Dict[str, Any]) -> SafetyDecision:
    if not args.get("url"):
        return SafetyDecision.block("missing url", "Which URL should I open?")
    return SafetyDecision.allow()

def _handle_create_note(args: Dict[str, Any]) -> SafetyDecision:
    if not args.get("content"):
        return SafetyDecision.block("missing content", "What should the note say?")
    return SafetyDecision.allow()

def _handle_calendar(args: Dict[str, Any]) -> SafetyDecision:
    op = str(args.get("operation") or "").lower()
    if op == "create":
        title = str(args.get("title") or "").strip()
        date = str(args.get("date") or args.get("startDate") or "").strip()
        time = str(args.get("time") or args.get("startTime") or "").strip()
        
        desc_parts = []
        if title: desc_parts.append(f'"{title}"')
        if date: desc_parts.append(f"on {date}")
        if time: desc_parts.append(f"at {time}")
        
        desc = " ".join(desc_parts) if desc_parts else "a new event"
        return SafetyDecision.allow_with_confirmation(f"I'll create {desc}. Confirm?")
    return SafetyDecision.allow()

def _handle_reminders(args: Dict[str, Any]) -> SafetyDecision:
    op = str(args.get("operation") or "").lower()
    if op == "create":
        name = str(args.get("name") or "").strip() or "a reminder"
        return SafetyDecision.allow_with_confirmation(f"I'll create a reminder ({name}). Confirm?")
    return SafetyDecision.allow()

def _handle_mail(args: Dict[str, Any]) -> SafetyDecision:
    op = str(args.get("operation") or "").lower()
    if op == "send":
        to = str(args.get("to") or "").strip()
        subject = str(args.get("subject") or "").strip()
        return SafetyDecision.allow_with_confirmation(f"I'll send an email to {to} ({subject}). Confirm?")
    return SafetyDecision.allow()

def _handle_send_message(args: Dict[str, Any]) -> SafetyDecision:
    recipient = args.get("recipient", "")
    message = args.get("message", "")
    if not recipient:
        return SafetyDecision.block("missing recipient", "Who should I send the message to?")
    if not message:
        return SafetyDecision.block("missing message", "What message should I send?")
    return SafetyDecision.allow_with_confirmation(f"I'll send '{message}' to {recipient}. Confirm?")

def _handle_type_text(args: Dict[str, Any]) -> SafetyDecision:
    person = args.get("person", "")
    message = args.get("message", "")
    if not message:
        return SafetyDecision.block("missing message", "What should I type?")
    target = person if person else "the active window"
    preview = message[:40] + "..." if len(message) > 40 else message
    return SafetyDecision.allow_with_confirmation(f"I'll type '{preview}' in {target}. Confirm?")

def _handle_read_messages(args: Dict[str, Any]) -> SafetyDecision:
    contact = args.get("contact") or args.get("recipient") or ""
    if not str(contact).strip():
        return SafetyDecision.block("missing contact", "Which contact should I read messages for?")
    return SafetyDecision.allow()

def _handle_safe_pass(args: Dict[str, Any]) -> SafetyDecision:
    return SafetyDecision.allow()

def _handle_stop_nexus(args: Dict[str, Any]) -> SafetyDecision:
    return SafetyDecision.allow_with_confirmation("I'm about to shut down completely. Are you sure?")

def _handle_restart_nexus(args: Dict[str, Any]) -> SafetyDecision:
    return SafetyDecision.allow_with_confirmation("I'm about to restart myself. Confirm?")

# Dispatch Table
_HANDLERS: Dict[Intent, Callable[[Dict[str, Any]], SafetyDecision]] = {
    Intent.UNKNOWN: _handle_unknown,
    Intent.OPEN_APP: _handle_open_app,
    Intent.CLOSE_APP: _handle_close_app,
    Intent.CLOSE_ALL_APPS: _handle_close_all,
    Intent.SEARCH_WEB: _handle_search_web,
    Intent.OPEN_URL: _handle_open_url,
    Intent.CREATE_NOTE: _handle_create_note,
    Intent.CONTACTS: _handle_safe_pass,
    Intent.CALENDAR: _handle_calendar,
    Intent.REMINDERS: _handle_reminders,
    Intent.MAPS: _handle_safe_pass,
    Intent.MAIL: _handle_mail,
    Intent.SEND_MESSAGE: _handle_send_message,
    Intent.TYPE_TEXT: _handle_type_text,
    Intent.READ_MESSAGES: _handle_read_messages,
    Intent.READ_SCREEN: _handle_safe_pass,
    Intent.REMEMBER_THIS: _handle_safe_pass,
    Intent.RECALL_MEMORY: _handle_safe_pass,
    Intent.UPDATE_MEMORY: _handle_safe_pass,
    Intent.FORGET_THIS: _handle_safe_pass,
    Intent.LIST_MEMORIES: _handle_safe_pass,
    Intent.EXIT: _handle_safe_pass,
    Intent.STOP_NEXUS: _handle_stop_nexus,
    Intent.RESTART_NEXUS: _handle_restart_nexus,
}


def check_step(step: ActionStep) -> SafetyDecision:
    """
    Safety gate for all actions. Returns whether an action is allowed,
    and whether it requires user confirmation.
    """
    handler = _HANDLERS.get(step.intent)
    if not handler:
        return SafetyDecision.block("no policy", f"I don't have a safety policy for {step.intent.value}. Blocked for safety.")
    
    return handler(step.args or {})


def check_command(cmd: Command) -> SafetyDecision:  
    if not cmd.steps:
        return SafetyDecision.allow("chat")

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
            if step.intent == Intent.CLOSE_APP:
                app = (step.args or {}).get("app_name", "")
                if app:
                    close_targets.append(app)
            elif d.prompt:
                other_confirmations.append(d.prompt)

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


