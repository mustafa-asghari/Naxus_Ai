"""
NexusOrchestrator - The Main Conversation Loop

This module extracts the core conversation orchestration logic from nexus.py
into a clean, testable class. It handles:
- Wake word detection
- Conversation lifecycle
- Action planning and execution
- Response narration

This is the "Application Layer" orchestrator that coordinates all components.
"""
from __future__ import annotations

import os
import re
import sys
import shutil
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from core.models import ActionStep, Command, Result
from core.intent import Intent
from core.safety import check_command, SafetyDecision
from core.planner import plan_turn, TurnPlan
from core.narrator import narrate_turn

from skills.voice import (
    listen_to_user, speak_text, stop_speaking, speak_quick,
    init_voice, is_interrupted, clear_interrupt, check_interrupt_word
)
from skills.wake_word import wait_for_wake_word
from skills.system import open_app, close_app
from skills.web_search import search_web
from skills.browser import open_url
from skills.discord import send_discord_message, read_active_window

from macos.running_apps import get_running_apps, get_frontmost_app

from data.MCP.mcp_client import MCPMemoryClient
from data.MCP.apple_mcp_client import AppleMCPClient


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

WRITE_CONFIDENCE_AUTO = 0.65
WRITE_CONFIDENCE_ASK = 0.60

_SECRET_PATTERNS = [
    r"sk-[A-Za-z0-9]{20,}",
    r"-----BEGIN [A-Z ]+PRIVATE KEY-----",
    r"password\s*[:=]\s*\S+",
]

_TLD_PATTERN = r"(com|net|org|io|ai|app|dev|edu|gov|co|uk|au)"


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def redact(text: str) -> str:
    """Redact sensitive patterns from text."""
    out = text
    for p in _SECRET_PATTERNS:
        out = re.sub(p, "[REDACTED]", out, flags=re.IGNORECASE)
    return out


def detect_url(text: str) -> Optional[str]:
    """
    Best-effort URL detector for voice input like:
      - "open google.com"
      - "go to youtube dot com"
    Returns a normalized URL with https:// prefix if needed.
    """
    if not text:
        return None
    t = text.strip().lower()

    # Convert common spoken patterns
    for tld in ["com", "net", "org", "io", "ai", "app", "dev", "edu", "gov", "co", "uk", "au"]:
        t = re.sub(rf"\bdot\s+{tld}\b", f".{tld}", t)

    t = re.sub(r"\s*\.\s*", ".", t)
    t = re.sub(r"\s*/\s*", "/", t)

    m = re.search(rf"((?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.{_TLD_PATTERN}(?:/[^\s]*)?)", t)
    if not m:
        return None
    url = m.group(1).strip().rstrip(".,)")
    if not url:
        return None
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def detect_close_targets(raw: str, running_apps: list[str]) -> list[str]:
    """
    If the user asks to close apps, match mentioned app names
    against the current running apps list. Supports multiple apps.
    """
    if not raw:
        return []
    t = raw.lower()
    if "close" not in t and "quit" not in t and "exit" not in t:
        return []
    if "close all" in t or "quit everything" in t or "close everything" in t:
        return []

    matches: list[tuple[int, str]] = []
    for app in running_apps:
        al = app.lower()
        aliases = {al}
        
        # Common app aliases
        if "google chrome" in al:
            aliases.add("chrome")
        if "visual studio code" in al:
            aliases.update(["vscode", "code", "vs code"])
        if "messages" == al:
            aliases.add("message")
        if "system settings" == al:
            aliases.add("settings")
        if "notes" == al:
            aliases.update(["notes", "note"])
        if "safari" in al:
            aliases.add("safari")
        if "finder" in al:
            aliases.add("finder")
        if "discord" in al:
            aliases.add("discord")
        if "terminal" in al:
            aliases.add("terminal")
        if "music" in al or "itunes" in al:
            aliases.update(["music", "itunes"])
        if "spotify" in al:
            aliases.add("spotify")
        if "slack" in al:
            aliases.add("slack")
        if "zoom" in al:
            aliases.add("zoom")
        if "teams" in al or "microsoft teams" in al:
            aliases.add("teams")
        if "word" in al or "microsoft word" in al:
            aliases.add("word")
        if "excel" in al or "microsoft excel" in al:
            aliases.add("excel")
        if "preview" in al:
            aliases.add("preview")
        if "calendar" in al:
            aliases.add("calendar")
        if "mail" in al:
            aliases.add("mail")
        if "photos" in al:
            aliases.add("photos")
        if "reminders" in al:
            aliases.add("reminders")
        if "xcode" in al:
            aliases.add("xcode")
        if "cursor" in al:
            aliases.add("cursor")

        for a in aliases:
            if not a or len(a) < 3:
                continue
            idx = t.find(a)
            if idx != -1:
                matches.append((idx, app))
                break

    matches.sort(key=lambda x: x[0])
    out: list[str] = []
    seen = set()
    for _i, app in matches:
        if app not in seen:
            seen.add(app)
            out.append(app)
    return out


def looks_like_phone_or_email(s: str) -> bool:
    """Check if string looks like a phone number or email."""
    ss = (s or "").strip()
    if not ss:
        return False
    return bool(re.fullmatch(r"[+\d][\d\s().-]{6,}", ss) or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", ss))


def parse_contacts_tool_text(text: str) -> tuple[str, list[str]]:
    """Parse apple-mcp contacts tool response."""
    t = (text or "").strip()
    if not t:
        return "", []
    if ":" not in t:
        return t.strip(), []
    name, rest = t.split(":", 1)
    handles = [h.strip() for h in rest.split(",") if h.strip()]
    return name.strip(), handles


def is_confirmation_positive(user_text: str) -> bool:
    """Determine if user's response means YES or NO."""
    if not user_text:
        return False

    clean = user_text.lower().strip()
    
    positive_words = {
        "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright",
        "do it", "confirm", "confirmed", "go ahead", "proceed", "affirmative",
        "absolutely", "definitely", "of course", "for sure", "go for it",
        "please", "please do", "yes please", "that's right", "correct",
        "right", "uh huh", "mm hmm", "yea", "ya", "yas", "yah",
        # Fuzzy matches for common misrecognitions
        "confer", "conf", "confir", "confirme", "go for",
        "yep yep", "yes yes", "uh-huh", "mhm"
    }
    
    negative_words = {
        "no", "nah", "nope", "cancel", "stop", "don't", "abort",
        "never", "negative", "no way", "not now", "wait", "hold on",
        "actually no", "no thanks", "nope nope"
    }

    # Direct match
    if clean in positive_words:
        return True
    if clean in negative_words:
        return False

    # Partial match
    for phrase in positive_words:
        if phrase in clean and len(phrase) > 2:
            return True

    for phrase in negative_words:
        if phrase in clean and len(phrase) > 2:
            return False

    # Fuzzy: starts with positive prefix
    if clean.startswith(("ye", "su", "ok", "al", "go", "conf", "uh", "mm")):
        return True

    # Default to no for safety
    return False


def expand_steps(actions: list[ActionStep]) -> list[ActionStep]:
    """Expand meta-actions like CLOSE_ALL_APPS into individual steps."""
    expanded: list[ActionStep] = []
    for step in actions:
        if step.intent == Intent.CLOSE_ALL_APPS:
            apps = get_running_apps()
            print(f"[CLOSE_ALL] Found {len(apps)} apps to close: {apps}")
            if not apps:
                print("[CLOSE_ALL] No apps found to close (get_running_apps returned empty)")
            for app in apps:
                expanded.append(ActionStep(Intent.CLOSE_APP, {"app_name": app}))
        else:
            expanded.append(step)
    return expanded


# ═══════════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR CLASS
# ═══════════════════════════════════════════════════════════════════════════════

class NexusOrchestrator:
    """
    Main conversation orchestrator for Nexus.
    Handles the wake word → conversation → sleep lifecycle.
    """
    
    def __init__(
        self,
        mcp: MCPMemoryClient,
        apple: AppleMCPClient,
        session_id: str = "default",
        log: Optional[logging.Logger] = None
    ):
        self.mcp = mcp
        self.apple = apple
        self.session_id = session_id
        self.log = log or logging.getLogger("nexus")
        self.chat_history: list[str] = []
        
        # Skill dispatch table
        self._skill_handlers = {
            Intent.OPEN_APP: open_app,
            Intent.CLOSE_APP: close_app,
            Intent.SEARCH_WEB: search_web,
            Intent.OPEN_URL: open_url,
            Intent.TYPE_TEXT: send_discord_message,
            Intent.READ_SCREEN: read_active_window,
        }
    
    async def run(self) -> int:
        """Main run loop - handles wake/conversation cycles."""
        print("Nexus started. Listening... (Say 'quit' to exit)")
        
        try:
            while True:
                # Wait for wake word
                if not wait_for_wake_word():
                    continue
                
                # Enter conversation mode
                await self._run_conversation()
        finally:
            pass
        
        return 0
    
    async def _run_conversation(self) -> None:
        """Handle a single conversation session until sleep."""
        print("--- ENTERING CONVERSATION MODE ---")
        
        # Load history from database
        hist_data = await self.mcp.call("pg_get_recent_history", {
            "session_id": self.session_id, 
            "limit": 10
        })
        self.chat_history = hist_data.get("history", [])
        
        # Greeting based on context
        if self.chat_history:
            last_msg = self.chat_history[-1]
            print(f"[RESTORED MEMORY] Context: {last_msg[:50]}...")
            speak_text("I'm back. As we were saying...")
        else:
            speak_text("I'm listening, what's on your mind.")
        
        # Conversation loop
        while True:
            should_continue = await self._handle_turn()
            if not should_continue:
                break
    
    async def _handle_turn(self) -> bool:
        """
        Handle a single conversation turn.
        Returns True to continue, False to exit conversation.
        """
        # Listen for command
        raw = listen_to_user()
        
        # Skip empty/noise input
        if not raw or not raw.strip() or len(raw.strip()) < 4:
            if raw:
                print(f"Ignored noise: '{raw}'")
            return True
        
        # Direct keyword detection for critical commands (bypass planner for reliability)
        raw_lower = raw.lower()
        if any(phrase in raw_lower for phrase in [
            "shut yourself down", "shut down yourself", "terminate yourself",
            "close yourself", "kill yourself", "stop yourself", "quit nexus",
            "exit nexus", "turn yourself off"
        ]):
            print("Nexus: Shutting down.")
            speak_text("Shutting down. Goodbye.")
            import os
            os._exit(0)
        
        if any(phrase in raw_lower for phrase in [
            "restart yourself", "reboot yourself", "reload yourself"
        ]):
            print("Nexus: Restarting.")
            speak_text("Restarting myself now.")
            import os, sys
            os.execv(sys.executable, ['python'] + sys.argv)
        
        # Log user event
        user_event = await self.mcp.call("pg_append_event", {
            "kind": "user_msg",
            "payload": {"text": redact(raw)},
            "session_id": self.session_id,
            "tags": ["user"],
        })
        user_event_id = user_event.get("event_id")
        
        # Plan the turn
        app_list = get_running_apps()
        history_text = "\n".join(self.chat_history[-4:])
        
        plan = plan_turn(
            raw,
            history=history_text,
            context=f"Running Apps: {', '.join(app_list)}"
        )
        
        # Apply deterministic overrides
        plan = self._apply_overrides(plan, raw, app_list)
        
        # Build tool result bundle
        tool_bundle: Dict[str, Any] = {
            "memory_read": None,
            "memory_write": None,
            "actions": [],
        }
        
        # Handle memory operations
        await self._handle_memory_ops(plan, tool_bundle, raw, user_event_id)
        
        # Execute actions
        expanded_actions = expand_steps(plan.actions)
        
        # Check for sleep command
        for step in expanded_actions:
            if step.intent == Intent.EXIT:
                print("Nexus: Going to sleep.")
                speak_text("Going to sleep.")
                return False
        
        # Execute if we have actions
        if expanded_actions:
            await self._execute_actions(expanded_actions, raw, tool_bundle)
        
        # Narrate response
        reply = narrate_turn(raw, tool_bundle)
        print(f"Nexus: {reply}")
        speak_text(reply)
        
        # Log assistant reply
        await self.mcp.call("pg_append_event", {
            "kind": "assistant_reply",
            "payload": {"text": redact(reply), "tools": tool_bundle},
            "session_id": self.session_id,
            "tags": ["assistant"],
        })
        
        # Update history
        self.chat_history.append(f"User: {raw}")
        self.chat_history.append(f"Nexus: {reply}")
        
        return True
    
    def _apply_overrides(self, plan: TurnPlan, raw: str, app_list: list[str]) -> TurnPlan:
        """Apply deterministic overrides for URL detection and close targets."""
        actions = list(plan.actions) if plan.actions else []
        
        # URL override
        detected_url = detect_url(raw)
        if detected_url:
            filtered: list[ActionStep] = []
            has_open_url = False

            for st in actions:
                if st.intent == Intent.OPEN_URL:
                    has_open_url = True
                    url_arg = str((st.args or {}).get("url") or "").strip()
                    if not url_arg:
                        filtered.append(ActionStep(Intent.OPEN_URL, {"url": detected_url}))
                    else:
                        filtered.append(st)
                    continue

                if st.intent == Intent.OPEN_APP:
                    app_name = str((st.args or {}).get("app_name") or "")
                    if "." in app_name.lower():
                        continue

                filtered.append(st)

            if not has_open_url:
                filtered.append(ActionStep(Intent.OPEN_URL, {"url": detected_url}))

            actions = filtered

        # Close targets override
        close_targets = detect_close_targets(raw, app_list)
        if close_targets:
            keep: list[ActionStep] = [st for st in actions if st.intent != Intent.CLOSE_APP]
            close_steps = [ActionStep(Intent.CLOSE_APP, {"app_name": a}) for a in close_targets]
            actions = close_steps + keep
        else:
            t = (raw or "").lower()
            if any(k in t for k in ["close this", "close it", "quit this", "quit it"]):
                if not any(st.intent in {Intent.CLOSE_APP, Intent.CLOSE_ALL_APPS} for st in actions):
                    front = get_frontmost_app()
                    if front:
                        actions = [ActionStep(Intent.CLOSE_APP, {"app_name": front})] + actions
        
        return TurnPlan(
            memory_read=plan.memory_read,
            memory_write=plan.memory_write,
            actions=actions
        )
    
    async def _handle_memory_ops(
        self, 
        plan: TurnPlan, 
        tool_bundle: Dict[str, Any],
        raw: str,
        user_event_id: Optional[str]
    ) -> None:
        """Handle memory read/write operations."""
        if plan.memory_read:
            res = await self.mcp.call("ch_search_notes_text", {
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
                speak_text("Should I save that to memory?")
                conf_ans = listen_to_user()
                if conf_ans and "yes" in conf_ans.lower():
                    allowed = True
            
            if allowed:
                res = await self.mcp.call("ch_insert_note", {
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
    
    async def _execute_actions(
        self,
        actions: list[ActionStep],
        raw: str,
        tool_bundle: Dict[str, Any]
    ) -> None:
        """Execute a list of actions with safety checks."""
        cmd_obj = Command(raw=raw, plan="(turn_plan)", steps=actions)
        safety = check_command(cmd_obj)
        
        should_run = False
        
        if not safety.allowed:
            msg = safety.prompt or "I cannot do that."
            print(f"🛑 {msg}")
            speak_text(msg)
            tool_bundle["actions"].append({"intent": "BLOCKED", "ok": False, "message": msg})
            return
        
        if safety.requires_confirmation:
            warning_msg = safety.prompt or "This requires confirmation."
            speak_text(warning_msg)
            print(f"⚠️ {warning_msg} Waiting for voice confirmation...")
            
            confirmation = listen_to_user()
            if is_confirmation_positive(confirmation):
                should_run = True
                speak_text("Confirmed.")
            else:
                print(f"Aborted. (User said: {confirmation})")
                speak_text("Okay, cancelled.")
        else:
            should_run = True
        
        if not should_run:
            return
        
        for step in actions:
            # Check for interrupt before each action
            if is_interrupted():
                speak_quick("Cancelled.")
                tool_bundle["actions"].append({"intent": "CANCELLED", "ok": False, "message": "Interrupted by user"})
                break
            
            await self._execute_step(step, tool_bundle)
    
    async def _execute_step(self, step: ActionStep, tool_bundle: Dict[str, Any]) -> None:
        """Execute a single action step."""
        intent = step.intent
        
        # Nexus control commands - execute immediately
        if intent == Intent.STOP_NEXUS:
            tool_bundle["actions"].append({"intent": intent.value, "ok": True, "message": "Shutting down Nexus."})
            self._stop_nexus()
            return
        elif intent == Intent.RESTART_NEXUS:
            tool_bundle["actions"].append({"intent": intent.value, "ok": True, "message": "Restarting Nexus."})
            self._restart_nexus()
            return
        
        # Apple MCP actions
        elif intent in {Intent.CREATE_NOTE, Intent.SEND_MESSAGE, Intent.READ_MESSAGES,
                       Intent.CONTACTS, Intent.MAIL, Intent.REMINDERS, 
                       Intent.CALENDAR, Intent.MAPS}:
            await self._execute_apple_mcp(step, tool_bundle)
        
        # Local skill actions
        elif intent in self._skill_handlers:
            handler = self._skill_handlers[intent]
            result = handler(step)
            tool_bundle["actions"].append({
                "intent": intent.value,
                "ok": result.ok,
                "message": result.message,
            })
        
        else:
            tool_bundle["actions"].append({
                "intent": intent.value,
                "ok": False,
                "message": f"No handler for {intent.value}"
            })
    
    async def _execute_apple_mcp(self, step: ActionStep, tool_bundle: Dict[str, Any]) -> None:
        """Execute Apple MCP backed action."""
        intent = step.intent
        args = dict(step.args or {})
        
        # Map intents to tool names and prepare args
        if intent == Intent.CREATE_NOTE:
            content = str(args.get("content") or "").strip()
            folder = str(args.get("folder") or "Nexus").strip() or "Nexus"
            if not content:
                tool_bundle["actions"].append({"intent": intent.value, "ok": False, "message": "Missing note content."})
                return
            title = content.splitlines()[0][:60] if content else "Nexus Note"
            res = await self.apple.call("notes", {"operation": "create", "title": title, "body": content, "folderName": folder})
            msg = res.get("text") or f"Created note '{title}'."
            tool_bundle["actions"].append({"intent": intent.value, "ok": not bool(res.get("isError")), "message": msg})
        
        elif intent == Intent.SEND_MESSAGE:
            recipient = str(args.get("recipient") or "").strip()
            message = str(args.get("message") or "").strip()
            if not recipient or not message:
                tool_bundle["actions"].append({"intent": intent.value, "ok": False, "message": "Missing recipient or message."})
                return
            
            resolved_name, resolved_handle, _cands, err = await self._resolve_contact(recipient)
            if err:
                tool_bundle["actions"].append({"intent": intent.value, "ok": False, "message": f"Contacts lookup failed: {err}"})
                return
            
            handle = resolved_handle or (recipient if looks_like_phone_or_email(recipient) else None)
            if not handle:
                tool_bundle["actions"].append({"intent": intent.value, "ok": False, "message": f"Couldn't find a contact named {recipient}."})
                return
            
            res = await self.apple.call("messages", {"operation": "send", "phoneNumber": handle, "message": message})
            msg = res.get("text") or f"Sent message to {resolved_name or recipient}."
            tool_bundle["actions"].append({"intent": intent.value, "ok": not bool(res.get("isError")), "message": msg})
        
        elif intent == Intent.READ_MESSAGES:
            contact = str(args.get("contact") or "").strip()
            if not contact:
                tool_bundle["actions"].append({"intent": intent.value, "ok": False, "message": "Missing contact."})
                return
            
            limit = args.get("limit") or 5
            try:
                limit_i = max(1, min(20, int(limit)))
            except Exception:
                limit_i = 5
            
            resolved_name, resolved_handle, _cands, err = await self._resolve_contact(contact)
            if err:
                tool_bundle["actions"].append({"intent": intent.value, "ok": False, "message": f"Contacts lookup failed: {err}"})
                return
            
            handle = resolved_handle or (contact if looks_like_phone_or_email(contact) else None)
            if not handle:
                tool_bundle["actions"].append({"intent": intent.value, "ok": False, "message": f"Couldn't find a contact named {contact}."})
                return
            
            res = await self.apple.call("messages", {"operation": "read", "phoneNumber": handle, "limit": limit_i})
            msg = res.get("text") or "No messages found."
            tool_bundle["actions"].append({"intent": intent.value, "ok": not bool(res.get("isError")), "message": msg})
        
        elif intent == Intent.CONTACTS:
            name = str(args.get("name") or "").strip()
            res = await self.apple.call("contacts", {"name": name} if name else {})
            msg = res.get("text") or "No contacts found."
            tool_bundle["actions"].append({"intent": intent.value, "ok": not bool(res.get("isError")), "message": msg})
        
        elif intent in {Intent.MAIL, Intent.REMINDERS, Intent.CALENDAR, Intent.MAPS}:
            tool_name = intent.value.lower()
            if "operation" not in args:
                defaults = {"MAIL": "unread", "REMINDERS": "list", "CALENDAR": "list", "MAPS": "search"}
                args["operation"] = defaults.get(intent.value, "list")
            res = await self.apple.call(tool_name, args)
            msg = res.get("text") or f"{tool_name.capitalize()} request completed."
            tool_bundle["actions"].append({"intent": intent.value, "ok": not bool(res.get("isError")), "message": msg})
    
    async def _resolve_contact(self, query: str) -> Tuple[Optional[str], Optional[str], list[str], Optional[str]]:
        """Resolve a contact name to a handle."""
        q = (query or "").strip()
        if not q:
            return None, None, [], None
        if q.lower() in {"me", "myself"}:
            return "me", "me", ["me"], None
        
        if looks_like_phone_or_email(q):
            return q, q, [q], None
        
        try:
            res = await self.apple.call("contacts", {"name": q})
            text = str(res.get("text") or "")
            if not text:
                return None, None, [], "Contacts lookup returned no text."
            name, handles = parse_contacts_tool_text(text)
            if not handles:
                return None, None, [], None
            if len(handles) == 1:
                return name or q, handles[0], [name or q], None
            return None, None, [name or q], None
        except Exception as e:
            return None, None, [], f"{e!r}"
    
    def _stop_nexus(self) -> None:
        """Stop the Nexus program."""
        print("Terminating Nexus Program...")
        speak_text("Shutting down. Goodbye.")
        os._exit(0)
    
    def _restart_nexus(self) -> None:
        """Restart the Nexus program."""
        print("Restarting Nexus Program...")
        speak_text("Restarting myself now.")
        os.execv(sys.executable, ['python'] + sys.argv)
