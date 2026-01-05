# skills/system.py
from __future__ import annotations

import os
import subprocess
from typing import List, Set, Optional, Tuple
from macos.running_apps import get_running_apps, applescript_quote  

from core.models import Result, ActionStep


# ----------------------------
# Config
# ----------------------------

# Extra guard here too (even if safety.py already blocks)
PROTECTED_APPS: Set[str] = {
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

CMD_TIMEOUT_SEC = 10


# ----------------------------
# Small helpers
# ----------------------------

def _run(cmd: list[str], timeout: int = CMD_TIMEOUT_SEC) -> tuple[bool, str]:
    """
    Run a command and return (ok, diagnostic_string).
    """
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=timeout)
        ok = (p.returncode == 0)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        msg = f"rc={p.returncode}"
        if out:
            msg += f" stdout={out}"
        if err:
            msg += f" stderr={err}"
        return ok, msg
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, f"exception={e!r}"




def _is_running(app_name: str) -> bool:
    """
    Best-effort: check if an app appears in foreground process list.
    """
    return app_name in set(get_running_apps())


def _resolve_app_name(requested: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Deterministic resolver using running apps + /Applications (no new file).
    Returns (resolved_app_name, error_message).
    - If installed exact match exists, returns it.
    - If not, attempts:
      - case-insensitive exact match
      - pluralization ("Note" -> "Notes")
      - contains match among installed apps
      - contains match among running apps (helps with current session)
    """

    req = (requested or "").strip()
    if not req:
        return None, "Missing app_name"

    if req in PROTECTED_APPS:
        return None, f"Blocked: refusing to use protected app name: {req}"

    # Build installed apps list (lightweight scan)
    installed: List[str] = []
    for d in ("/Applications", os.path.expanduser("~/Applications")):
        if not os.path.isdir(d):
            continue
        try:
            for name in os.listdir(d):
                if name.endswith(".app"):
                    installed.append(name[:-4])
        except Exception:
            pass

    # De-dup + sort
    installed = sorted(set(installed), key=str.lower)
    running = get_running_apps()

    # 1) exact
    if req in installed:
        return req, None

    # 2) case-insensitive exact
    low = req.lower()
    for a in installed:
        if a.lower() == low:
            return a, None

    # 3) pluralization: Note -> Notes
    if not low.endswith("s"):
        target = low + "s"
        for a in installed:
            if a.lower() == target:
                return a, None

    # 4) contains match in installed
    contains = [a for a in installed if low in a.lower()]
    if len(contains) == 1:
        return contains[0], None
    if len(contains) > 1:
        return None, f"App '{req}' is ambiguous. Did you mean: {', '.join(contains[:6])}?"

    # 5) fallback: contains match in running apps
    running_contains = [a for a in running if low in a.lower()]
    if len(running_contains) == 1:
        return running_contains[0], None
    if len(running_contains) > 1:
        return None, f"App '{req}' matches multiple running apps: {', '.join(running_contains[:6])}."

    return None, f"App '{req}' not found. Try the full macOS app name (e.g., 'Notes', 'IntelliJ IDEA')."


# ----------------------------
# Skills
# ----------------------------

def open_app(step: ActionStep) -> Result:
    requested = (step.args or {}).get("app_name")
    resolved, err = _resolve_app_name(str(requested or ""))
    if err:
        return Result(ok=False, message=err)

    # Attempt open
    ok, diag = _run(["open", "-a", resolved])
    if not ok:
        return Result(ok=False, message=f"Failed to open {resolved}. ({diag})")

    # Optional verify: if it appears in running apps
    if _is_running(resolved):
        return Result(ok=True, message=f"Opened {resolved}.")
    # Some apps take time; don't mark fail just because it isn't visible yet.
    return Result(ok=True, message=f"Opened {resolved}. (may take a moment to appear)")


def close_app(step: ActionStep) -> Result:
    requested = (step.args or {}).get("app_name")
    resolved, err = _resolve_app_name(str(requested or ""))
    if err:
        return Result(ok=False, message=err)

    # Quit via AppleScript
    script = f'tell application "{applescript_quote(resolved)}" to quit'
    ok, diag = _run(["osascript", "-e", script])
    if not ok:
        return Result(ok=False, message=f"Failed to quit {resolved}. ({diag})")

    # Optional verify
    if _is_running(resolved):
        # App may prompt “Do you want to save?” and still be running
        return Result(ok=False, message=f"Tried to quit {resolved}, but it still appears to be running (maybe waiting for save confirmation).")

    return Result(ok=True, message=f"Quit {resolved}.")
