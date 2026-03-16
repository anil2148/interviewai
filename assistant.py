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

import ctypes
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

try:
    from pynput import keyboard as pynput_keyboard  # type: ignore
except Exception:  # optional dependency
    pynput_keyboard = None


recognizer = sr.Recognizer()
command_queue: "queue.Queue[str]" = queue.Queue()
shutdown_event = threading.Event()
_tts_lock = threading.Lock()
_tts_engine = pyttsx3.init()
_tts_engine.setProperty("rate", config.TTS_RATE)
_tts_engine.setProperty("volume", config.TTS_VOLUME)
_hotkey_backend: Optional[str] = None
_pynput_listener: Optional[object] = None


def log(msg: str) -> None:
    if config.DEBUG:
        print(f"[HiddenAIAssistant] {msg}")


def hide_console_window_if_configured() -> None:
    """Hide the Windows console to keep assistant invisible while sharing screen.

    Backward-compatible: if running with an older config module that does not yet
    define HIDE_CONSOLE/PLATFORM, default to hiding on Windows only.
    """
    hide_console = getattr(config, "HIDE_CONSOLE", True)
    platform_name = str(getattr(config, "PLATFORM", "")).lower()
    if not platform_name:
        import platform as _platform

        platform_name = "windows" if _platform.system().lower().startswith("win") else _platform.system().lower()

    if not hide_console:
        return

    if platform_name != "windows":
    """Hide the Windows console to keep assistant invisible while sharing screen."""
    if not config.HIDE_CONSOLE:
        return

    if config.PLATFORM != "windows":
        return

    try:
        user32 = ctypes.WinDLL("user32")
        kernel32 = ctypes.WinDLL("kernel32")
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
            log("Windows console window hidden.")
    except Exception as exc:
        log(f"Could not hide console window: {exc}")


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


def _to_pynput_hotkey(hotkey: str) -> str:
    """Convert keyboard package hotkey syntax to pynput GlobalHotKeys syntax."""
    mapping = {
        "ctrl": "<ctrl>",
        "control": "<ctrl>",
        "shift": "<shift>",
        "alt": "<alt>",
        "cmd": "<cmd>",
        "command": "<cmd>",
        "win": "<cmd>",
        "windows": "<cmd>",
        "option": "<alt>",
    }
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    converted = [mapping.get(p, p) for p in parts]
    return "+".join(converted)


def register_hotkey() -> bool:
    """Register a global hotkey using an OS-appropriate backend without crashing."""
    global _hotkey_backend
    global _pynput_listener

    platform_name = str(getattr(config, "PLATFORM", "")).lower()
    if platform_name.startswith("win"):
        platform_name = "windows"

    # Use keyboard backend on Windows; use pynput elsewhere to avoid known keyboard
    # permission/mapping issues on macOS.
    if platform_name == "windows":
        try:
            keyboard.add_hotkey(config.HOTKEY, on_hotkey)
            _hotkey_backend = "keyboard"
            return True
        except Exception as exc:
            log(f"Hotkey registration failed with keyboard backend: {exc}")
            return False

    if pynput_keyboard is None:
        log("pynput is not installed; hotkey disabled on this platform.")
        return False

    try:
        hotkey = _to_pynput_hotkey(config.HOTKEY)
        listener = pynput_keyboard.GlobalHotKeys({hotkey: on_hotkey})
        listener.start()
        _pynput_listener = listener
        _hotkey_backend = "pynput"
        return True
    except Exception as exc:
        log(f"Hotkey registration failed with pynput backend: {exc}")
        return False


def cleanup_hotkeys() -> None:
    """Unregister hotkeys/listeners for whichever backend was used."""
    global _pynput_listener

    if _hotkey_backend == "keyboard":
        keyboard.unhook_all_hotkeys()

    if _hotkey_backend == "pynput" and _pynput_listener is not None:
        try:
            _pynput_listener.stop()
        except Exception as exc:
            log(f"Failed to stop pynput listener cleanly: {exc}")


def wake_word_loop() -> None:
    """Optional wake-word loop based on Porcupine.

    Notes:
    - Requires `pvporcupine` and `pyaudio` installed.
    - Requires `PORCUPINE_ACCESS_KEY`.
    """
    if pvporcupine is None:
        log("pvporcupine not installed. Wake-word loop disabled.")
        return
    if not config.PORCUPINE_ACCESS_KEY:
        log("PORCUPINE_ACCESS_KEY missing. Wake-word loop disabled.")
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

    hide_console_window_if_configured()

    worker = threading.Thread(target=process_commands, daemon=True)
    worker.start()

    wake_thread = None
    if config.USE_WAKE_WORD:
        wake_thread = threading.Thread(target=wake_word_loop, daemon=True)
        wake_thread.start()

    if register_hotkey():
        log(f"Assistant is running in background. Press {config.HOTKEY} to speak.")
    else:
        log("Global hotkey unavailable. Enable wake word or fix hotkey permissions/dependencies.")
    keyboard.add_hotkey(config.HOTKEY, on_hotkey)
    log(f"Assistant is running in background. Press {config.HOTKEY} to speak.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("KeyboardInterrupt received. Shutting down.")
    finally:
        shutdown_event.set()
        cleanup_hotkeys()
        worker.join(timeout=2)
        if wake_thread is not None:
            wake_thread.join(timeout=2)


if __name__ == "__main__":
    main()
