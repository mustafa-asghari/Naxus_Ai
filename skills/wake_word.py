# skills/wake_word.py
import pvporcupine
import functools
from pvrecorder import PvRecorder
import os
import sys

# Optional: Sound effect for "Ding"
try:
    import simpleaudio as sa
    HAS_AUDIO = True
except ImportError:
    HAS_AUDIO = False

def play_wake_sound():
    if HAS_AUDIO:
        # You can replace this with a real wav file path later
        # For now, we just print, or you can find a 'ding.wav' on your mac
        print("Ding!") 

def wait_for_wake_word():
    """
    Listens for the wake word (e.g., 'Jarvis' or 'Hey Nexus')
    Blocks until detected.
    """
    
    # 1. Access Key: You need to get this from console.picovoice.ai (It's free)
    # Put it in your .env file as PICOVOICE_KEY
    ACCESS_KEY = os.getenv("PICOVOICE_KEY")
    
    if not ACCESS_KEY:
        print("⚠️  Warning: PICOVOICE_KEY not found in .env.")
        print("   Listening normally (No wake word mode)...")
        return True # Fallback: Just proceed
    
    # 2. Initialize Porcupine
    # keywords=["jarvis"] is built-in. 
    # To use "Hey Nexus", download the file and use: keyword_paths=["path/to/hey_nexus.ppn"]
    try:
        porcupine = pvporcupine.create(
            access_key=ACCESS_KEY,
            keywords=['jarvis'] # Change to 'computer' or your custom file
        )
    except Exception as e:
        print(f"Error initializing Wake Word: {e}")
        return True

    recorder = PvRecorder(device_index=-1, frame_length=porcupine.frame_length)
    recorder.start()
    
    print("Listening for 'Jarvis'...") # Updates this when you switch to Nexus

    try:
        while True:
            # 3. Read a frame of audio
            pcm = recorder.read()
            
            # 4. Check if it matches the wake word
            result = porcupine.process(pcm)
            
            if result >= 0:
                print("Wake word detected!")
                play_wake_sound()
                return True # Breaking the loop allows the "Big Brain" to take over
                
    except KeyboardInterrupt:
        recorder.stop()
        return False
    finally:
        porcupine.delete()
        recorder.delete()