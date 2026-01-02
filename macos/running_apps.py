from __future__ import annotations

import os
import subprocess
from typing import List, Set


def _default_exclusions() -> Set[str]:
    raw = os.getenv("NEXUS_EXCLUDE_APPS", "Finder,Terminal,iTerm2,Nexus")
    parts = [p.strip() for p in raw.split(",")]
    return {p for p in parts if p}


def get_running_apps() -> List[str]:
    """
    Ask System Events for the names of foreground apps (not background-only).
    """
    script = (
        'tell application "System Events" to get name of every application process '
        'whose background only is false'
    )

    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    if completed.returncode != 0:
        # If AppleScript fails, don't block anything—just act like nothing is running
        return []

    # Typical shape: "Finder, Safari, Google Chrome"
    raw = (completed.stdout or "").strip()
    if not raw:
        return []

    apps = [a.strip() for a in raw.split(",") if a.strip()]
    exclusions = _default_exclusions()

    # Strip out anything we asked to ignore
    filtered = [a for a in apps if a not in exclusions]
    return filtered


def applescript_quote(s: str) -> str:
    """
    Escape a string so it survives inside AppleScript double quotes.
    """
    return s.replace("\\", "\\\\").replace('"', '\\"')
