import pvporcupine
import pyaudio
import struct
import os
import sys
from pathlib import Path  # Added for file path handling
from skills.voice import stop_speaking
from dotenv import load_dotenv

load_dotenv()

PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY") 

def wait_for_wake_word():
    """
    Listens for 'Nexus' using your custom .ppn file.
    """
    # 1. Locate the nexus.ppn file dynamically
    base_dir = Path(__file__).resolve().parent
    keyword_path = base_dir / "nexus.ppn"

    try:
        # Check if the file actually exists
        if keyword_path.exists():
            porcupine = pvporcupine.create(
                access_key=PORCUPINE_ACCESS_KEY,
                keyword_paths=[str(keyword_path)] # USE CUSTOM FILE
            )
            print(f"\n[GUARD] Listening for 'Nexus' (Custom Model)...")
        else:
            # Fallback if you haven't downloaded the file yet
            print(f"\n[GUARD] 'nexus.ppn' not found in skills/ folder.")
            print("[GUARD] Falling back to 'Computer' (Built-in).")
            porcupine = pvporcupine.create(
                access_key=PORCUPINE_ACCESS_KEY,
                keywords=["computer"]
            )

    except Exception as e:
        print(f"⚠️ Porcupine Error: {e}")
        input("Press Enter to manually wake...")
        return True

    pa = pyaudio.PyAudio()
    audio_stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length
    )

    try:
        while True:
            pcm = audio_stream.read(porcupine.frame_length, exception_on_overflow=False)
            pcm = struct.unpack_from("h" * porcupine.frame_length, pcm)

            keyword_index = porcupine.process(pcm)

            if keyword_index >= 0:
                print("✅ NEXUS DETECTED")
                stop_speaking()
                os.system('afplay /System/Library/Sounds/Hero.aiff') 
                return True

    except KeyboardInterrupt:
        return False
    finally:
        if audio_stream is not None:
            audio_stream.close()
        if pa is not None:
            pa.terminate()
        porcupine.delete()