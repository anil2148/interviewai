"""HiddenAIAssistant: background voice assistant with hotkey and optional wake word.

Deployment notes
----------------
Windows:
  - Run with pythonw.exe to hide console, e.g. `pythonw.exe assistant.py`
  - Or package with PyInstaller: `pyinstaller --noconsole --onefile assistant.py`

macOS:
  - Run detached with `nohup python3 assistant.py &`
  - For startup, use a LaunchAgent (.plist)

Linux:
  - Run detached with `nohup python3 assistant.py &`
  - For startup, add a .desktop file to ~/.config/autostart/
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import keyboard
import ollama
import pyttsx3
import speech_recognition as sr

import config

try:
    import pvporcupine  # type: ignore
except Exception:  # optional dependency
    pvporcupine = None


recognizer = sr.Recognizer()
command_queue: "queue.Queue[str]" = queue.Queue()
shutdown_event = threading.Event()
_tts_lock = threading.Lock()
_tts_engine = pyttsx3.init()
_tts_engine.setProperty("rate", config.TTS_RATE)
_tts_engine.setProperty("volume", config.TTS_VOLUME)


def log(msg: str) -> None:
    if config.DEBUG:
        print(f"[HiddenAIAssistant] {msg}")


def listen_once(timeout: float) -> Optional[str]:
    """Record one utterance and return transcribed text, or None on failure."""
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=config.AMBIENT_ADJUST_SECONDS)
        log("Listening for speech...")
        try:
            audio = recognizer.listen(
                source,
                timeout=timeout,
                phrase_time_limit=config.LISTEN_PHRASE_TIME_LIMIT_SECONDS,
            )
        except sr.WaitTimeoutError:
            log("Listen timeout: no speech detected.")
            return None

    try:
        if config.STT_ENGINE == "google":
            text = recognizer.recognize_google(audio)
            return text.strip()

        if config.STT_ENGINE == "vosk":
            if not config.VOSK_MODEL_PATH:
                log("Vosk selected but HIDDEN_AI_VOSK_MODEL_PATH is empty.")
                return None
            raw = recognizer.recognize_vosk(audio, model=config.VOSK_MODEL_PATH)
            data = json.loads(raw)
            return data.get("text", "").strip() or None

        log(f"Unsupported STT_ENGINE: {config.STT_ENGINE}")
        return None

    except sr.UnknownValueError:
        log("Speech recognition could not understand audio.")
    except sr.RequestError as exc:
        log(f"Speech recognition service error: {exc}")
    except Exception as exc:
        log(f"Unexpected transcription error: {exc}")

    return None


def ask_ai(prompt: str) -> str:
    """Send prompt to configured AI engine and return response text."""
    if config.AI_ENGINE == "ollama":
        response = ollama.chat(
            model=config.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"num_ctx": 4096},
            keep_alive="5m",
        )
        message = response.get("message", {})
        return str(message.get("content", "")).strip()

    if config.AI_ENGINE == "openai":
        if not config.OPENAI_API_KEY:
            return "OpenAI is configured, but OPENAI_API_KEY is missing."

        payload = {
            "model": config.OPENAI_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        }
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{config.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            return (
                body.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
                or "No response text returned by OpenAI."
            )
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="ignore")
            return f"OpenAI HTTP error {exc.code}: {details}"
        except urllib.error.URLError as exc:
            return f"OpenAI connection error: {exc}"

    return f"Unsupported AI_ENGINE '{config.AI_ENGINE}'."


def speak(text: str) -> None:
    """Convert text to speech using pyttsx3."""
    if not text:
        return
    with _tts_lock:
        _tts_engine.say(text)
        _tts_engine.runAndWait()


def process_commands() -> None:
    """Background worker that handles prompts from the command queue."""
    log("Command processor started.")
    while not shutdown_event.is_set():
        try:
            prompt = command_queue.get(timeout=0.25)
        except queue.Empty:
            continue

        if not prompt:
            command_queue.task_done()
            continue

        log(f"Processing prompt: {prompt}")
        try:
            answer = ask_ai(prompt)
        except Exception as exc:
            answer = f"AI processing failed: {exc}"
            log(answer)

        try:
            speak(answer)
        except Exception as exc:
            log(f"TTS failed: {exc}")
        finally:
            command_queue.task_done()


def on_hotkey() -> None:
    """Hotkey callback: capture speech and enqueue transcribed text."""
    if shutdown_event.is_set():
        return

    text = listen_once(timeout=config.LISTEN_TIMEOUT_SECONDS)
    if text:
        log(f"Heard via hotkey: {text}")
        command_queue.put(text)


def wake_word_loop() -> None:
    """Optional wake-word loop based on Porcupine.

    For practical cross-platform deployment, this implementation uses Porcupine to detect
    a wake word and then captures speech using the shared SpeechRecognition microphone flow.
    """
    if not config.USE_WAKE_WORD:
        return

    if pvporcupine is None:
        log("Wake word enabled but pvporcupine is not installed.")
        return

    if not config.PORCUPINE_ACCESS_KEY:
        log("Wake word enabled but PORCUPINE_ACCESS_KEY is missing.")
        return

    import pyaudio

    porcupine = pvporcupine.create(
        access_key=config.PORCUPINE_ACCESS_KEY,
        keywords=config.WAKE_WORD_KEYWORDS,
    )
    pa = pyaudio.PyAudio()
    stream = pa.open(
        rate=porcupine.sample_rate,
        channels=1,
        format=pyaudio.paInt16,
        input=True,
        frames_per_buffer=porcupine.frame_length,
    )

    log(f"Wake word listener started for: {', '.join(config.WAKE_WORD_KEYWORDS)}")

    try:
        while not shutdown_event.is_set():
            pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
            frame = memoryview(pcm).cast("h")
            result = porcupine.process(frame)
            if result >= 0:
                log("Wake word detected.")
                text = listen_once(timeout=config.LISTEN_TIMEOUT_SECONDS)
                if text:
                    command_queue.put(text)
                time.sleep(0.2)
    finally:
        stream.stop_stream()
        stream.close()
        pa.terminate()
        porcupine.delete()
        log("Wake word listener stopped.")


def main() -> None:
    log("Starting HiddenAIAssistant.")

    worker = threading.Thread(target=process_commands, daemon=True)
    worker.start()

    wake_thread = None
    if config.USE_WAKE_WORD:
        wake_thread = threading.Thread(target=wake_word_loop, daemon=True)
        wake_thread.start()

    keyboard.add_hotkey(config.HOTKEY, on_hotkey)
    log(f"Hotkey registered: {config.HOTKEY}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("KeyboardInterrupt received. Shutting down.")
    finally:
        shutdown_event.set()
        keyboard.unhook_all_hotkeys()
        worker.join(timeout=2)
        if wake_thread is not None:
            wake_thread.join(timeout=2)


if __name__ == "__main__":
    main()
