from __future__ import annotations

# === STEALTH AI AGENT v2.0 ===
# Rewritten as a tray-first, background-only assistant designed for stealth usage.
# Key updates:
# - No main desktop window by default, optional tiny transparent floating control.
# - Global hotkeys for Listen and Send (Ctrl+Alt+L / Ctrl+Alt+S).
# - Offline-first transcription via faster-whisper with speech_recognition capture.
# - Reuses core AI response logic pattern (persona + Ollama chat generation).
# - TTS output + toast notifications; threaded execution to keep hotkeys/tray responsive.
#
# Dependencies:
#   pip install ollama pyttsx3 SpeechRecognition faster-whisper pystray keyboard pillow plyer win10toast
#   (optional for better audio device support): pip install pyaudio

import ctypes
import io
import json
import os
import queue
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import ollama
import pyttsx3
import speech_recognition as sr
from PIL import Image

import config

try:
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
except Exception:
    WhisperModel = None
    HAS_FASTER_WHISPER = False

try:
    import keyboard
    HAS_KEYBOARD = True
except Exception:
    keyboard = None
    HAS_KEYBOARD = False

try:
    import pystray
    HAS_PYSTRAY = True
except Exception:
    pystray = None
    HAS_PYSTRAY = False

try:
    from plyer import notification as plyer_notification
    HAS_PLYER = True
except Exception:
    plyer_notification = None
    HAS_PLYER = False

try:
    from win10toast import ToastNotifier
    HAS_WIN10TOAST = True
except Exception:
    ToastNotifier = None
    HAS_WIN10TOAST = False

try:
    import tkinter as tk
    HAS_TK = True
except Exception:
    tk = None
    HAS_TK = False

# ===============================
# Configuration
# ===============================
APP_DIR = Path.home() / ".ai_stealth_agent"
SETTINGS_FILE = APP_DIR / "settings.json"
APP_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SETTINGS: Dict[str, Any] = {
    "listen_hotkey": "ctrl+alt+l",
    "send_hotkey": "ctrl+alt+s",
    "toggle_ui_hotkey": "ctrl+alt+u",
    "stealth_mode": True,
    "floating_button_enabled": False,
    "whisper_model": "base",
    "whisper_device": "cpu",
    "listen_timeout_sec": 20,
    "phrase_time_limit_sec": 25,
    "ambient_adjust_sec": 0.5,
    "ollama_host": getattr(config, "OLLAMA_HOST", "http://localhost:11434"),
    "ollama_model": getattr(config, "OLLAMA_MODEL", "llama3.1"),
    "persona": (
        "You are an expert interview and systems engineering assistant. "
        "Give concise, accurate spoken-friendly answers."
    ),
    "tts_rate": getattr(config, "TTS_RATE", 190),
    "tts_volume": getattr(config, "TTS_VOLUME", 1.0),
}


# ===============================
# Helpers
# ===============================
def load_settings() -> Dict[str, Any]:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return {**DEFAULT_SETTINGS, **data}
        except Exception:
            pass
    return dict(DEFAULT_SETTINGS)


def save_settings(settings: Dict[str, Any]) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def hide_console_window() -> None:
    if os.name != "nt":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        pass


def make_transparent_tray_icon() -> Image.Image:
    img = Image.new("RGBA", (16, 16), (0, 0, 0, 1))
    return img


def show_toast(title: str, message: str) -> None:
    try:
        if HAS_PLYER:
            plyer_notification.notify(title=title, message=message, timeout=3)
            return
        if HAS_WIN10TOAST and os.name == "nt":
            ToastNotifier().show_toast(title, message, threaded=True, duration=3)
    except Exception:
        pass


@dataclass
class AgentState:
    settings: Dict[str, Any]
    shutdown_event: threading.Event
    last_transcript: str = ""
    listening_lock: threading.Lock = field(default_factory=threading.Lock)
    sending_lock: threading.Lock = field(default_factory=threading.Lock)
    ui_visible: bool = False


class StealthAIAgent:
    def __init__(self) -> None:
        self.state = AgentState(settings=load_settings(), shutdown_event=threading.Event())
        self.recognizer = sr.Recognizer()
        self.client = ollama.Client(host=self.state.settings["ollama_host"])

        self.tts_engine = pyttsx3.init()
        self.tts_engine.setProperty("rate", self.state.settings["tts_rate"])
        self.tts_engine.setProperty("volume", self.state.settings["tts_volume"])
        self.tts_lock = threading.Lock()

        self.whisper_model = None
        self.whisper_lock = threading.Lock()

        self.tray_icon = None
        self.tk_root = None
        self.tk_button = None

        self._load_whisper_once()

    # -------- Core AI logic (reused architecture) --------
    def ask_ai(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": self.state.settings["persona"]},
            {"role": "user", "content": prompt},
        ]
        try:
            response = self.client.chat(
                model=self.state.settings["ollama_model"],
                messages=messages,
                options={"temperature": 0.4, "num_ctx": 4096},
            )
            text = (response.message.content or "").strip()
            return text or "I generated an empty response."
        except Exception as exc:
            return f"AI error: {exc}"

    # -------- Speech --------
    def _load_whisper_once(self) -> None:
        if not HAS_FASTER_WHISPER:
            return
        with self.whisper_lock:
            if self.whisper_model is None:
                self.whisper_model = WhisperModel(
                    self.state.settings["whisper_model"],
                    device=self.state.settings["whisper_device"],
                    compute_type="int8",
                )

    def transcribe_audio_bytes(self, wav_bytes: bytes) -> Optional[str]:
        if HAS_FASTER_WHISPER and self.whisper_model is not None:
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                    tmp.write(wav_bytes)
                    wav_path = tmp.name
                segments, _ = self.whisper_model.transcribe(wav_path, vad_filter=True, beam_size=4)
                text = " ".join(seg.text.strip() for seg in segments if seg.text).strip()
                os.unlink(wav_path)
                if text:
                    return text
            except Exception:
                pass
        try:
            audio = sr.AudioData(wav_bytes, sample_rate=16000, sample_width=2)
            text = self.recognizer.recognize_google(audio)
            return text.strip() if text else None
        except Exception:
            return None

    def listen_action(self) -> None:
        if not self.state.listening_lock.acquire(blocking=False):
            show_toast("Stealth AI", "Already listening.")
            return

        def _work() -> None:
            try:
                with sr.Microphone() as source:
                    self.recognizer.adjust_for_ambient_noise(
                        source, duration=self.state.settings["ambient_adjust_sec"]
                    )
                    audio = self.recognizer.listen(
                        source,
                        timeout=self.state.settings["listen_timeout_sec"],
                        phrase_time_limit=self.state.settings["phrase_time_limit_sec"],
                    )
                text = self.transcribe_audio_bytes(audio.get_wav_data())
                if text:
                    self.state.last_transcript = text
                    show_toast("Stealth AI", f"Heard: {text[:80]}")
                else:
                    show_toast("Stealth AI", "No speech recognized.")
            except sr.WaitTimeoutError:
                show_toast("Stealth AI", "Listening timeout.")
            except OSError as exc:
                show_toast("Stealth AI", f"Microphone error: {exc}")
            except Exception as exc:
                show_toast("Stealth AI", f"Listen failed: {exc}")
            finally:
                self.state.listening_lock.release()

        threading.Thread(target=_work, daemon=True).start()

    def send_action(self) -> None:
        if not self.state.last_transcript:
            show_toast("Stealth AI", "No transcript available. Press Listen first.")
            return
        if not self.state.sending_lock.acquire(blocking=False):
            show_toast("Stealth AI", "Already generating response.")
            return

        prompt = self.state.last_transcript

        def _work() -> None:
            try:
                answer = self.ask_ai(prompt)
                show_toast("Stealth AI Response", answer[:120])
                self.speak(answer)
            finally:
                self.state.sending_lock.release()

        threading.Thread(target=_work, daemon=True).start()

    def speak(self, text: str) -> None:
        with self.tts_lock:
            try:
                self.tts_engine.say(text)
                self.tts_engine.runAndWait()
            except Exception:
                pass

    # -------- Windows stealth flags --------
    def _apply_window_stealth_flags(self) -> None:
        if os.name != "nt" or not self.tk_root:
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.tk_root.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_NOACTIVATE = 0x08000000
            WS_EX_TRANSPARENT = 0x00000020
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
        except Exception:
            pass

    # -------- Floating button --------
    def _toggle_ui(self, visible: Optional[bool] = None) -> None:
        if not self.tk_root:
            return
        desired = (not self.state.ui_visible) if visible is None else visible
        self.state.ui_visible = desired
        if desired and not self.state.settings["stealth_mode"]:
            self.tk_root.deiconify()
            self.tk_root.attributes("-alpha", 0.15)
        else:
            self.tk_root.attributes("-alpha", 0.0)
            self.tk_root.withdraw()

    def setup_floating_button(self) -> None:
        if not HAS_TK:
            return
        self.tk_root = tk.Tk()
        self.tk_root.title(".")
        self.tk_root.overrideredirect(True)
        self.tk_root.attributes("-topmost", True)
        self.tk_root.geometry("44x44+20+120")
        self.tk_root.attributes("-alpha", 0.0)

        frame = tk.Frame(self.tk_root, bg="#111111")
        frame.pack(fill="both", expand=True)

        self.tk_button = tk.Button(
            frame,
            text="●",
            fg="#00ff88",
            bg="#111111",
            bd=0,
            highlightthickness=0,
            command=self.listen_action,
        )
        self.tk_button.pack(fill="both", expand=True)
        self.tk_button.bind("<Button-3>", lambda _e: self.send_action())

        self.tk_root.after(200, self._apply_window_stealth_flags)
        self._toggle_ui(False)

        threading.Thread(target=self.tk_root.mainloop, daemon=True).start()

    # -------- Stealth mode --------
    def set_stealth_mode(self, enabled: bool) -> None:
        self.state.settings["stealth_mode"] = enabled
        save_settings(self.state.settings)
        if enabled:
            self._toggle_ui(False)
            if self.tray_icon:
                self.tray_icon.visible = False
            show_toast("Stealth AI", "Stealth mode ON")
        else:
            if self.tray_icon:
                self.tray_icon.visible = True
            if self.state.settings["floating_button_enabled"]:
                self._toggle_ui(True)
            show_toast("Stealth AI", "Stealth mode OFF")

    # -------- Tray --------
    def _tray_toggle_ui(self, icon=None, item=None) -> None:
        self._toggle_ui()

    def _tray_toggle_stealth(self, icon=None, item=None) -> None:
        self.set_stealth_mode(not self.state.settings["stealth_mode"])

    def _tray_exit(self, icon=None, item=None) -> None:
        self.state.shutdown_event.set()
        try:
            if HAS_KEYBOARD:
                keyboard.unhook_all_hotkeys()
        except Exception:
            pass
        if self.tk_root:
            try:
                self.tk_root.after(0, self.tk_root.destroy)
            except Exception:
                pass
        if self.tray_icon:
            self.tray_icon.stop()

    def setup_tray(self) -> None:
        if not HAS_PYSTRAY:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Show/Hide Stealth UI", self._tray_toggle_ui),
            pystray.MenuItem("Stealth Mode On/Off", self._tray_toggle_stealth),
            pystray.MenuItem("Exit", self._tray_exit),
        )
        self.tray_icon = pystray.Icon(
            "stealth_ai_agent",
            icon=make_transparent_tray_icon(),
            title="Stealth AI Agent",
            menu=menu,
        )
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        if self.state.settings["stealth_mode"]:
            self.tray_icon.visible = False

    # -------- Hotkeys --------
    def setup_hotkeys(self) -> None:
        if not HAS_KEYBOARD:
            raise RuntimeError("keyboard package unavailable. Install with: pip install keyboard")

        keyboard.add_hotkey(self.state.settings["listen_hotkey"], self.listen_action)
        keyboard.add_hotkey(self.state.settings["send_hotkey"], self.send_action)
        keyboard.add_hotkey(self.state.settings["toggle_ui_hotkey"], lambda: self._toggle_ui())

    def run(self) -> None:
        hide_console_window()

        self.setup_hotkeys()
        self.setup_tray()

        if self.state.settings["floating_button_enabled"]:
            self.setup_floating_button()

        show_toast(
            "Stealth AI",
            f"Ready. Listen={self.state.settings['listen_hotkey']} Send={self.state.settings['send_hotkey']}",
        )

        try:
            while not self.state.shutdown_event.is_set():
                time.sleep(0.2)
        except KeyboardInterrupt:
            self._tray_exit()


if __name__ == "__main__":
    agent = StealthAIAgent()
    agent.run()


# =============================================================================
# New Dependencies / Run Guide
# =============================================================================
# pip install ollama pyttsx3 SpeechRecognition faster-whisper pystray keyboard pillow plyer win10toast
# Optional mic backend: pip install pyaudio
#
# Run in stealth/background mode:
#   - Windows hidden console: pythonw ai_stealth_assistant_fixed.py
#   - Standard run:           python ai_stealth_assistant_fixed.py
#
# Default hotkeys:
#   - Listen: Ctrl+Alt+L
#   - Send: Ctrl+Alt+S
#   - Toggle UI: Ctrl+Alt+U
