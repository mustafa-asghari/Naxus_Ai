# skills/notes.py
from __future__ import annotations
import subprocess
from core.models import ActionStep, Result
from skills.system import applescript_quote  # We reuse this helper!

def create_note(step: ActionStep) -> Result:
    """
    Creates a new note in the Apple 'Notes' app.
    """
    # 1. Extract the content from the Planner's arguments
    content = (step.args or {}).get("content")
    folder = (step.args or {}).get("folder", "Notes") # Default to 'Notes' folder
    
    if not content:
        return Result(ok=False, message="Cannot create a note without content.")

    # 2. Prepare the AppleScript
    # We use 'applescript_quote' to make sure your text doesn't break the script 
    # if it contains quotes or special characters.
    safe_content = applescript_quote(content)
    safe_folder = applescript_quote(folder)

    # This is the "Magic Spell" that controls the app 🪄
    # It tells the 'Notes' application to create a note object.
    script = f'''
    tell application "Notes"
        tell account "iCloud"
            make new note at folder "{safe_folder}" with properties {{body: "{safe_content}"}}
        end tell
    end tell
    '''

    try:
        # 3. Fire the script using Python's subprocess
        # 'osascript -e' runs AppleScript from the command line.
        subprocess.run(["osascript", "-e", script], check=True, timeout=5)
        return Result(ok=True, message="Note created successfully in Notes app.")
        
    except subprocess.CalledProcessError:
        return Result(ok=False, message="Failed to create note. Make sure the 'Notes' app is set up.")
    except Exception as e:
        return Result(ok=False, message=f"Error creating note: {e}")  