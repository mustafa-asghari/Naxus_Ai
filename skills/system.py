import subprocess

from core.models import ActionStep, Result
from core.intent import Intent


def open_app(step: ActionStep) -> Result:
    app = step.args.get("app_name")
    if not app:
        return Result(False, "Missing app_name")

    argv = ["open", "-a", app]
    r = subprocess.run(argv, capture_output=True, text=True)

    return Result(
        ok=r.returncode == 0,
        message=f"Opened {app}" if r.returncode == 0 else f"Failed to open {app}",
    )


def close_app(step: ActionStep) -> Result:
    app = step.args.get("app_name")
    if not app:
        return Result(False, "Missing app_name")

    script = f'tell application "{app}" to quit'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    return Result(
        ok=r.returncode == 0,
        message=f"Closed {app}" if r.returncode == 0 else f"Failed to close {app}",
    )
