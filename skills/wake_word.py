import speech_recognition as sr
import os

def wait_for_wake_word():
    """
    Listens for 'Jarvis'.
    Returns True if heard, False if silence/noise.
    This function BLOCKS until sound is heard, saving CPU.
    """
    r = sr.Recognizer()
    r.dynamic_energy_threshold = True
    
    # 0 = MacBook Mic, 2 = External Mic (Change if needed)
    MIC_INDEX = 0 
    
    with sr.Microphone(device_index=MIC_INDEX) as source:
        print("\n[GUARD] Listening for 'Jarvis'...")
        
        # Adjust only slightly to keep loop fast
        r.adjust_for_ambient_noise(source, duration=0.5)

        try:
            # timeout=None means it waits FOREVER until it hears sound.
            # phrase_time_limit=2 means it only listens to short bursts (like "Jarvis")
            audio = r.listen(source, timeout=None, phrase_time_limit=2)
            
            text = r.recognize_google(audio).lower()
            
            if "jarvis" in text:
                print("✅ WAKE WORD DETECTED")
                # The 'Ding' sound
                os.system('afplay /System/Library/Sounds/Hero.aiff')
                return True
                
        except sr.WaitTimeoutError:
            pass # Just loop again
        except sr.UnknownValueError:
            pass # Heard noise, ignore
        except sr.RequestError:
            print("[GUARD] Internet connection failed.")
            
    return False