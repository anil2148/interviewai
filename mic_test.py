import sounddevice as sd
import numpy as np
from scipy.io.wavfile import write

fs = 16000

print("Recording 3 seconds...")
audio = sd.rec(int(3 * fs), samplerate=fs, channels=1)
sd.wait()

write("test.wav", fs, audio)

print("Saved as test.wav")