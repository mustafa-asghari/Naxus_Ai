import speech_recognition as sr
import subprocess  


# ... (speak_text function remains the same) ...

def listen_to_user() -> str:
    """
    Listens to the microphone with increased patience.
    """
    recognizer = sr.Recognizer()
    
    # NEW: Make Nexus wait longer before cutting you off
    recognizer.pause_threshold = 1.2  # Wait 1.2 seconds of silence before stopping
    recognizer.energy_threshold = 300 # Helps with background noise sensitivity
    recognizer.dynamic_energy_threshold = True

    with sr.Microphone() as source:
        print("Listening for your command...")
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        
        try:
            # increased phrase_time_limit to allow longer sentences
            audio_data = recognizer.listen(source, timeout=5, phrase_time_limit=15)
            
            print("Processing speech...")
            text = recognizer.recognize_google(audio_data)
            print(f"You said: {text}")
            return text
            
        except sr.UnknownValueError:
            return ""
        except sr.RequestError:
            print("Nexus: Speech service is down.")
            return ""
        except Exception:
            return ""
        

import subprocess

def speak_text(text: str):
    """
    Uses macOS 'say' with a high-quality human-like male voice.
    Requires 'Evan' (Enhanced) to be installed via System Settings.
    """
    try:
        # '-v Evan' selects the high-quality male voice.
        # '-r 175' speeds it up slightly to sound conversational (default is usually too slow).
        # If 'Evan' isn't installed, try 'Nathan' or 'Daniel'.
        subprocess.run(["say", "-v", "Evan", "-r", "195", text], check=False)
    except Exception as e:
        print(f"Error in speaking skill: {e}")