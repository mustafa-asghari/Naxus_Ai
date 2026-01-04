# skills/system.py
from __future__ import annotations
import subprocess
from core.models import Result, ActionStep

def _run(cmd: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, check=False, capture_output=True, text=True)
        ok = (p.returncode == 0)
        out = (p.stdout or "").strip()
        err = (p.stderr or "").strip()
        msg = f"rc={p.returncode}"
        if out:
            msg += f" stdout={out}"
        if err:
            msg += f" stderr={err}"
        return ok, msg
    except Exception as e:
        return False, f"exception={e!r}"

def open_app(step: ActionStep) -> Result:
    app = (step.args or {}).get("app_name")
    if not app:
        return Result(ok=False, message="Missing app_name")
    ok, diag = _run(["open", "-a", app])
    if ok:
        return Result(ok=True, message=f"Opened {app}. ({diag})")
    return Result(ok=False, message=f"Failed to open {app}. ({diag})")

def close_app(step: ActionStep) -> Result:
    app = (step.args or {}).get("app_name")
    if not app:
        return Result(ok=False, message="Missing app_name")

    # Safer quit (AppleScript)
    ok, diag = _run(["osascript", "-e", f'tell application "{app}" to quit'])
    if ok:
        return Result(ok=True, message=f"Quit {app}. ({diag})")
    return Result(ok=False, message=f"Failed to quit {app}. ({diag})")
