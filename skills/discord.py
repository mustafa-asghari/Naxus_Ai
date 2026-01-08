import subprocess
import time
from skills.system import applescript_quote
import base64
from core.models import ActionStep, Result
from openai import OpenAI

def send_discord_message(step: ActionStep) -> Result:
    person = (step.args or {}).get("person")
    message = (step.args or {}).get("message")

    # 1. Activate Discord
    subprocess.run(["open", "-a", "Discord"])
    time.sleep(1.0) # Wait for window to focus

    # 2. Use AppleScript to simulate keystrokes (The "Robot" hand)
    # Cmd+K opens the "Quick Switcher" in Discord
    setup_script = f'''
    tell application "System Events"
        keystroke "k" using command down
        delay 0.5
        keystroke "{applescript_quote(person)}"
        delay 1.0
        keystroke return
        delay 1.0
        keystroke "{applescript_quote(message)}"
        delay 0.5
        keystroke return
    end tell
    '''
    
    subprocess.run(["osascript", "-e", setup_script])
    return Result(ok=True, message=f"Typed message to {person} in Discord.")

def read_active_window(step: ActionStep) -> Result:
    # 1. Take a screenshot of the open window
    # screencapture -x (no sound) -w (window only) /tmp/nexus_vision.png
    subprocess.run(["screencapture", "-x", "/tmp/nexus_vision.png"])
    
    # 2. Encode image for OpenAI
    with open("/tmp/nexus_vision.png", "rb") as img_file:
        base64_image = base64.b64encode(img_file.read()).decode('utf-8')

    # 3. Ask GPT-4o what it sees
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read the chat history or content in this window. Summarize what is being discussed."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ],
            }
        ],
        max_tokens=300,
    )

    content = response.choices[0].message.content
    return Result(ok=True, message=f"Screen analysis: {content}")