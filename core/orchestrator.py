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
from core.narrator import narrate_turn, narrate_turn_streaming

# Supermemory integration for graph-based AI memory
from data.supermemory_client import (
    add_memory as supermemory_add,
    search_memories as supermemory_search,
    delete_memory as supermemory_delete,
    list_memories as supermemory_list,
)

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

from data.MCP.apple_mcp_client import AppleMCPClient

# Import helpers from dedicated module
from core.helpers import (
    redact, detect_url, detect_close_targets,
    looks_like_phone_or_email, parse_contacts_tool_text,
    is_confirmation_positive, expand_steps,
    WRITE_CONFIDENCE_AUTO, WRITE_CONFIDENCE_ASK
)


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
            os._exit(0)
        
        if any(phrase in raw_lower for phrase in [
            "restart yourself", "reboot yourself", "reload yourself"
        ]):
            print("Nexus: Restarting.")
            speak_text("Restarting myself now.")
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
        
        # Define callback for streaming TTS
        spoke_ref = [False]
        def on_speak_cb(text: str):
            spoke_ref[0] = True
            print(f"Nexus (Stream): {text}")
            # Run TTS in a thread so it doesn't block parsing/actions
            import threading
            threading.Thread(target=speak_text, args=(text,), daemon=True).start()

        plan = plan_turn(
            raw,
            history=history_text,
            context=f"Running Apps: {', '.join(app_list)}",
            on_speak=on_speak_cb
        )
        
        # Apply deterministic overrides
        plan = self._apply_overrides(plan, raw, app_list)
        
        # 1. IMMEDIATE RESPONSE (Single LLM Call Optimization)
        # 1. IMMEDIATE RESPONSE (Single LLM Call Optimization)
        # If the planner generated a response, speak it immediately (if not already streamed).
        initial_reply = ""
        if plan.response_text:
            initial_reply = plan.response_text
            if not spoke_ref[0]:
                print(f"Nexus (Plan): {initial_reply}")
                # Run TTS in a thread so it doesn't block action execution
                import threading
                threading.Thread(target=speak_text, args=(initial_reply,), daemon=True).start()
        
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
                if not initial_reply:
                    speak_text("Going to sleep.")
                return False
        
        # Execute if we have actions
        if expanded_actions:
            await self._execute_actions(expanded_actions, raw, tool_bundle)
            
        # 2. CONDITIONAL FOLLOW-UP NARRATION
        # We only call the Narrator LLM if we retrieved data or encountered errors.
        # Simple actions (Open App, Write Note) are considered complete with the initial response.
        
        needs_narration = False
        
        # Check if any action failed
        if any(not a.get("ok", True) for a in tool_bundle["actions"]):
            needs_narration = True
            
        # Check if we have retrieval results (Search, Read, Recall, etc.)
        retrieval_intents = {
            Intent.SEARCH_WEB, Intent.READ_SCREEN, Intent.READ_MESSAGES,
            Intent.RECALL_MEMORY, Intent.LIST_MEMORIES, Intent.CONTACTS,
            Intent.MAPS  # Maps often returns search results
        }
        # Also check Mail/Calendar/Reminders if they were 'list'/'search' operations
        # (This is harder to check from just intent, so we check if 'data' or 'message' contains results)
        
        for step in expanded_actions:
            if step.intent in retrieval_intents:
                needs_narration = True
                break
            # Heuristic for other apps: if operation was list/search/unread
            if step.intent in {Intent.MAIL, Intent.CALENDAR, Intent.REMINDERS}:
                op = str((step.args or {}).get("operation", "")).lower()
                if op in {"list", "search", "unread", "latest"}:
                    needs_narration = True
                    break

        final_reply = initial_reply
        
        if needs_narration:
            # Check if streaming TTS is enabled
            use_streaming = os.getenv("NEXUS_STREAM_TTS", "false").lower() in ("true", "1", "yes")
            
            print("Nexus: Generating follow-up narration...")
            if use_streaming:
                # Streaming mode: speak sentences as they're generated
                followup_text = ""
                for sentence in narrate_turn_streaming(raw, tool_bundle):
                    followup_text += sentence + " "
                    print(f"Nexus (Narrator): {sentence}")
                    speak_text(sentence, allow_interrupt=True)
                final_reply += " " + followup_text.strip()
            else:
                # Non-streaming mode
                followup = narrate_turn(raw, tool_bundle)
                print(f"Nexus (Narrator): {followup}")
                speak_text(followup)
                final_reply += " " + followup

        # Log assistant reply
        await self.mcp.call("pg_append_event", {
            "kind": "assistant_reply",
            "payload": {"text": redact(final_reply), "tools": tool_bundle},
            "session_id": self.session_id,
            "tags": ["assistant"],
        })
        
        # Update history
        self.chat_history.append(f"User: {raw}")
        self.chat_history.append(f"Nexus: {final_reply}")
        
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
            actions=actions,
            response_text=plan.response_text
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
        
        # Memory CRUD actions (using Supermemory API)
        elif intent == Intent.REMEMBER_THIS:
            content = (step.args or {}).get("content", "")
            title = (step.args or {}).get("title", "")
            tags = (step.args or {}).get("tags", [])
            if content:
                result = supermemory_add(
                    content=content,
                    metadata={"title": title} if title else {},
                    tags=tags if isinstance(tags, list) else [],
                )
                tool_bundle["actions"].append({
                    "intent": intent.value, 
                    "ok": result.get("ok", False),
                    "message": result.get("message", f"Remembered: {content[:50]}...")
                })
            else:
                tool_bundle["actions"].append({
                    "intent": intent.value, "ok": False, "message": "Nothing to remember"
                })
        
        elif intent == Intent.RECALL_MEMORY:
            query = (step.args or {}).get("query", "")
            if query:
                result = supermemory_search(query=query, limit=5)
                items = result.get("results", [])
                if items:
                    # Format memories for response
                    memory_text = "; ".join([i.get("content", "")[:100] for i in items[:3]])
                    tool_bundle["actions"].append({
                        "intent": intent.value, "ok": True,
                        "message": f"Found memories: {memory_text}",
                        "data": items
                    })
                else:
                    tool_bundle["actions"].append({
                        "intent": intent.value, "ok": True, 
                        "message": "No memories found matching that query"
                    })
            else:
                tool_bundle["actions"].append({
                    "intent": intent.value, "ok": False, "message": "No search query provided"
                })
        
        elif intent == Intent.UPDATE_MEMORY:
            query = (step.args or {}).get("query", "")
            new_content = (step.args or {}).get("new_content", "")
            if query and new_content:
                # With Supermemory, we delete old and add new (simpler API)
                search_result = supermemory_search(query=query, limit=1)
                items = search_result.get("results", [])
                if items and items[0].get("id"):
                    # Delete old memory
                    supermemory_delete(items[0]["id"])
                # Add new memory
                result = supermemory_add(content=new_content)
                tool_bundle["actions"].append({
                    "intent": intent.value, "ok": result.get("ok", False),
                    "message": f"Updated memory: {new_content[:50]}..."
                })
            else:
                tool_bundle["actions"].append({
                    "intent": intent.value, "ok": False, "message": "Missing query or new content"
                })
        
        elif intent == Intent.FORGET_THIS:
            query = (step.args or {}).get("query", "")
            if query:
                # Find and delete the memory
                search_result = supermemory_search(query=query, limit=1)
                items = search_result.get("results", [])
                if items and items[0].get("id"):
                    result = supermemory_delete(items[0]["id"])
                    tool_bundle["actions"].append({
                        "intent": intent.value, "ok": result.get("ok", False),
                        "message": f"Deleted memory about: {query}"
                    })
                else:
                    tool_bundle["actions"].append({
                        "intent": intent.value, "ok": False, "message": "No memory found to delete"
                    })
            else:
                tool_bundle["actions"].append({
                    "intent": intent.value, "ok": False, "message": "What should I forget?"
                })
        
        elif intent == Intent.LIST_MEMORIES:
            result = supermemory_list(limit=10)
            docs = result.get("documents", [])
            if docs:
                memory_list = "; ".join([d.get("content", "")[:80] for d in docs[:5]])
                tool_bundle["actions"].append({
                    "intent": intent.value, "ok": True,
                    "message": f"Your memories: {memory_list}",
                    "data": docs
                })
            else:
                tool_bundle["actions"].append({
                    "intent": intent.value, "ok": True, "message": "No memories stored yet"
                })
        
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
