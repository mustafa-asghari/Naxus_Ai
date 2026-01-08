import subprocess
from core.models import ActionStep, Result  

def open_url(step: ActionStep) -> Result:
    url = (step.args or {}).get("url")
    if not url:
        return Result(ok=False, message="No URL provided.")

    # logic: The 'open' command in macOS handles default browsers automatically
    try:
        subprocess.run(["open", url], check=True)
        return Result(ok=True, message=f"Opened {url}")
    except Exception as e:
        return Result(ok=False, message=f"Failed to open URL: {e}")