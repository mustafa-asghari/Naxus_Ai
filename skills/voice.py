"""
Voice Module - Local faster-whisper STT + Dynamic Interrupt Detection

Features:
- faster-whisper for fast LOCAL speech-to-text (no network latency, no API cost)
- Uses large-v3 model for best accuracy
- VAD-based speech detection during TTS
- Pre-loaded models for faster response
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from typing import Optional, Tuple

import numpy as np
import pyaudio
import torch
from faster_whisper import WhisperModel
from openai import OpenAI

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

# Whisper model: tiny, base, small, medium, large-v3
# large-v3 is most accurate. On Apple Silicon, use compute_type="int8" for speed
WHISPER_MODEL = os.getenv("NEXUS_WHISPER_MODEL", "large-v3")
WHISPER_DEVICE = os.getenv("NEXUS_WHISPER_DEVICE", "cpu")  # cpu or cuda
WHISPER_COMPUTE = os.getenv("NEXUS_WHISPER_COMPUTE", "int8")  # int8, float16, float32

# Audio settings
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 512

# Volume threshold for interrupt detection
# Higher = less sensitive (won't trigger on speaker output)
VOLUME_THRESHOLD_INTERRUPT = 1500  # Lowered for better interrupt detection
VOLUME_THRESHOLD_SPEECH = 500      # Lower for recording
DEBUG_AUDIO = True  # Set to False to disable audio level debug output

# ═══════════════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ═══════════════════════════════════════════════════════════════════════════════

_current_speech_process: Optional[subprocess.Popen] = None
_vad_model = None
_whisper_model: Optional[WhisperModel] = None
_pa_instance: Optional[pyaudio.PyAudio] = None
_interrupt_flag = threading.Event()
_openai_client: Optional[OpenAI] = None


# ═══════════════════════════════════════════════════════════════════════════════
# INITIALIZATION (Call once at startup)
# ═══════════════════════════════════════════════════════════════════════════════

def init_voice() -> None:
    """
    Initialize all voice components at startup.
    Pre-loads Whisper and VAD models for faster response.
    """
    print("[NEXUS] Initializing voice system...")
    _init_whisper()
    _init_vad()
    _get_pyaudio()
    _get_openai()
    print("[NEXUS] Voice system ready.")


def _init_whisper() -> None:
    """Load faster-whisper model (once)."""
    global _whisper_model
    if _whisper_model is None:
        print(f"[NEXUS] Loading faster-whisper model ({WHISPER_MODEL})...")
        print("[NEXUS] First run will download the model (~1.5GB for large-v3)")
        _whisper_model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE
        )
        print("[NEXUS] Whisper loaded.")


def _init_vad() -> None:
    """Load Silero VAD model (once)."""
    global _vad_model
    if _vad_model is None:
        print("[NEXUS] Loading VAD...")
        _vad_model, _ = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            onnx=False,
            trust_repo=True
        )
        print("[NEXUS] VAD loaded.")


def _get_pyaudio() -> pyaudio.PyAudio:
    """Get or create PyAudio instance (reused for speed)."""
    global _pa_instance
    if _pa_instance is None:
        _pa_instance = pyaudio.PyAudio()
    return _pa_instance


def _get_openai() -> OpenAI:
    """Get OpenAI client for LLM interrupt classification."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


# ═══════════════════════════════════════════════════════════════════════════════
# INTERRUPT SYSTEM - Dynamic LLM-Based Detection
# ═══════════════════════════════════════════════════════════════════════════════

def set_interrupt() -> None:
    """Signal that an interrupt was requested."""
    _interrupt_flag.set()


def clear_interrupt() -> None:
    """Clear the interrupt flag."""
    _interrupt_flag.clear()


def is_interrupted() -> bool:
    """Check if an interrupt was requested."""
    return _interrupt_flag.is_set()


def classify_interrupt(text: str) -> Tuple[bool, str]:
    """
    Fast local classification of interrupt commands.
    Uses keyword matching for speed (no LLM latency).
    
    Returns (is_interrupt: bool, intent: str)
    """
    if not text or len(text.strip()) < 2:
        return False, "none"
    
    lower = text.lower().strip()
    
    # Stop intents
    stop_patterns = [
        "stop", "quit", "abort", "halt", "enough", "shut up", 
        "be quiet", "silence", "end", "terminate", "cease"
    ]
    for p in stop_patterns:
        if p in lower:
            return True, "stop"
    
    # Skip intents
    skip_patterns = ["skip", "next", "move on", "pass", "nevermind", "never mind"]
    for p in skip_patterns:
        if p in lower:
            return True, "skip"
    
    # Cancel intents
    cancel_patterns = ["cancel", "forget it", "don't", "no don't"]
    for p in cancel_patterns:
        if p in lower:
            return True, "cancel"
    
    # Wait intents
    wait_patterns = ["wait", "hold on", "one moment", "pause", "hold"]
    for p in wait_patterns:
        if p in lower:
            return True, "wait"
    
    # Continue intents (not interrupts, but good to know)
    continue_patterns = ["continue", "go on", "keep going", "yes", "okay", "proceed"]
    for p in continue_patterns:
        if p in lower:
            return False, "continue"
    
    return False, "none"


def check_interrupt_word(text: str) -> bool:
    """Quick check for interrupt commands (uses LLM)."""
    is_interrupt, _ = classify_interrupt(text)
    return is_interrupt


# ═══════════════════════════════════════════════════════════════════════════════
# SPEECH OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

def stop_speaking() -> None:
    """Kill the current 'say' process immediately."""
    global _current_speech_process
    if _current_speech_process:
        try:
            _current_speech_process.terminate()
            _current_speech_process.wait(timeout=0.2)
        except Exception:
            pass
        _current_speech_process = None


def speak_text(text: str, allow_interrupt: bool = True) -> bool:
    """
    Speak text with optional voice interrupt detection.
    Returns True if completed, False if interrupted.
    """
    global _current_speech_process, _vad_model
    
    if _vad_model is None:
        _init_vad()

    stop_speaking()
    clear_interrupt()
    
    print(f"[NEXUS] Speaking: {text[:50]}...")

    _current_speech_process = subprocess.Popen(
        ["say", "-v", "Evan", "-r", "230", text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

    if not allow_interrupt:
        _current_speech_process.wait()
        return True

    # Monitor for voice interrupt - lowered threshold for better detection
    CONFIDENCE_THRESHOLD = 0.4  # Was 0.7 - VAD shows lower probs during speech
    
    pa = _get_pyaudio()
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )

    interrupted = False
    try:
        while _current_speech_process.poll() is None:
            if is_interrupted():
                interrupted = True
                break
                
            audio_chunk = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            audio_int16 = np.frombuffer(audio_chunk, np.int16)
            if len(audio_int16) == 0:
                continue
            mean_sq = np.mean(audio_int16.astype(np.float64)**2)
            rms_vol = np.sqrt(max(0, mean_sq))

            # Debug: show audio levels occasionally
            if DEBUG_AUDIO and int(rms_vol) % 100 == 0 and rms_vol > 500:
                print(f"[AUDIO] Vol: {int(rms_vol)} (threshold: {VOLUME_THRESHOLD_INTERRUPT})")
            
            if rms_vol > VOLUME_THRESHOLD_INTERRUPT:
                audio_float32 = audio_int16.astype(np.float32) / 32768.0
                tensor = torch.from_numpy(audio_float32)
                speech_prob = _vad_model(tensor, SAMPLE_RATE).item()
                
                if DEBUG_AUDIO:
                    print(f"[VAD] Vol: {int(rms_vol)}, Speech prob: {speech_prob:.2f}")
                
                if speech_prob > CONFIDENCE_THRESHOLD:
                    print(f"\n[INTERRUPT] Speech detected (Vol: {int(rms_vol)}, Prob: {speech_prob:.2f})")
                    stop_speaking()
                    interrupted = True
                    break

    except Exception as e:
        print(f"[NEXUS] VAD Error: {e}")
    finally:
        stream.close()

    return not interrupted


def speak_quick(text: str) -> None:
    """Quick speak without interrupt monitoring (for confirmations)."""
    stop_speaking()
    subprocess.run(
        ["say", "-v", "Evan", "-r", "230", text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SPEECH INPUT - Whisper (Fast Local Recognition)
# ═══════════════════════════════════════════════════════════════════════════════

def _record_audio(duration: float = 8.0, silence_threshold: int = 500, 
                  silence_duration: float = 1.5) -> Optional[np.ndarray]:
    """
    Record audio with automatic silence detection.
    Returns numpy array of audio samples.
    """
    pa = _get_pyaudio()
    
    # Find microphone device - use default input device
    try:
        default_device = pa.get_default_input_device_info()
        device_index = default_device['index']
    except Exception:
        device_index = 0  # Fallback to device 0
    
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        input=True,
        input_device_index=device_index,
        frames_per_buffer=CHUNK_SIZE
    )
    
    frames = []
    silent_chunks = 0
    max_silent_chunks = int(silence_duration * SAMPLE_RATE / CHUNK_SIZE)
    max_chunks = int(duration * SAMPLE_RATE / CHUNK_SIZE)
    has_speech = False
    
    try:
        for _ in range(max_chunks):
            try:
                data = stream.read(CHUNK_SIZE, exception_on_overflow=False)
            except Exception:
                continue
                
            if not data or len(data) == 0:
                continue
                
            frames.append(data)
            
            # Check volume - handle potential empty/zero data
            audio_int16 = np.frombuffer(data, np.int16)
            if len(audio_int16) == 0:
                continue
            
            # Safe RMS calculation
            mean_sq = np.mean(audio_int16.astype(np.float64)**2)
            rms = np.sqrt(max(0, mean_sq))  # Ensure non-negative
            
            if rms > silence_threshold:
                has_speech = True
                silent_chunks = 0
            else:
                silent_chunks += 1
            
            # Stop after silence following speech
            if has_speech and silent_chunks > max_silent_chunks:
                break
                
    except Exception as e:
        print(f"[NEXUS] Recording error: {e}")
    finally:
        try:
            stream.close()
        except Exception:
            pass
    
    if not frames:
        return None
    
    # Convert to numpy array
    audio_data = b''.join(frames)
    if len(audio_data) == 0:
        return None
        
    audio_np = np.frombuffer(audio_data, np.int16).astype(np.float32) / 32768.0
    return audio_np


def listen_to_user(timeout: int = 8) -> Optional[str]:
    """
    Listen and transcribe using local faster-whisper (no network, no API cost).
    """
    global _whisper_model
    
    if _whisper_model is None:
        _init_whisper()
    
    stop_speaking()
    time.sleep(0.5)  # Brief pause to let audio settle after speaking
    print("\n[NEXUS] Listening...")
    
    # Record audio
    audio = _record_audio(duration=float(timeout))
    if audio is None or len(audio) == 0:
        return None
    
    print("[NEXUS] Transcribing...")
    
    try:
        # Prompt helps Whisper recognize domain-specific vocabulary
        initial_prompt = (
            "Nexus voice commands: open, close, quit, launch, "
            "shut yourself down, restart yourself, terminate yourself, "
            "send message, read messages, create note, set reminder, "
            "Chrome, Safari, Discord, Notes, VSCode, Visual Studio Code, "
            "Terminal, Spotify, calendar, reminder, confirm, cancel, yes, no"
        )
        
        # Transcribe with faster-whisper (local, fast)
        segments, info = _whisper_model.transcribe(
            audio,
            language="en",
            initial_prompt=initial_prompt,
            beam_size=5,
            vad_filter=True,  # Filter out non-speech
        )
        
        # Collect all segments
        text = " ".join(segment.text for segment in segments).strip()
        
        if text:
            print(f"[NEXUS] Heard: {text}")
            return text
        return None
        
    except Exception as e:
        print(f"[NEXUS] Transcription error: {e}")
        return None


def quick_listen(timeout: float = 2.0) -> Optional[str]:
    """
    Quick listen for interrupt commands.
    Uses shorter duration for faster response.
    """
    global _whisper_model
    
    if _whisper_model is None:
        _init_whisper()
    
    audio = _record_audio(duration=timeout, silence_duration=0.5)
    if audio is None:
        return None
    
    try:
        result = _whisper_model.transcribe(
            audio,
            language="en", 
            fp16=False,
            temperature=0,
        )
        return result.get("text", "").strip() or None
    except Exception:
        return None


def listen_for_interrupt(timeout: float = 1.5) -> Tuple[bool, Optional[str], str]:
    """
    Quick check for interrupt commands during action execution.
    
    Returns (was_interrupted, raw_text, intent)
    """
    text = quick_listen(timeout)
    if not text:
        return False, None, "none"
    
    is_interrupt, intent = classify_interrupt(text)
    if is_interrupt:
        set_interrupt()
    
    return is_interrupt, text, intent