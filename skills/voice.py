import torch
import numpy as np
import pyaudio
import subprocess


# --- GLOBAL VARIABLES ---
_current_speech_process = None
_vad_model = None
_utils = None

def init_vad():
    """
    Downloads/Loads the Silero VAD model (High accuracy speech detection).
    Run this once at startup.
    """
    global _vad_model, _utils
    if _vad_model is None:
        print("[NEXUS] Loading VAD Neural Network...")
        # Load Silero VAD from TorchHub (downloads automatically)
        _vad_model, _utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            onnx=False,
            trust_repo=True
        )
        print("[NEXUS] VAD Loaded.")

def stop_speaking():
    """Kills the current 'say' process immediately."""
    global _current_speech_process
    if _current_speech_process:
        try:
            _current_speech_process.terminate()
            _current_speech_process.wait(timeout=0.2)
        except Exception:
            pass
        _current_speech_process = None



def speak_text(text: str):
    """
    Speaks text and allows interruption ONLY if the user speaks LOUDLY.
    Uses Volume Gating (via Numpy) + VAD.
    """
    global _current_speech_process, _vad_model, _utils
    
    if _vad_model is None:
        init_vad()

    stop_speaking()
    print(f"[NEXUS] Speaking: {text[:50]}...")

    _current_speech_process = subprocess.Popen(
        ["say", "-v", "Evan", "-r", "190", text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    SAMPLE_RATE = 16000
    FRAME_SIZE = 512 
    
    pa = pyaudio.PyAudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=FRAME_SIZE
    )

    # --- SETTINGS ---
    # Threshold for Volume (Amplitude).
    # 500 = Quiet room / Whisper
    # 1000 = Normal speech
    # 3000 = Loud speech
    VOLUME_THRESHOLD = 1000 
    CONFIDENCE_THRESHOLD = 0.6

    try:
        while _current_speech_process.poll() is None:
            audio_chunk = stream.read(FRAME_SIZE, exception_on_overflow=False)
            
            # Convert to Numpy Array
            audio_int16 = np.frombuffer(audio_chunk, np.int16)

            # 1. CALCULATE VOLUME (RMS) MANUALLY
            # Square the samples, take the mean, then the square root.
            rms_vol = np.sqrt(np.mean(audio_int16**2))

            # 2. VOLUME GATE
            if rms_vol > VOLUME_THRESHOLD:
                
                # Normalize for AI Model
                audio_float32 = audio_int16.astype(np.float32) / 32768.0
                tensor = torch.from_numpy(audio_float32)
                
                # Check VAD
                speech_prob = _vad_model(tensor, SAMPLE_RATE).item()
                
                if speech_prob > CONFIDENCE_THRESHOLD:
                    print(f"\n[INTERRUPT] Loud Speech (Vol: {int(rms_vol)}, Prob: {speech_prob:.2f}). Stopping.")
                    stop_speaking()
                    break

    except Exception as e:
        print(f"VAD Error: {e}")
    finally:
        stream.close()
        pa.terminate()

# --- KEEP YOUR OLD LISTEN FUNCTION ---
import speech_recognition as sr
def listen_to_user():
    """Standard listening logic"""
    r = sr.Recognizer()
    r.pause_threshold = 1.2
    r.dynamic_energy_threshold = True
    stop_speaking() # Ensure silence
    
    with sr.Microphone(device_index=0) as source:
        print("\n[NEXUS] Listening...")
        try:
            audio = r.listen(source, timeout=8)
            print("[NEXUS] Processing...")
            return r.recognize_google(audio)
        except Exception:
            return None