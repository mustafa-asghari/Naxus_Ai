import speech_recognition as sr
import subprocess  


# ... (speak_text function remains the same) ...

def listen_to_user():
    r = sr.Recognizer()
    
    # --- PATIENCE SETTINGS (The Fix) ---
    # 1. How long silence must last to be considered "end of sentence"
    # 0.8 is default. 2.0 allows you to pause and think without being cut off.
    r.pause_threshold = 2.0  
    
    # 2. How much "non-speech" audio to keep
    # This helps catch the soft start/end of words
    r.non_speaking_duration = 0.5 
    
    # CHANGE TO 2 IF USING EXTERNAL MIC
    MIC_INDEX = 0 
    
    with sr.Microphone(device_index=MIC_INDEX) as source:
        print("\n[NEXUS] Listening... (Take your time)")
        try:
            # We REMOVED 'phrase_time_limit'. Now it listens until YOU stop talking.
            # timeout=10 means it waits 10s for you to START talking.
            audio = r.listen(source, timeout=10)
            
            print("[NEXUS] Processing...")
            text = r.recognize_google(audio)
            print(f"[USER] {text}")
            return text
            
        except sr.WaitTimeoutError:
            # You didn't say anything at all
            return None
        except sr.UnknownValueError:
            # You spoke, but it was just noise
            return None
        except Exception as e:
            # Some other error
            return None


def speak_text(text: str):
    """
    Uses macOS 'say' with a high-quality human-like male voice.
    Requires 'Evan' (Enhanced) to be installed via System Settings.
    """
    try:
        # '-v Evan' selects the high-quality male voice.
        # '-r 175' speeds it up slightly to sound conversational (default is usually too slow).
        # If 'Evan' isn't installed, try 'Nathan' or 'Daniel'.
        subprocess.run(["say", "-v", "Evan", "-r", "190", text], check=False)
    except Exception as e:
        print(f"Error in speaking skill: {e}")