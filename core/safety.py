from __future__ import annotations

import subprocess

from core.models import ActionStep, Result
from core.intent import Intent
from macos.running_apps import applescript_quote


def open_app(step: ActionStep) -> Result:
    app = step.args.get("app_name")
    if not isinstance(app, str) or not app.strip():
        return Result(ok=False, message="OPEN_APP missing 'app_name'.")

    argv = ["open", "-a", app.strip()]

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:
        return Result(ok=False, message=f"Error opening '{app}': {e}", data={"argv": argv})

    if completed.returncode == 0:
        return Result(ok=True, message=f"Opened '{app}'.", data={"argv": argv})

    return Result(
        ok=False,
        message=f"Failed to open '{app}'.",
        data={"argv": argv, "returncode": completed.returncode, "stderr": (completed.stderr or "").strip()},
    )


def close_app(step: ActionStep) -> Result:
    app = step.args.get("app_name")
    if not isinstance(app, str) or not app.strip():
        return Result(ok=False, message="CLOSE_APP missing 'app_name'.")

    safe_name = applescript_quote(app.strip())
    script = f'tell application "{safe_name}" to quit'
    argv = ["osascript", "-e", script]

    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except Exception as e:
        return Result(ok=False, message=f"Error quitting '{app}': {e}", data={"argv": argv})

    if completed.returncode == 0:
        return Result(ok=True, message=f"Quit '{app}'.", data={"argv": argv})

    return Result(
        ok=False,
        message=f"Failed to quit '{app}'.",
        data={"argv": argv, "returncode": completed.returncode, "stderr": (completed.stderr or "").strip()},
    )
