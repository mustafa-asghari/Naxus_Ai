import re
import subprocess
from typing import List

from skills.system import applescript_quote
from core.models import ActionStep, Result


def _run_applescript(script: str) -> tuple[bool, str]:
    try:
        p = subprocess.run(["osascript", "-e", script], check=False, text=True, capture_output=True, timeout=8)
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()
            return False, err or f"osascript rc={p.returncode}"
        return True, (p.stdout or "").strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, repr(e)


def search_contacts(query: str, limit: int = 5) -> tuple[list[str], str | None]:
    """
    Returns up to `limit` contact display names that contain `query` (case-insensitive).
    Uses the macOS Contacts app via AppleScript. Requires Automation permission.
    """
    q = (query or "").strip()
    if not q:
        return [], None

    safe_q = applescript_quote(q)
    limit = max(1, min(10, int(limit)))

    # AppleScript returns a list like: {"John Doe", "Johnny Appleseed"}
    script = f'''
    tell application "Contacts"
        set matches to (people whose name contains "{safe_q}" or first name contains "{safe_q}" or last name contains "{safe_q}" or organization contains "{safe_q}")
        set outNames to {{}}
        repeat with p in matches
            set end of outNames to name of p
            if (count of outNames) ≥ {limit} then exit repeat
        end repeat
        return outNames
    end tell
    '''

    ok, out = _run_applescript(script)
    if not ok:
        return [], out or "Contacts lookup failed"
    if not out:
        return [], None

    # Parse AppleScript list output.
    # Typical outputs:
    # - John Doe
    # - {"John Doe", "Jane Doe"}
    s = out.strip()
    if s.startswith("{") and s.endswith("}"):
        s = s[1:-1].strip()
        if not s:
            return [], None
        parts = [p.strip().strip('"') for p in s.split(",")]
        return [p for p in parts if p], None
    return [s.strip().strip('"')], None


def _looks_like_phone_number(s: str) -> bool:
    return bool(re.fullmatch(r"[+\d][\d\s().-]{6,}", (s or "").strip()))

def send_imessage(step: ActionStep) -> Result:
    # 1. Get arguments from the planner
    message = (step.args or {}).get("message")
    recipient = (step.args or {}).get("recipient") # Phone number or Contact Name

    if not message or not recipient:
        return Result(ok=False, message="Missing message or recipient.")

    safe_msg = applescript_quote(message)
    safe_recipient = applescript_quote(recipient)

    # 2. The AppleScript Magic
    # This tells the Messages app to find a buddy and send text.
    script = f'''
    tell application "Messages"
        set targetService to 1st service whose service type = iMessage
        set targetBuddy to buddy "{safe_recipient}" of targetService
        send "{safe_msg}" to targetBuddy
    end tell
    '''

    try:
        subprocess.run(["osascript", "-e", script], check=True, text=True)
        return Result(ok=True, message=f"Sent message to {recipient}")
    except subprocess.CalledProcessError:
        return Result(ok=False, message=f"Could not send message. Is '{recipient}' in your contacts?")


def read_messages(step: ActionStep) -> Result:
    """
    Best-effort: reads recent messages from a chat whose name contains the provided contact string.
    This uses AppleScript with the Messages app and may require Automation permission.
    """
    contact = (step.args or {}).get("contact") or (step.args or {}).get("recipient")
    limit = (step.args or {}).get("limit") or 5
    try:
        limit = max(1, min(20, int(limit)))
    except Exception:
        limit = 5

    if not contact or not str(contact).strip():
        return Result(ok=False, message="Missing contact name.")

    safe_contact = applescript_quote(str(contact).strip())

    script = f'''
    tell application "Messages"
        set outText to ""
        set targetName to "{safe_contact}"
        set theChats to text chats
        repeat with c in theChats
            try
                set chatName to name of c
                if chatName contains targetName then
                    set msgs to messages of c
                    set n to count of msgs
                    set startIndex to n - ({limit} - 1)
                    if startIndex < 1 then set startIndex to 1
                    repeat with i from startIndex to n
                        set m to item i of msgs
                        set body to text of m
                        set outText to outText & body & linefeed
                    end repeat
                    exit repeat
                end if
            end try
        end repeat
        return outText
    end tell
    '''

    ok, out = _run_applescript(script)
    if not ok:
        return Result(ok=False, message=f"Couldn't read messages. ({out})")
    if not out.strip():
        return Result(ok=True, message=f"No recent messages found for '{contact}'.")
    return Result(ok=True, message=f"Recent messages for '{contact}':\n{out.strip()}")