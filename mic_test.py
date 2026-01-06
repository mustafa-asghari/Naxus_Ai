import speech_recognition as sr

def test_microphone():
    # 1. Setup the Recognizer
    # This acts as the "brain" that processes the audio
    r = sr.Recognizer()

    # 2. List all available microphones
    # This helps us see if Python can even SEE your hardware
    print("--- Available Microphones ---")
    for index, name in enumerate(sr.Microphone.list_microphone_names()):
        print(f"Mic Index {index}: {name}")
    print("-----------------------------")

    # 3. Open the Microphone connection
    # We use the default microphone here.
    with sr.Microphone() as source:
        print("\n[STATUS] Calibrating for background noise... (Please be quiet)")
        
        # This line listens to your room's silence to figure out 
        # what is "noise" and what is "speech".
        r.adjust_for_ambient_noise(source, duration=2)
        
        print("[STATUS] Calibration Complete. SPEAK NOW!")
        print("[STATUS] Say something like: 'Testing, one, two, three'")

        try:
            # 4. Listen for input
            # The code will pause here until it hears speaking
            audio_data = r.listen(source, timeout=5)
            print("[STATUS] Audio captured. Processing...")

            # 5. Convert Audio to Text (Google API)
            text = r.recognize_google(audio_data)
            print(f"\n[SUCCESS] I heard you say: '{text}'")

        except sr.WaitTimeoutError:
            print("\n[ERROR] I stopped listening because I didn't hear anything for 5 seconds.")
            print("Tip: Check if your mic is muted or if the volume is too low.")
        except sr.UnknownValueError:
            print("\n[ERROR] I heard sound, but couldn't understand the words.")
        except Exception as e:
            print(f"\n[ERROR] Something went wrong: {e}")

if __name__ == "__main__":
    test_microphone()