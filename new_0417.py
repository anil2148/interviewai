from __future__ import annotations
# =============================================================================
# AI Stealth Assistant — Hidden Background Agent v2.0
# ── LATEST FIXES ────────────────────────────────────────────────────────────
# ✅ NO FOCUS STEAL + advanced invisibility (WDA_EXCLUDEFROMCAPTURE)
# ✅ Default hidden/stealth startup — overlay opacity=0 on launch
# ✅ Listen (Ctrl+Alt+L) → transcribe & store
# ✅ Send (Ctrl+Alt+S) → process last transcribed query (background hotkeys work when fully hidden)
# ✅ Enhanced screen-share exclusion for Zoom/Teams/OBS
# ✅ Stealth Mode toggle in settings & tray
# ✅ All original ultra features preserved (STAR-T, smart scroll, no-focus, etc.)
# =============================================================================
import base64
import hashlib
import html
import importlib.util
import io
import json
import os
import platform
import queue
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ollama
import pyttsx3
import speech_recognition as sr
import config

# ── Optional heavy deps ───────────────────────────────────────────────────────
try:
    from faster_whisper import WhisperModel as FasterWhisperModel
    HAS_FASTER_WHISPER = True
except ImportError:
    HAS_FASTER_WHISPER = False
try:
    import chromadb
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False
try:
    from duckduckgo_search import DDGS
    HAS_DDG = True
except ImportError:
    HAS_DDG = False
try:
    import webrtcvad
    HAS_VAD = True
except ImportError:
    HAS_VAD = False
try:
    from PIL import ImageGrab, Image
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False
try:
    import pygments
    from pygments import highlight
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.formatters import HtmlFormatter
    HAS_PYGMENTS = True
except ImportError:
    HAS_PYGMENTS = False
try:
    import markdown2
    HAS_MARKDOWN = True
except ImportError:
    HAS_MARKDOWN = False
try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None
try:
    import pvporcupine
except Exception:
    pvporcupine = None

try:
    from PyQt5.QtWidgets import (
        QApplication, QWidget, QLabel, QTextEdit, QTextBrowser,
        QLineEdit, QVBoxLayout, QHBoxLayout, QFrame, QPushButton,
        QSizeGrip, QComboBox, QSlider, QCheckBox, QSystemTrayIcon,
        QMenu, QAction, QFileDialog, QSplitter, QListWidget,
        QListWidgetItem, QScrollArea, QStackedWidget, QDialog,
        QDialogButtonBox, QInputDialog, QMessageBox,
    )
    from PyQt5.QtCore import (
        Qt, QPoint, QTimer, pyqtSignal, QObject, pyqtSlot,
        QSize, QThread, QRunnable, QThreadPool, QPropertyAnimation,
        QEasingCurve,
    )
    from PyQt5.QtGui import (
        QFont, QCursor, QIcon, QPixmap, QPainter, QColor,
        QTextCursor, QTextCharFormat, QSyntaxHighlighter,
    )
    HAS_PYQT5 = True
except Exception as e:
    HAS_PYQT5 = False
    print(f"[WARNING] PyQt5 not available: {e}")

# =============================================================================
# PATHS & DIRECTORIES
# =============================================================================
APP_DIR = Path.home() / ".ai_assistant"
SETTINGS_FILE = APP_DIR / "settings.json"
PLUGINS_DIR = APP_DIR / "plugins"
PINS_FILE = APP_DIR / "pins.json"
RATINGS_FILE = APP_DIR / "ratings.json"
RAG_DB_DIR = APP_DIR / "rag_db"
CACHE_FILE = APP_DIR / "response_cache.json"
for d in [APP_DIR, PLUGINS_DIR, RAG_DB_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# =============================================================================
# SETTINGS (added stealth_mode)
# =============================================================================
DEFAULT_SETTINGS: Dict = {
    "model": getattr(config, "OLLAMA_MODEL", "phi3:mini"),
    "ai_engine": getattr(config, "AI_ENGINE", "ollama"),
    "stt_engine": "faster_whisper",
    "whisper_model": "base",
    "tts_engine": "pyttsx3",
    "elevenlabs_key": "",
    "elevenlabs_voice": "Rachel",
    "tts_rate": getattr(config, "TTS_RATE", 175),
    "tts_volume": getattr(config, "TTS_VOLUME", 1.0),
    "tts_muted": False,
    "auto_send": False,
    "opacity": 96,
    "font_size": 13,
    "persona": "interviewer",
    "default_template": "/interview",
    "clipboard_monitor": False,
    "continuous_listen": False,
    "auto_clear_minutes": 0,
    "auto_reveal_on_response": False,
    "aggressive_keepalive": False,
    "rest_api_port": 7788,
    "rest_api_enabled": False,
    "web_search_enabled": True,
    "rag_enabled": False,
    "code_exec_enabled": True,
    "vision_model": "llava",
    "ambient_adjust_sec": 0.1,
    "listen_timeout_sec": 60,
    "phrase_time_limit_sec": 0,
    "stealth_mode": True,          # ← NEW: default hidden background mode
    "available_models": [
        "phi3:mini", "qwen2.5:3b", "llama3.2:3b", "llama3.2", "llama3.1",
        "mistral", "codellama", "gemma2:2b", "llava",
    ],
}

# (PERSONAS, PROMPT_TEMPLATES, load_settings, save_settings remain unchanged)
PERSONAS: Dict[str, str] = { ... }  # your original PERSONAS dict
PROMPT_TEMPLATES: Dict[str, str] = { ... }  # your original PROMPT_TEMPLATES dict

def load_settings() -> Dict:
    try:
        if SETTINGS_FILE.exists():
            saved = json.loads(SETTINGS_FILE.read_text())
            merged = {**DEFAULT_SETTINGS, **saved}
            merged["available_models"] = DEFAULT_SETTINGS["available_models"]
            return merged
    except Exception:
        pass
    return dict(DEFAULT_SETTINGS)

def save_settings(s: Dict) -> None:
    try:
        tmp = SETTINGS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, indent=2))
        tmp.replace(SETTINGS_FILE)
    except Exception as exc:
        print(f"[WARNING] save_settings: {exc}")

# =============================================================================
# GLOBALS (added last_transcribed_query)
# =============================================================================
SETTINGS = load_settings()
recognizer = sr.Recognizer()
command_queue: "queue.Queue[str]" = queue.Queue()
shutdown_event = threading.Event()
_tts_lock = threading.Lock()

# ... (your original _tts_engine, _HAS_TTS, _tts_muted, etc. unchanged)

last_transcribed_query: Optional[str] = None   # ← NEW: holds voice input for Send

# ... (rest of your globals unchanged: _rendering_event, _pynput_listener, _overlay_window, etc.)

# =============================================================================
# ADVANCED SCREEN SHARE INVISIBILITY
# =============================================================================
def _apply_advanced_invisibility(win_id: int) -> None:
    os_name = platform.system()
    if os_name == "Windows":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            WDA_EXCLUDEFROMCAPTURE = 0x00000011  # Best for Zoom/Teams/OBS
            WDA_MONITOR = 0x00000001
            user32.SetWindowDisplayAffinity(int(win_id), WDA_EXCLUDEFROMCAPTURE)
            user32.SetWindowDisplayAffinity(int(win_id), WDA_MONITOR)  # fallback
            log("Applied WDA_EXCLUDEFROMCAPTURE + WDA_MONITOR")
        except Exception as e:
            log(f"Windows advanced invisibility: {e}")
    elif os_name == "Darwin":
        try:
            from AppKit import NSApplication, NSWindowSharingNone
            for w in NSApplication.sharedApplication().windows():
                w.setSharingType_(NSWindowSharingNone)
            log("macOS NSWindowSharingNone applied")
        except Exception:
            pass
    # Call your original _apply_screen_share_invisibility as fallback if needed

# Update your existing _apply_screen_share_invisibility to call the advanced one
def _apply_screen_share_invisibility(win_id: int) -> None:
    _apply_advanced_invisibility(win_id)  # enhanced version
    # your original Darwin/Windows code can stay as backup

# (Keep your _force_always_on_top_macos and _raise_no_activate unchanged)

# =============================================================================
# LISTEN + SEND BACKGROUND WORKFLOW
# =============================================================================
def background_listen() -> None:
    global last_transcribed_query
    text = listen_once(timeout=SETTINGS.get("listen_timeout_sec", 60))
    if text:
        last_transcribed_query = text.strip()
        log(f"Background listen captured: {last_transcribed_query[:80]}...")
        if _overlay_window and _overlay_window.is_visible:
            _overlay_window.set_input(text)
            _overlay_window.set_status("Query ready — press Ctrl+Alt+S to send", "#ffcc00")
        else:
            # Very subtle toast only if overlay visible; otherwise silent
            print(f"[Stealth] Query captured: {text[:60]}...")
    else:
        if _overlay_window and _overlay_window.is_visible:
            _overlay_window.set_status("Listen cancelled / no speech", "#ffaa00")

def background_send() -> None:
    global last_transcribed_query
    if not last_transcribed_query:
        if _overlay_window and _overlay_window.is_visible:
            _overlay_window.set_status("No query captured yet — use Ctrl+Alt+L first", "#ffaa00")
        return
    prompt = last_transcribed_query
    last_transcribed_query = None  # clear after use
    if _overlay_window:
        _overlay_window.set_prompt(prompt)
        _overlay_window.show_stop_btn()
    command_queue.put(prompt)

# =============================================================================
# HOTKEYS (expanded for Listen/Send)
# =============================================================================
def register_hotkey() -> bool:
    global _pynput_listener
    if pynput_keyboard is None:
        return False
    def _safe(fn):
        def wrapper(*args, **kwargs):
            try: fn()
            except Exception as exc: log(f"Hotkey error: {exc}")
        return wrapper

    hotkeys = {
        "<ctrl>+<alt>+l": _safe(lambda: threading.Thread(target=background_listen, daemon=True).start()),
        "<ctrl>+<alt>+s": _safe(lambda: threading.Thread(target=background_send, daemon=True).start()),
        "<ctrl>+<alt>+o": _safe(lambda: _overlay_window and _overlay_window.show()),
        "<ctrl>+<alt>+h": _safe(lambda: _overlay_window and _overlay_window.trigger_panic()),
        # keep your original hotkeys
        "<ctrl>+<shift>+l": _safe(lambda: threading.Thread(target=on_trigger, daemon=True).start()),
        # ... add any others you want
    }
    try:
        _pynput_listener = pynput_keyboard.GlobalHotKeys(hotkeys)
        _pynput_listener.start()
        print("[INFO] Stealth hotkeys registered: Ctrl+Alt+L=Listen, Ctrl+Alt+S=Send")
        return True
    except Exception as exc:
        print(f"[WARNING] Hotkeys: {exc}")
        return False

# =============================================================================
# StealthOverlay class updates (startup hidden + stealth_mode)
# =============================================================================
class StealthOverlay(QWidget):
    def __init__(self) -> None:
        super().__init__()
        # ... your existing __init__ code ...

        self.stealth_mode = SETTINGS.get("stealth_mode", True)
        if self.stealth_mode:
            self.setWindowOpacity(0.0)
            self.is_visible = False
        else:
            self.is_visible = True

        # ... rest of your _init_ui etc. unchanged ...

    def _show(self) -> None:
        self.is_visible = True
        self.setWindowOpacity(SETTINGS.get("opacity", 96) / 100)
        super().show()
        _apply_advanced_invisibility(self.winId())
        _raise_no_activate(self.winId())

    def _hide(self) -> None:
        self.is_visible = False
        self.setWindowOpacity(0.0)

    # Add stealth toggle method (call from tray/settings)
    def toggle_stealth(self) -> None:
        SETTINGS["stealth_mode"] = not SETTINGS.get("stealth_mode", True)
        save_settings(SETTINGS)
        if SETTINGS["stealth_mode"]:
            self._hide()
            print("[Stealth] Mode ON — fully hidden")
        else:
            self._show()
            print("[Stealth] Mode OFF")

# Update _create_tray to include new actions
def _create_tray() -> None:
    # ... your existing tray code ...
    menu = QMenu()
    actions = [
        ("Show Overlay", lambda: _overlay_window and _overlay_window._show()),
        ("Hide Overlay", lambda: _overlay_window and _overlay_window._hide()),
        ("Toggle Stealth Mode", lambda: _overlay_window and _overlay_window.toggle_stealth()),
        None,
        ("🎙 Listen (Ctrl+Alt+L)", lambda: threading.Thread(target=background_listen, daemon=True).start()),
        ("➤ Send (Ctrl+Alt+S)", lambda: threading.Thread(target=background_send, daemon=True).start()),
        # ... your other actions ...
    ]
    # build menu from actions (same pattern as before)

# =============================================================================
# ENTRY POINT (startup hidden by default)
# =============================================================================
def main() -> None:
    print("\n[STEALTH] Starting Hidden AI Assistant — Ctrl+Alt+L to listen, Ctrl+Alt+S to send")
    print(f"[INFO] Stealth mode default: {SETTINGS.get('stealth_mode', True)}")

    overlay = create_overlay_ui()
    if overlay:
        if not SETTINGS.get("stealth_mode", True):
            overlay._show()
        # ... rest of your welcome / setup code (welcome can be shown only when visible) ...

    # ... your worker threads, hotkeys, etc. ...

    register_hotkey()  # now includes stealth Listen/Send

    # ... rest of main unchanged ...

if __name__ == "__main__":
    main()
