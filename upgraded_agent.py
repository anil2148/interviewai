from __future__ import annotations
# =============================================================================
#  AI Stealth Assistant  —  Ultra Full Featured
#  ── LATEST FIXES ────────────────────────────────────────────────────────────
#   ✅ NO FOCUS STEAL: Qt.WindowType.WindowDoesNotAcceptFocus + WA_ShowWithoutActivating
#      on window, WA_X11DoNotAcceptFocus for Linux. Removed activateWindow()
#      from _show()/_keepalive(). All background setFocus() calls removed.
#      Overlay stays on top without ever taking focus from the active app.
#   ✅ BIG RESPONSE AREA: user bubble capped at 72px (2 lines), response
#      browser gets all remaining space via stretch=1.
#   ✅ SMART AUTO-SCROLL: tracks whether user manually scrolled up during
#      streaming. If they did, stops auto-scrolling so they can read.
#      If they scroll back to bottom, auto-scroll resumes. ensureCursorVisible()
#      called on every token for smooth cursor-following.
#  ── PREVIOUS FIXES (carried forward) ────────────────────────────────────────
#   ✅ Overlay never disappears — keepalive timer re-raises every 2 s.
#   ✅ Stop button works — stop event cleared at right place, not inside stream.
#   ✅ No auto-send after listen — blockSignals() around setText in _set_input.
#   ✅ Response cleared on new request — _set_prompt clears response_text.
#   ✅ Retry button (🔄 / Ctrl+R), char counter, progress bar, toasts.
#   ✅ STAR-T default persona, /interview template pre-loaded.
#   ✅ All earlier signal type fixes (bool→int), walrus buf, ElevenLabs tmp.
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
import urllib.request
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ollama
try:
    import pyttsx3
    _PYTTSX3_AVAILABLE = True
except Exception:
    pyttsx3 = None  # type: ignore
    _PYTTSX3_AVAILABLE = False
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

# ── PyQt6 ─────────────────────────────────────────────────────────────────────
try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QLabel, QTextEdit, QTextBrowser,
        QLineEdit, QVBoxLayout, QHBoxLayout, QFrame, QPushButton,
        QSizeGrip, QComboBox, QSlider, QCheckBox, QSystemTrayIcon,
        QMenu, QFileDialog, QSplitter, QListWidget, QListWidgetItem,
        QScrollArea, QStackedWidget, QProgressBar,
        QDialogButtonBox, QInputDialog, QMessageBox,
    )
    from PyQt6.QtCore import (
        Qt, QPoint, QTimer, pyqtSignal, QObject, pyqtSlot,
        QSize, QEvent,
    )
    from PyQt6.QtGui import (
        QAction, QFont, QCursor, QIcon, QPixmap, QPainter, QColor,
        QTextCursor, QTextCharFormat,
    )
    HAS_PYQT6 = True
except Exception as e:
    HAS_PYQT6 = False
    print(f"[ERROR] PyQt6 not available: {e}")

# =============================================================================
#  PATHS & DIRECTORIES
# =============================================================================

APP_DIR       = Path.home() / ".ai_assistant"
SETTINGS_FILE = APP_DIR / "settings.json"
PLUGINS_DIR   = APP_DIR / "plugins"
PINS_FILE     = APP_DIR / "pins.json"
RATINGS_FILE  = APP_DIR / "ratings.json"
RAG_DB_DIR    = APP_DIR / "rag_db"
CACHE_FILE    = APP_DIR / "response_cache.json"

for d in [APP_DIR, PLUGINS_DIR, RAG_DB_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# =============================================================================
#  SETTINGS
# =============================================================================

DEFAULT_SETTINGS: Dict = {
    "model":              getattr(config, "OLLAMA_MODEL",  "phi3:mini"),
    "ai_engine":          getattr(config, "AI_ENGINE",     "ollama"),
    "stt_engine":         "faster_whisper",
    "whisper_model":      "base",
    "tts_engine":         "pyttsx3",
    "elevenlabs_key":     "",
    "elevenlabs_voice":   "Rachel",
    "tts_rate":           getattr(config, "TTS_RATE",   175),
    "tts_volume":         getattr(config, "TTS_VOLUME", 1.0),
    "tts_muted":          False,
    "auto_send":          False,
    "opacity":            96,
    "font_size":          13,
    "persona":            "interviewer",   # ← default: interview mode
    "default_template":   "/interview",    # ← pre-select STAR-T template
    "clipboard_monitor":  False,
    "continuous_listen":  False,
    "auto_clear_minutes": 0,
    "auto_reveal_on_response": False,
    "aggressive_keepalive": False,
    "rest_api_port":      7788,
    "rest_api_enabled":   False,
    "web_search_enabled": True,
    "rag_enabled":        False,
    "code_exec_enabled":  True,
    "vision_model":       "llava",
    "ambient_adjust_sec": 0.1,
    "listen_timeout_sec":      60,
    "phrase_time_limit_sec":    0,
    "pause_threshold_sec":      0.8,   # PERF: was missing (fell back to 1.5s) — 0.8s cuts off faster after speech ends
    "available_models":   [
        "phi3:mini", "qwen2.5:3b", "llama3.2:3b", "llama3.2", "llama3.1",
        "mistral", "codellama", "gemma2:2b", "llava",
    ],
}

PERSONAS: Dict[str, str] = {
    "interviewer": (
        "You are an expert interview coach and answer assistant for technical interviews. "
        "ALWAYS structure behavioural answers using the STAR-T framework:\n"
        "  S = Situation  — set the scene briefly (1-2 sentences)\n"
        "  T = Task       — what was your specific responsibility\n"
        "  A = Action     — what YOU specifically did (use 'I', not 'we')\n"
        "  R = Result     — quantify the outcome where possible\n"
        "  T = Takeaway   — what you learned or would do differently\n\n"
        "For TECHNICAL questions: give a clear definition, a concrete example, "
        "then mention trade-offs or best practices.\n"
        "For CODING questions: write clean, commented, production-quality code with explanation.\n"
        "Be specific, use metrics when possible, show depth of knowledge. "
        "Aim for answers that take 90-150 seconds when spoken aloud."
    ),
    "default":   "",
    "coder":     (
        "You are an expert software engineer. Give production-ready code. "
        "Be concise. Always explain key decisions briefly."
    ),
    "architect": (
        "You are a senior solutions architect. Think in systems. "
        "Mention trade-offs, scalability, and best practices."
    ),
    "coach":     (
        "You are a supportive career coach. Give actionable advice. "
        "Be encouraging but realistic."
    ),
    "teacher":   (
        "You are a patient teacher. Explain concepts from first principles. "
        "Use analogies and examples. Check understanding."
    ),
}

PROMPT_TEMPLATES: Dict[str, str] = {
    "/interview": (
        "Give a complete, structured interview answer using the STAR-T framework "
        "(Situation → Task → Action → Result → Takeaway) for this question: "
    ),
    "/code":      "Write clean, production-ready code for: ",
    "/explain":   "Explain this clearly and concisely: ",
    "/bullet":    "Summarize in bullet points: ",
    "/review":    "Review this code, find issues and suggest improvements:\n",
    "/test":      "Write comprehensive unit tests for: ",
    "/debug":     "Debug this and explain the root cause:\n",
    "/simplify":  "Simplify and rewrite this more clearly: ",
    "/search":    "Search the web and answer: ",
    "/vision":    "Analyze the current screen and answer: ",
    "/run":       "Write Python code to accomplish this, then run it: ",
    "/rag":       "Search my documents and answer: ",
}


def load_settings() -> Dict:
    try:
        if SETTINGS_FILE.exists():
            saved  = json.loads(SETTINGS_FILE.read_text())
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
#  GLOBALS
# =============================================================================

SETTINGS       = load_settings()
recognizer     = sr.Recognizer()
command_queue: "queue.Queue[str]" = queue.Queue()
shutdown_event = threading.Event()

_tts_lock = threading.Lock()
_IS_MACOS = platform.system() == "Darwin"

def _init_tts_engine():
    """Initialise pyttsx3. On macOS/M1 this must happen on the thread that calls it."""
    if not _PYTTSX3_AVAILABLE:
        raise RuntimeError("pyttsx3 not available")
    engine = pyttsx3.init()
    engine.setProperty("rate",   SETTINGS["tts_rate"])
    engine.setProperty("volume", SETTINGS["tts_volume"])
    return engine

try:
    if not _IS_MACOS and _PYTTSX3_AVAILABLE:
        # On non-macOS platforms, a single shared engine is fine
        _tts_engine = _init_tts_engine()
    else:
        # On macOS/M1, use 'say' command; pyttsx3 only as per-call fallback
        _tts_engine = None
    _HAS_TTS = True
except Exception as _tts_err:
    print(f"[WARNING] pyttsx3 init failed: {_tts_err} — TTS disabled")
    _tts_engine = None
    _HAS_TTS    = False

_tts_muted: bool   = SETTINGS["tts_muted"]
_rendering_event   = threading.Event()

_pynput_listener   = None
_overlay_window    = None
_qt_app            = None
_tray_icon         = None
_rest_server       = None
_continuous_thread = None
_whisper_model     = None
_ollama_client: Optional[ollama.Client] = None

_conv_history: List[Dict] = []
_session_tokens: int      = 0
_panic_hidden: bool       = False
_clipboard_last: str      = ""

_response_cache: Dict[str, str] = {}
_CACHE_MAX = 200

_generation_stop_event = threading.Event()
_generation_in_progress = threading.Event()


def log(msg: str) -> None:
    if getattr(config, "DEBUG", False):
        print(f"[Assistant] {msg}")


# =============================================================================
#  RESPONSE CACHE
# =============================================================================

def _load_cache() -> None:
    global _response_cache
    try:
        if CACHE_FILE.exists():
            _response_cache = json.loads(CACHE_FILE.read_text())
    except Exception:
        _response_cache = {}


def _save_cache() -> None:
    try:
        CACHE_FILE.write_text(json.dumps(_response_cache))
    except Exception:
        pass


def _cache_key(prompt: str, model: str, persona: str) -> str:
    raw = f"{model}:{persona}:{prompt.strip().lower()}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(prompt: str) -> Optional[str]:
    key = _cache_key(prompt, SETTINGS["model"], SETTINGS["persona"])
    if key not in _response_cache:
        return None
    val = _response_cache.pop(key)
    _response_cache[key] = val
    return val


def _cache_set(prompt: str, answer: str) -> None:
    if len(answer) < 20:
        return
    key = _cache_key(prompt, SETTINGS["model"], SETTINGS["persona"])
    if key in _response_cache:
        del _response_cache[key]
    _response_cache[key] = answer
    if len(_response_cache) > _CACHE_MAX:
        evict = _CACHE_MAX // 5
        for k in list(_response_cache.keys())[:evict]:
            del _response_cache[k]
    _save_cache()


_load_cache()


# =============================================================================
#  OLLAMA
# =============================================================================

def _get_ollama_client() -> ollama.Client:
    global _ollama_client
    if _ollama_client is None:
        host = getattr(config, "OLLAMA_HOST", "http://localhost:11434")
        _ollama_client = ollama.Client(host=host)
        try:
            _ollama_client.chat(
                model=SETTINGS["model"],
                messages=[{"role": "user", "content": "hi"}],
                options={"num_ctx": 4096},
                keep_alive="60m",
            )
            print(f"[INFO] Ollama '{SETTINGS['model']}' warmed ✓")
        except ollama.ResponseError as exc:
            print(f"[WARNING] Ollama warm-up: {exc}")
        except Exception as exc:
            print(f"[INFO] Warm-up skipped: {exc}")
    return _ollama_client


def reload_ollama_client() -> None:
    global _ollama_client
    _ollama_client = None
    threading.Thread(target=_get_ollama_client, daemon=True).start()


# =============================================================================
#  FASTER-WHISPER STT
# =============================================================================

_whisper_load_lock = threading.Lock()


def _get_whisper_model() -> Optional["FasterWhisperModel"]:
    global _whisper_model
    if not HAS_FASTER_WHISPER:
        return None
    # Use a lock so concurrent triggers don't double-load the model
    with _whisper_load_lock:
        if _whisper_model is None:
            size = SETTINGS.get("whisper_model", "base")
            try:
                print(f"[INFO] Loading faster-whisper '{size}'…")
                # int8 is the correct fast choice for CPU inference.
                # float16 is only faster on GPU; on CPU it's actually slower than int8
                # because most CPUs lack the AVX-512 FP16 instructions CTranslate2 needs.
                _whisper_model = FasterWhisperModel(size, device="cpu", compute_type="int8")
                print(f"[INFO] faster-whisper '{size}' loaded ✓")
            except Exception as exc:
                print(f"[WARNING] faster-whisper failed: {exc}")
    return _whisper_model


def _preload_whisper_model() -> None:
    """Warm up Whisper model AND run a dummy inference so CTranslate2's JIT
    compilation is done before the first real voice trigger.
    Without the dummy run the first transcription is still slow (~1-2s extra)
    even though the model weights are loaded, because operator kernels aren't
    compiled until the first forward pass."""
    if not HAS_FASTER_WHISPER:
        return

    def _load_and_warmup():
        model = _get_whisper_model()
        if model is None:
            return
        try:
            import struct, numpy as _np
            # Build the smallest valid WAV: 0.5s of silence at 16 kHz mono 16-bit
            sr = 16000
            n_samples = sr // 2                           # 0.5 seconds
            pcm = b"\x00\x00" * n_samples                 # silent PCM
            data_size = len(pcm)
            wav_header = struct.pack(
                "<4sI4s4sIHHIIHH4sI",
                b"RIFF", 36 + data_size, b"WAVE",
                b"fmt ", 16, 1, 1,
                sr, sr * 2, 2, 16,
                b"data", data_size,
            )
            dummy_wav = io.BytesIO(wav_header + pcm)
            # Run a real transcribe pass — this triggers CTranslate2 kernel JIT
            segs, _ = model.transcribe(dummy_wav, beam_size=1, language="en",
                                       without_timestamps=True,
                                       condition_on_previous_text=False)
            _ = list(segs)   # consume generator to complete the forward pass
            print("[INFO] faster-whisper warmed up ✓ (ready for instant transcription)")
        except Exception as exc:
            print(f"[INFO] faster-whisper warmup skipped: {exc}")

    threading.Thread(target=_load_and_warmup, daemon=True,
                     name="whisper-warmup").start()


# PERF: Suppress RuntimeWarning once at import time instead of on every transcription call
import warnings as _warnings_mod
_warnings_mod.filterwarnings("ignore", category=RuntimeWarning)


def transcribe_faster_whisper(audio_data: bytes) -> Optional[str]:
    model = _get_whisper_model()
    if model is None:
        return None
    if len(audio_data) < 500:
        # BUG FIX 3: Original threshold was 8000 bytes, which silently dropped short
        # utterances (< ~250 ms at 16 kHz 16-bit). Whisper's own vad_filter handles
        # silence/noise detection, so we only reject truly empty/corrupt data here.
        return None
    try:
        # PERF: Wrap bytes in BytesIO once — no temp file on disk
        audio_file = io.BytesIO(audio_data)

        segments, _ = model.transcribe(
            audio_file,
            beam_size=1,          # already minimal — greedy decoding
            language="en",        # skip language detection entirely
            vad_filter=True,
            vad_parameters=dict(
                min_silence_duration_ms=300,  # PERF: was 800ms — cuts segments sooner
                speech_pad_ms=100,            # PERF: was 500ms — less padding per segment
                threshold=0.5,               # PERF: standard VAD sensitivity
            ),
            without_timestamps=True,  # PERF: skip timestamp computation — not needed for STT
            word_timestamps=False,    # PERF: skip per-word alignment
            condition_on_previous_text=False,  # PERF: no inter-segment context needed
        )
        # PERF: consume the generator in one pass — no intermediate list
        text = "".join(s.text for s in segments).strip()
        return text or None
    except Exception as exc:
        log(f"faster-whisper: {exc}")
        return None


# =============================================================================
#  WEB SEARCH
# =============================================================================

def web_search(query: str, max_results: int = 5) -> str:
    if not HAS_DDG:
        return "[Web search unavailable — pip install duckduckgo-search]"
    try:
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                title = r.get("title", "")
                body  = r.get("body",  "")[:200]
                href  = r.get("href",  "")
                results.append(f"• {title}\n  {body}\n  {href}")
        return "\n\n".join(results) if results else "No results found."
    except Exception as exc:
        return f"Web search error: {exc}"


# =============================================================================
#  RAG
# =============================================================================

_rag_collection = None


def _get_rag_collection():
    global _rag_collection
    if not HAS_CHROMA:
        return None
    if _rag_collection is None:
        try:
            client = chromadb.PersistentClient(path=str(RAG_DB_DIR))
            _rag_collection = client.get_or_create_collection("docs")
        except Exception as exc:
            log(f"RAG init: {exc}")
    return _rag_collection


def rag_add_document(text: str, doc_id: str, metadata: Dict = None) -> bool:
    col = _get_rag_collection()
    if col is None:
        return False
    try:
        chunks = [text[i:i+500] for i in range(0, len(text), 400)]
        for i, chunk in enumerate(chunks):
            col.add(documents=[chunk], ids=[f"{doc_id}_{i}"],
                    metadatas=[metadata or {"source": doc_id}])
        return True
    except Exception as exc:
        log(f"RAG add: {exc}")
        return False


def rag_search(query: str, n: int = 4) -> str:
    global _rag_collection
    col = _get_rag_collection()
    if col is None:
        return ""
    try:
        count = col.count()
        if count == 0:
            return ""
        results = col.query(query_texts=[query], n_results=min(n, count))
        docs  = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        if not docs:
            return ""
        parts = []
        for doc, meta in zip(docs, metas):
            source = meta.get("source", "doc") if meta else "doc"
            parts.append(f"[{source}]\n{doc}")
        return "\n\n".join(parts)
    except Exception as exc:
        log(f"RAG search: {exc}")
        _rag_collection = None
        return ""


# =============================================================================
#  CODE SANDBOX
# =============================================================================

def extract_code_blocks(text: str) -> List[Tuple[str, str]]:
    import re
    matches = re.findall(r"```(\w*)\n(.*?)```", text, re.DOTALL)
    return [(lang or "python", code.strip()) for lang, code in matches]


def run_code_sandbox(code: str, timeout: int = 15) -> str:
    import os as _os
    safe_env = {k: v for k, v in _os.environ.items()
                if k in ("PATH","HOME","TERM","LANG","LC_ALL","PYTHONPATH","TMPDIR","TMP","TEMP")}
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout, env=safe_env,
        )
        out = result.stdout.strip()
        err = result.stderr.strip()
        if out and err:
            return f"Output:\n{out}\n\nErrors:\n{err}"
        return out or err or "(no output)"
    except subprocess.TimeoutExpired:
        return f"⏱ Code execution timed out after {timeout}s"
    except Exception as exc:
        return f"Execution error: {exc}"


# =============================================================================
#  VISION
# =============================================================================

def capture_screenshot_b64() -> Optional[str]:
    try:
        if HAS_OCR:
            img = ImageGrab.grab()
        else:
            screen = QApplication.primaryScreen()
            qpix   = screen.grabWindow(0)
            # FIX-D: two explicit steps — walrus inside save() created a second
            # BytesIO so buf still pointed at the original empty object
            buf = io.BytesIO()
            qpix.save(buf, "JPEG", quality=60)
            return base64.b64encode(buf.getvalue()).decode()

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        log(f"screenshot: {exc}")
        return None


def ask_vision(question: str, image_b64: str) -> str:
    try:
        client = _get_ollama_client()
        resp = client.chat(
            model=SETTINGS.get("vision_model", "llava"),
            messages=[{"role": "user", "content": question, "images": [image_b64]}],
            options={"num_ctx": 2048},
        )
        return resp.message.content.strip()
    except Exception as exc:
        return f"Vision error: {exc}"


# =============================================================================
#  OCR
# =============================================================================

def ocr_clipboard_image() -> Optional[str]:
    if not HAS_OCR:
        return None
    try:
        cb   = QApplication.clipboard()
        qimg = cb.image()
        if qimg.isNull():
            return None
        # FIX-D: same walrus double-buf bug as capture_screenshot_b64
        buf = io.BytesIO()
        qimg.save(buf, "PNG")
        pil_img = Image.open(io.BytesIO(buf.getvalue()))
        text = pytesseract.image_to_string(pil_img).strip()
        return text if len(text) > 3 else None
    except Exception as exc:
        log(f"OCR: {exc}")
        return None


# =============================================================================
#  TTS
# =============================================================================

def speak(text: str) -> None:
    if _tts_muted or not text or not _HAS_TTS:
        return
    if _rendering_event.is_set():
        cleared = _rendering_event.wait(timeout=5.0)
        if not cleared:
            log("speak(): rendering_event timeout — speaking anyway")
    if _tts_muted:
        return
    engine = SETTINGS.get("tts_engine", "pyttsx3")
    if engine == "elevenlabs":
        _speak_elevenlabs(text)
    elif engine == "coqui":
        _speak_coqui(text)
    else:
        _speak_pyttsx3(text)


def _speak_pyttsx3(text: str) -> None:
    if not _HAS_TTS:
        return
    if _IS_MACOS:
        # On macOS / Apple M1: pyttsx3 shares an NSRunLoop that crashes when called
        # from a background thread after the Qt event loop has started.
        # Use the built-in 'say' command instead — it is always available on macOS,
        # requires no extra deps, and works correctly from any thread.
        try:
            rate = SETTINGS.get("tts_rate", 175)
            # 'say' rate is words-per-minute; map from pyttsx3 range (50-350) → say range
            subprocess.Popen(
                ["say", "-r", str(rate), text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return
        except Exception as exc:
            log(f"macOS say: {exc}")
        # Fallback: fresh pyttsx3 engine per call (slower but functional)
        try:
            with _tts_lock:
                eng = _init_tts_engine()
                eng.say(text)
                eng.runAndWait()
                eng.stop()
        except Exception as exc:
            log(f"pyttsx3 macOS fallback: {exc}")
        return

    # Non-macOS: use shared engine
    if _tts_engine is None:
        return
    with _tts_lock:
        try:
            _tts_engine.say(text)
            _tts_engine.runAndWait()
        except RuntimeError as exc:
            log(f"pyttsx3 runloop: {exc}")
        except Exception as exc:
            log(f"pyttsx3: {exc}")


def _speak_elevenlabs(text: str) -> None:
    key = SETTINGS.get("elevenlabs_key", "")
    if not key:
        _speak_pyttsx3(text)
        return
    try:
        voice = SETTINGS.get("elevenlabs_voice", "Rachel")
        url   = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
        data  = json.dumps({
            "text": text, "model_id": "eleven_monolingual_v1",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"xi-api-key": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            audio = resp.read()
        # FIX-E: tmp must be assigned inside the with-block before file closes
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio)
            tmp = f.name
        os.system(f"afplay {tmp} 2>/dev/null || mpg123 {tmp} 2>/dev/null")
        os.unlink(tmp)
    except Exception as exc:
        log(f"ElevenLabs TTS: {exc}")
        _speak_pyttsx3(text)


def _speak_coqui(text: str) -> None:
    try:
        from TTS.api import TTS as CoquiTTS  # type: ignore
        tts = CoquiTTS("tts_models/en/ljspeech/tacotron2-DDC")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            tmp = f.name
        tts.tts_to_file(text=text, file_path=tmp)
        os.system(f"afplay {tmp} 2>/dev/null || aplay {tmp} 2>/dev/null")
        os.unlink(tmp)
    except Exception as exc:
        log(f"Coqui TTS: {exc}")
        _speak_pyttsx3(text)


def _get_microphone() -> "sr.Microphone":
    """Return sr.Microphone with an explicit device index.
    On macOS/M1 the default index=-1 causes a PortAudio crash."""
    if not _IS_MACOS:
        return sr.Microphone()
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        device_index = None
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info.get("maxInputChannels", 0) > 0:
                device_index = i
                break
        pa.terminate()
        if device_index is not None:
            return sr.Microphone(device_index=device_index)
    except Exception as exc:
        log(f"_get_microphone device scan: {exc}")
    return sr.Microphone()


def _record_with_sounddevice(duration_max: int = 60,
                              silence_sec: float = 0.8,
                              samplerate: int = 16000,
                              stop_event: Optional[threading.Event] = None,
                              manual_mode: bool = False) -> Optional[bytes]:
    """
    Record audio using sounddevice.

    manual_mode=False (auto/silence-detection):
        Records until silence_sec of quiet after speech, or duration_max.

    manual_mode=True (Push-to-Talk / full manual control):
        Records until stop_event is set by the caller (button second-click).
        Silence detection is disabled — every frame is kept regardless of RMS.

    Returns WAV bytes (header + PCM), or None on error.
    """
    try:
        import sounddevice as sd
        import numpy as np
    except ImportError:
        return None

    CHUNK      = int(samplerate * 0.05)  # 50 ms frames
    SILENCE_RMS = 400
    effective_silence     = min(silence_sec, 0.8)
    silence_chunks_needed = max(4, int(effective_silence / 0.05))

    frames: list      = []
    silent_count: int = 0
    got_speech: bool  = False

    # FIX: clear stop_event here inside the recorder so it is guaranteed fresh
    # even if the caller forgot, and the check is AFTER the read so we always
    # capture at least one frame before honouring a stop request.
    if manual_mode and stop_event:
        stop_event.clear()

    try:
        with sd.InputStream(samplerate=samplerate, channels=1,
                            dtype="int16", blocksize=CHUNK) as stream:
            deadline = time.time() + duration_max
            while time.time() < deadline:
                data, _ = stream.read(CHUNK)
                pcm = data[:, 0]
                frames.append(pcm.tobytes())

                # Manual PTT: check AFTER reading so we always capture the frame
                # that was in the buffer when the user clicked stop.
                if manual_mode and stop_event and stop_event.is_set():
                    break

                # Emit live RMS level for UI feedback (manual mode only)
                rms = int(np.sqrt(np.mean(pcm.astype(np.int32) ** 2)))
                if manual_mode and _overlay_window:
                    level = min(100, int(rms / 327))  # 0-32767 → 0-100
                    _overlay_window.communicate.ptt_level.emit(level)

                if not manual_mode:
                    # Auto mode: stop after silence following speech
                    if rms > SILENCE_RMS:
                        got_speech   = True
                        silent_count = 0
                    elif got_speech:
                        silent_count += 1
                        if silent_count >= silence_chunks_needed:
                            break

        if not frames:
            return None
        return _pcm_to_wav(b"".join(frames), samplerate=samplerate)

    except Exception as exc:
        log(f"sounddevice record: {exc}")
        return None


def _pcm_to_wav(pcm_bytes: bytes, samplerate: int = 16000) -> bytes:
    """Wrap raw 16-bit mono PCM in a minimal WAV header."""
    import struct
    num_samples  = len(pcm_bytes) // 2
    byte_rate    = samplerate * 2          # 1 channel × 16-bit
    data_size    = len(pcm_bytes)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1,                 # PCM, mono
        samplerate, byte_rate, 2, 16,      # sample rate, byte rate, block align, bits
        b"data", data_size,
    )
    return header + pcm_bytes


# =============================================================================
#  STT
# =============================================================================

def listen_once(timeout: float) -> Optional[str]:
    # FIX: use the passed `timeout` argument directly.
    # Previously the function ignored its parameter and re-read from SETTINGS,
    # making the parameter useless and preventing callers from overriding the timeout.
    listen_timeout = timeout if timeout and timeout > 0 else SETTINGS.get("listen_timeout_sec", 60)

    # Give more time between words before considering speech "done"
    _orig_pause  = recognizer.pause_threshold
    _orig_phrase = recognizer.phrase_threshold
    recognizer.pause_threshold        = SETTINGS.get("pause_threshold_sec", 1.5)
    recognizer.phrase_threshold       = 0.1
    recognizer.non_speaking_duration  = SETTINGS.get("pause_threshold_sec", 1.5)
    recognizer.energy_threshold       = 300
    recognizer.dynamic_energy_threshold = True

    # BUG FIX 2: Wrap the ENTIRE function body (including macOS early-return path)
    # inside the try/finally so recognizer thresholds are ALWAYS restored.
    # Previously the macOS sounddevice path returned before the finally block ran,
    # leaving pause_threshold/phrase_threshold permanently polluted for future calls.
    try:
        if _overlay_window:
            _overlay_window.set_status("🎙 Listening… (speak your question)", "#ffaa00")

        # ── M1 / macOS: use sounddevice — bypasses PyAudio/PortAudio entirely ──
        if _IS_MACOS:
            try:
                import sounddevice  # noqa — just test importability
                wav = _record_with_sounddevice(
                    duration_max=listen_timeout,
                    silence_sec=recognizer.pause_threshold,
                    samplerate=16000,
                    manual_mode=False,
                )
                if not wav:
                    if _overlay_window:
                        _overlay_window.set_status("Ready", "#00c864")
                    return None  # finally block still runs ✓

                if _overlay_window:
                    _overlay_window.set_status("Transcribing…", "#00aaff")
                return _transcribe_bytes(wav, sample_rate=16000, sample_width=2)  # finally runs ✓

            except ImportError:
                print("[WARNING] sounddevice not installed — falling back to PyAudio.\n"
                      "  Install: pip install sounddevice")
                # Fall through to PyAudio path below

        # ── Non-M1 / PyAudio fallback path ────────────────────────────────────
        sample_rate  = 16000
        sample_width = 2

        # BUG FIX 5: Read phrase_time_limit from settings and pass it to
        # recognizer.listen(). Previously the setting existed and had a UI slider
        # but was never actually used, so the slider had no effect.
        phrase_limit = SETTINGS.get("phrase_time_limit_sec", 0) or None

        try:
            mic = _get_microphone()
        except OSError as exc:
            print(f"[ERROR] Microphone not found: {exc}\n"
                  "  macOS: System Settings → Privacy & Security → Microphone → allow Terminal/Python")
            if _overlay_window:
                _overlay_window.set_status("No mic — check Privacy settings", "#ff4444")
            return None

        with mic as source:
            # FIX 4: Cache ambient noise calibration — was blocking 500ms on EVERY
            # listen call. Now recalibrated only on first call and every 5 minutes,
            # or immediately if the recognizer's energy has drifted >50% from cache.
            global _ambient_energy_cache, _ambient_energy_ts
            now = time.time()
            needs_recal = (
                _ambient_energy_cache is None
                or (now - _ambient_energy_ts) > _AMBIENT_CACHE_TTL
            )
            if needs_recal:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                _ambient_energy_cache = recognizer.energy_threshold
                _ambient_energy_ts    = now
            else:
                recognizer.energy_threshold = _ambient_energy_cache

            sample_rate  = source.SAMPLE_RATE
            sample_width = source.SAMPLE_WIDTH

            # FIX 5: The previous code looped calling recognizer.listen() multiple
            # times and concatenated chunks. This was wrong — recognizer.listen()
            # already handles pause detection internally and returns a complete phrase.
            # The loop caused doubled/repeated audio being fed to the transcriber.
            # One listen() call with the full timeout is the correct pattern.
            try:
                audio = recognizer.listen(source, timeout=listen_timeout,
                                          phrase_time_limit=phrase_limit)
                full_audio = audio.get_wav_data()
                sample_rate  = source.SAMPLE_RATE
                sample_width = source.SAMPLE_WIDTH
            except sr.WaitTimeoutError:
                if _overlay_window:
                    _overlay_window.set_status("Ready", "#00c864")
                return None

        if _overlay_window:
            _overlay_window.set_status("Transcribing…", "#00aaff")
        return _transcribe_bytes(full_audio, sample_rate=sample_rate,
                                 sample_width=sample_width)

    except sr.UnknownValueError:
        if _overlay_window:
            _overlay_window.set_status("Ready", "#00c864")
        return None
    except Exception as exc:
        log(f"listen_once: {exc}")
        print(f"[ERROR] listen_once: {exc}")
        if _overlay_window:
            _overlay_window.set_status(f"Mic error — {exc}", "#ff4444")
        return None
    finally:
        # BUG FIX 2 (cont): This finally block now ALWAYS runs regardless of which
        # code path was taken above (macOS sounddevice, macOS fallback, or PyAudio).
        recognizer.pause_threshold  = _orig_pause
        recognizer.phrase_threshold = _orig_phrase



# =============================================================================
#  PUSH-TO-TALK: manual recording, stops when stop_event is set
# =============================================================================

# Global stop event for PTT mode — set by second mic-button click
_ptt_stop_event = threading.Event()


def listen_ptt() -> Optional[str]:
    """Record until _ptt_stop_event is set, then transcribe.
    The stop event is cleared inside _record_with_sounddevice so it is
    guaranteed fresh before the first read regardless of prior state."""
    listen_timeout = SETTINGS.get("listen_timeout_sec", 60)

    if _overlay_window:
        _overlay_window.communicate.update_status.emit(
            "🔴 Recording… click mic again to stop", "#e53e3e")

    # Prefer sounddevice (cross-platform, lower latency)
    try:
        import sounddevice  # noqa
        wav = _record_with_sounddevice(
            duration_max=listen_timeout,
            samplerate=16000,
            stop_event=_ptt_stop_event,
            manual_mode=True,
        )
    except ImportError:
        # PyAudio fallback for PTT — record in chunks until stop_event
        wav = _record_pyaudio_ptt(listen_timeout)

    if not wav:
        if _overlay_window:
            _overlay_window.set_status("Ready", "#00c864")
        return None

    if _overlay_window:
        _overlay_window.set_status("Transcribing…", "#00aaff")

    return _transcribe_bytes(wav, sample_rate=16000, sample_width=2)


def _record_pyaudio_ptt(duration_max: int = 60,
                         samplerate: int = 16000) -> Optional[bytes]:
    """PyAudio fallback for PTT: records chunks until _ptt_stop_event is set."""
    try:
        mic = _get_microphone()
    except OSError:
        return None
    import wave, io as _io
    frames = []
    CHUNK  = 1024
    try:
        import pyaudio
        pa     = pyaudio.PyAudio()
        stream = pa.open(rate=samplerate, channels=1,
                         format=pyaudio.paInt16, input=True,
                         frames_per_buffer=CHUNK)
        deadline = time.time() + duration_max
        while time.time() < deadline and not _ptt_stop_event.is_set():
            frames.append(stream.read(CHUNK, exception_on_overflow=False))
        stream.stop_stream(); stream.close(); pa.terminate()
        if not frames:
            return None
        buf = _io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(samplerate)
            wf.writeframes(b"".join(frames))
        return buf.getvalue()
    except Exception as exc:
        log(f"pyaudio PTT: {exc}")
        return None


# =============================================================================
#  AUTO-LISTEN LOOP: VAD-driven, runs continuously until toggled off
# =============================================================================

_auto_listen_active  = threading.Event()   # set = auto-listen is running
_auto_listen_thread: Optional[threading.Thread] = None


def start_auto_listen() -> None:
    """Start the continuous auto-listen loop in a background thread."""
    global _auto_listen_thread
    if _auto_listen_active.is_set():
        return
    _auto_listen_active.set()
    _auto_listen_thread = threading.Thread(
        target=_auto_listen_loop, daemon=True, name="auto-listen")
    _auto_listen_thread.start()
    print("[INFO] Auto-listen started")


def stop_auto_listen() -> None:
    """Stop the continuous auto-listen loop."""
    _auto_listen_active.clear()
    print("[INFO] Auto-listen stopped")


def _auto_listen_loop() -> None:
    """
    Continuously monitors the microphone using VAD (webrtcvad if available,
    else RMS-threshold fallback).  Each detected utterance is transcribed and
    placed in the input box. The loop runs until _auto_listen_active is cleared.
    """
    SR    = 16000
    CHUNK = 480   # 30 ms @ 16 kHz — required by webrtcvad

    # Try webrtcvad first; fall back to RMS-threshold detection
    vad = None
    if HAS_VAD:
        try:
            vad = webrtcvad.Vad(2)   # aggressiveness 0-3
        except Exception:
            vad = None

    # Prefer sounddevice for cross-platform support
    use_sd = False
    try:
        import sounddevice as _sd_test  # noqa
        use_sd = True
    except ImportError:
        pass

    if use_sd:
        _auto_listen_loop_sd(SR, CHUNK, vad)
    else:
        _auto_listen_loop_pyaudio(SR, CHUNK, vad)


def _is_speech_chunk(chunk_bytes: bytes, vad, sr: int) -> bool:
    """Return True if the 30-ms chunk contains speech."""
    if vad:
        try:
            return vad.is_speech(chunk_bytes, sr)
        except Exception:
            pass
    # RMS fallback
    import struct
    samples = struct.unpack(f"{len(chunk_bytes)//2}h", chunk_bytes)
    rms = (sum(s*s for s in samples) / len(samples)) ** 0.5
    return rms > 400


def _auto_listen_loop_sd(sr: int, chunk_size: int, vad) -> None:
    """sounddevice-based auto-listen loop."""
    try:
        import sounddevice as sd
        import numpy as np
    except ImportError:
        _auto_listen_loop_pyaudio(sr, chunk_size, vad)
        return

    SILENCE_CHUNKS = 16   # ~480 ms silence ends an utterance

    if _overlay_window:
        _overlay_window.communicate.update_status.emit(
            "🟢 Auto-listening…", "#00c864")

    frames: list      = []
    silent_cnt: int   = 0
    speaking: bool    = False

    try:
        with sd.InputStream(samplerate=sr, channels=1,
                            dtype="int16", blocksize=chunk_size) as stream:
            while _auto_listen_active.is_set() and not shutdown_event.is_set():
                data, _ = stream.read(chunk_size)
                pcm_bytes = data[:, 0].tobytes()
                speech    = _is_speech_chunk(pcm_bytes, vad, sr)

                if speech:
                    frames.append(pcm_bytes)
                    speaking   = True
                    silent_cnt = 0
                elif speaking:
                    frames.append(pcm_bytes)
                    silent_cnt += 1
                    if silent_cnt >= SILENCE_CHUNKS:
                        # Utterance ended — transcribe in a side thread
                        payload = _pcm_to_wav(b"".join(frames), samplerate=sr)
                        frames      = []
                        speaking    = False
                        silent_cnt  = 0
                        threading.Thread(
                            target=_auto_dispatch,
                            args=(payload,),
                            daemon=True,
                        ).start()

    except Exception as exc:
        log(f"auto-listen (sd): {exc}")
    finally:
        _auto_listen_active.clear()
        if _overlay_window:
            _overlay_window.communicate.update_status.emit("Ready", "#00c864")
            _overlay_window.communicate.auto_listen_changed.emit(False)


def _auto_listen_loop_pyaudio(sr: int, chunk_size: int, vad) -> None:
    """PyAudio-based auto-listen loop (fallback)."""
    try:
        import pyaudio
    except ImportError:
        log("auto-listen: neither sounddevice nor pyaudio available")
        _auto_listen_active.clear()
        return

    SILENCE_CHUNKS = 16

    if _overlay_window:
        _overlay_window.communicate.update_status.emit(
            "🟢 Auto-listening…", "#00c864")

    pa     = pyaudio.PyAudio()
    stream = pa.open(rate=sr, channels=1, format=pyaudio.paInt16,
                     input=True, frames_per_buffer=chunk_size)

    frames: list     = []
    silent_cnt: int  = 0
    speaking: bool   = False

    try:
        while _auto_listen_active.is_set() and not shutdown_event.is_set():
            pcm_bytes = stream.read(chunk_size, exception_on_overflow=False)
            speech    = _is_speech_chunk(pcm_bytes, vad, sr)

            if speech:
                frames.append(pcm_bytes)
                speaking   = True
                silent_cnt = 0
            elif speaking:
                frames.append(pcm_bytes)
                silent_cnt += 1
                if silent_cnt >= SILENCE_CHUNKS:
                    payload = _pcm_to_wav(b"".join(frames), samplerate=sr)
                    frames      = []
                    speaking    = False
                    silent_cnt  = 0
                    threading.Thread(
                        target=_auto_dispatch,
                        args=(payload,),
                        daemon=True,
                    ).start()
    except Exception as exc:
        log(f"auto-listen (pa): {exc}")
    finally:
        try:
            stream.stop_stream(); stream.close(); pa.terminate()
        except Exception:
            pass
        _auto_listen_active.clear()
        if _overlay_window:
            _overlay_window.communicate.update_status.emit("Ready", "#00c864")
            _overlay_window.communicate.auto_listen_changed.emit(False)


def _auto_dispatch(wav_bytes: bytes) -> None:
    """Transcribe one utterance captured by auto-listen and deliver to UI."""
    text = _transcribe_bytes(wav_bytes, sample_rate=16000, sample_width=2)
    if text and len(text.strip()) > 1:
        if _overlay_window:
            _overlay_window.set_input(text)
            _overlay_window.set_status(
                "✏  Review & press Enter to send", "#ffcc00")
        else:
            command_queue.put(text)


def _transcribe_bytes(wav_bytes: bytes,
                      sample_rate: int = 16000,
                      sample_width: int = 2) -> Optional[str]:
    """Transcribe raw WAV bytes using the configured STT engine."""
    engine = SETTINGS.get("stt_engine", "faster_whisper")

    if engine == "faster_whisper" and HAS_FASTER_WHISPER:
        return transcribe_faster_whisper(wav_bytes)

    if engine == "google" or (engine == "faster_whisper" and not HAS_FASTER_WHISPER):
        try:
            audio_data = sr.AudioData(wav_bytes, sample_rate, sample_width)
            text = recognizer.recognize_google(audio_data)
            return text.strip() or None
        except sr.UnknownValueError:
            return None
        except Exception as exc:
            log(f"Google STT: {exc}")
            return None

    if engine == "vosk":
        model_path = getattr(config, "VOSK_MODEL_PATH", "")
        if not model_path:
            return None
        try:
            audio_data = sr.AudioData(wav_bytes, sample_rate, sample_width)
            raw = recognizer.recognize_vosk(audio_data, model=model_path)
            return json.loads(raw).get("text", "").strip() or None
        except Exception as exc:
            log(f"Vosk STT: {exc}")
            return None

    return None



# =============================================================================
#  CONTINUOUS LISTENING
# =============================================================================

def continuous_listen_loop() -> None:
    """Legacy entry point kept for startup compatibility.
    Now delegates to the unified start_auto_listen() so there is one
    implementation for both the settings checkbox and the UI button."""
    start_auto_listen()


# =============================================================================
#  PLUGIN SYSTEM
# =============================================================================

_plugins: Dict[str, Any] = {}


def load_plugins() -> None:
    for path in PLUGINS_DIR.glob("*.py"):
        try:
            spec   = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "COMMANDS"):
                for cmd, fn in module.COMMANDS.items():
                    _plugins[cmd] = fn
                    print(f"[Plugin] Loaded /{cmd} from {path.name}")
        except Exception as exc:
            print(f"[Plugin] Failed {path.name}: {exc}")


load_plugins()


# =============================================================================
#  REST API
# =============================================================================

def start_rest_api(port: int) -> None:
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def do_POST(self):
            if self.path != "/ask":
                self.send_response(404); self.end_headers(); return
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length))
            prompt = body.get("prompt", "").strip()
            if not prompt:
                self.send_response(400); self.end_headers(); return

            result_q: queue.Queue = queue.Queue()

            def _ask():
                try:
                    ans = ask_ai_streaming(prompt, _sink=result_q)
                    result_q.put(("done", ans))
                except Exception as exc:
                    result_q.put(("error", str(exc)))

            threading.Thread(target=_ask, daemon=True).start()
            _, answer = result_q.get(timeout=60)
            resp = json.dumps({"answer": answer}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    print(f"[REST] API at http://127.0.0.1:{port}/ask")
    server.serve_forever()


# =============================================================================
#  ACTIVE WINDOW DETECTION
# =============================================================================

def get_active_window_title() -> str:
    try:
        if platform.system() == "Darwin":
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of first process whose frontmost is true'],
                capture_output=True, text=True, timeout=2)
            return r.stdout.strip()
        elif platform.system() == "Windows":
            import ctypes
            buf = ctypes.create_unicode_buffer(512)
            ctypes.windll.user32.GetWindowTextW(
                ctypes.windll.user32.GetForegroundWindow(), buf, 512)
            return buf.value
        else:
            r = subprocess.run(["xdotool", "getactivewindow", "getwindowname"],
                               capture_output=True, text=True, timeout=2)
            return r.stdout.strip()
    except Exception:
        return ""


def auto_switch_persona(window_title: str) -> None:
    title = window_title.lower()
    if any(k in title for k in ["vscode", "code", "pycharm", "intellij", "vim"]):
        suggested = "coder"
    elif any(k in title for k in ["terminal", "iterm", "bash", "zsh"]):
        suggested = "coder"
    elif any(k in title for k in ["zoom", "teams", "meet", "interview"]):
        suggested = "interviewer"
    else:
        return
    if SETTINGS["persona"] != suggested:
        SETTINGS["persona"] = suggested
        save_settings(SETTINGS)
        if _overlay_window:
            _overlay_window.set_status(f"Persona → {suggested}", "#ffaa00")
            try:
                _overlay_window.model_indicator.setText(
                    f"{SETTINGS['model']}  ·  {suggested}")
            except Exception:
                pass


# =============================================================================
#  MARKDOWN + SYNTAX HIGHLIGHT
# =============================================================================

def render_markdown(text: str) -> str:
    if not HAS_MARKDOWN:
        return f"<pre style='color:#00c864;white-space:pre-wrap;'>{html.escape(text)}</pre>"

    if HAS_PYGMENTS:
        import re
        def highlight_block(m: re.Match) -> str:
            lang = m.group(1) or "text"
            code = m.group(2)
            try:
                lexer = get_lexer_by_name(lang, stripall=True)
            except Exception:
                lexer = guess_lexer(code)
            formatter = HtmlFormatter(
                style="monokai", noclasses=True,
                prestyles="background:#1a1a1a;border-radius:6px;padding:10px;"
                          "margin:6px 0;overflow-x:auto;font-size:12px;"
            )
            return highlight(code, lexer, formatter)
        text = re.sub(r"```(\w*)\n(.*?)```", highlight_block, text, flags=re.DOTALL)

    md_html = markdown2.markdown(
        text,
        extras=["fenced-code-blocks", "tables", "strike", "task_list"],
    )

    return f"""
    <style>
        body      {{ color:#c8c8c8; font-family:'Menlo',monospace; font-size:13px;
                     line-height:1.6; background:transparent; margin:0; padding:4px; }}
        h1,h2,h3  {{ color:#00c864; margin:8px 0 4px; }}
        code       {{ background:#1e1e1e; color:#98d890; padding:1px 4px;
                     border-radius:3px; font-size:12px; }}
        pre        {{ background:#1a1a1a; padding:8px; border-radius:6px;
                     overflow-x:auto; border-left:3px solid #00c864; }}
        blockquote {{ border-left:3px solid #444; margin:4px 0; padding-left:8px; color:#888; }}
        table      {{ border-collapse:collapse; width:100%; margin:6px 0; }}
        th,td      {{ border:1px solid #333; padding:4px 8px; }}
        th         {{ background:#1e1e1e; color:#00c864; }}
        a          {{ color:#4ab8ff; }}
        li         {{ margin:2px 0; }}
        strong     {{ color:#ffffff; }}
        em         {{ color:#aaaaff; }}
    </style>
    {md_html}
    """


# =============================================================================
#  FOLLOW-UP SUGGESTIONS
# =============================================================================

def generate_followups(prompt: str, answer: str) -> List[str]:
    try:
        client  = _get_ollama_client()
        payload = (
            f"Given this Q&A, suggest exactly 3 short follow-up questions "
            f"(one per line, no numbering, max 8 words each):\n\n"
            f"Q: {prompt[:200]}\nA: {answer[:400]}"
        )
        resp = client.chat(
            model=SETTINGS["model"],
            messages=[{"role": "user", "content": payload}],
            options={"num_ctx": 1024, "num_predict": 80, "temperature": 0.7},
            keep_alive="60m",
        )
        lines = [l.strip().strip("•-123456789. ") for l in
                 resp.message.content.strip().split("\n") if l.strip()]
        return lines[:3]
    except Exception:
        return []


# =============================================================================
#  PINS & RATINGS
# =============================================================================

def load_pins() -> List[Dict]:
    try:
        if PINS_FILE.exists():
            return json.loads(PINS_FILE.read_text())
    except Exception:
        pass
    return []


def save_pin(prompt: str, answer: str) -> None:
    pins = load_pins()
    pins.append({"timestamp": datetime.now().isoformat(), "prompt": prompt, "answer": answer})
    PINS_FILE.write_text(json.dumps(pins, indent=2))


def save_rating(prompt: str, answer: str, rating: int) -> None:
    try:
        ratings = json.loads(RATINGS_FILE.read_text()) if RATINGS_FILE.exists() else []
        ratings.append({"timestamp": datetime.now().isoformat(), "rating": rating,
                         "prompt": prompt, "answer": answer[:300]})
        RATINGS_FILE.write_text(json.dumps(ratings, indent=2))
    except Exception as exc:
        log(f"save_rating: {exc}")


# =============================================================================
#  MAIN AI CALL
# =============================================================================

def ask_ai_streaming(prompt: str, _sink: Optional[queue.Queue] = None) -> str:
    global _session_tokens

    cached = _cache_get(prompt)
    if cached:
        log(f"Cache hit: {prompt[:50]}")
        if _overlay_window:
            _overlay_window.set_response(cached)
            _overlay_window.set_status("Ready (cached ⚡)", "#00c864")
        return cached

    for cmd, fn in _plugins.items():
        if prompt.strip().lower().startswith(f"/{cmd}"):
            rest = prompt[len(cmd)+1:].strip()
            try:
                result = fn(rest)
                if _overlay_window:
                    _overlay_window.set_response(str(result))
                return str(result)
            except Exception as exc:
                return f"Plugin /{cmd} error: {exc}"

    lower = prompt.lower().strip()

    if lower.startswith("/search ") or lower.startswith("/search\n"):
        query = prompt[8:].strip()
        if _overlay_window:
            _overlay_window.set_status("Searching web…", "#00aaff")
        results = web_search(query)
        prompt  = (f"Based on these web search results, answer: {query}\n\n"
                   f"Search results:\n{results}")

    elif lower.startswith("/vision") or lower.startswith("/screen"):
        if _overlay_window:
            _overlay_window.set_status("Capturing screen…", "#aa44ff")
        question = prompt.split(None, 1)[1] if " " in prompt else "What do you see?"
        img_b64  = capture_screenshot_b64()
        if img_b64:
            answer = ask_vision(question, img_b64)
            if _overlay_window:
                _overlay_window.set_response(answer)
            _cache_set(prompt, answer)
            return answer
        return "Could not capture screenshot."

    elif lower.startswith("/rag "):
        query   = prompt[5:].strip()
        context = rag_search(query)
        prompt  = (
            f"Using the following document excerpts, answer: {query}\n\nContext:\n{context}"
            if context else
            f"No documents found for: {query}. Answer from general knowledge: {query}"
        )

    elif lower.startswith("/run "):
        code_prompt = prompt[5:].strip()
        prompt = f"Write Python code to accomplish this task. Only output the code:\n{code_prompt}"

    elif SETTINGS.get("web_search_enabled") and HAS_DDG:
        current_keywords = ["today", "right now", "current price", "stock price",
                            "breaking news", "latest news", "weather today",
                            "what happened", "who won", "score today"]
        if any(k in lower for k in current_keywords):
            try:
                results = web_search(prompt, max_results=3)
                prompt  = (f"Using real-time search results, answer: {prompt}\n\n"
                           f"Web results:\n{results}")
            except Exception:
                pass

    if SETTINGS.get("rag_enabled") and HAS_CHROMA:
        context = rag_search(prompt)
        if context:
            prompt = f"Context from documents:\n{context}\n\nQuestion: {prompt}"

    system   = PERSONAS.get(SETTINGS.get("persona", "default"), "")
    messages: List[Dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(_conv_history[-20:])
    messages.append({"role": "user", "content": prompt})

    if _overlay_window:
        _overlay_window.set_status("Thinking…", "#aa44ff")

    if SETTINGS["ai_engine"] == "ollama":
        try:
            client = _get_ollama_client()
            full   = []
            # NOTE: do NOT clear _generation_stop_event here — it is cleared by
            # process_commands AFTER the response finishes. Clearing it here would
            # race with a Stop press that happened between queue.put() and this line.

            stream = client.chat(
                model=SETTINGS["model"], messages=messages,
                options={"num_ctx": 4096, "num_predict": 1024, "temperature": 0.7},
                keep_alive="60m", stream=True,
            )
            try:
                for chunk in stream:
                    if _generation_stop_event.is_set():
                        log("Generation stopped by user")
                        if _overlay_window:
                            _overlay_window.communicate.append_token.emit("\n\n*⏹ Stopped.*")
                        # Close the stream to immediately cancel the HTTP request
                        try:
                            stream.response.close()
                        except Exception:
                            pass
                        break
                    token = chunk.message.content or ""
                    if token:
                        full.append(token)
                        if _overlay_window:
                            _overlay_window.append_token(token)
                        if _sink:
                            _sink.put(("token", token))
            finally:
                # Always ensure stop button is reset even if an exception occurs mid-stream
                if _overlay_window:
                    _overlay_window.communicate.set_stop_btn.emit(0)

            answer = "".join(full).strip() or "Empty response."
            _session_tokens += len(prompt) // 4 + len(answer) // 4
            if _overlay_window:
                _overlay_window.update_tokens(_session_tokens)

            if lower.startswith("/run "):
                blocks = extract_code_blocks(answer)
                if blocks:
                    _, code  = blocks[0]
                    exec_out = run_code_sandbox(code)
                    answer   = f"{answer}\n\n**Execution Output:**\n```\n{exec_out}\n```"
                    if _overlay_window:
                        _overlay_window.set_response(answer)

            _cache_set(prompt.split("\nQuestion:")[-1].strip(), answer)
            return answer

        except ollama.ResponseError as exc:
            m = f"Ollama error: {exc}. Run: ollama pull {SETTINGS['model']}"
            log(m); return m
        except ConnectionRefusedError:
            m = "Ollama not running. Run: ollama serve"; log(m); return m
        except Exception as exc:
            m = f"Ollama error: {exc}"; log(m); return m

    if SETTINGS["ai_engine"] == "openai":
        api_key = getattr(config, "OPENAI_API_KEY", "")
        if not api_key:
            return "OPENAI_API_KEY missing."
        payload = {"model": getattr(config, "OPENAI_MODEL", "gpt-4"), "messages": messages}
        req = urllib.request.Request(
            url=f"{getattr(config,'OPENAI_BASE_URL','https://api.openai.com/v1').rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read().decode())
            _session_tokens += body.get("usage", {}).get("total_tokens", 0)
            if _overlay_window:
                _overlay_window.update_tokens(_session_tokens)
            answer = (body.get("choices", [{}])[0]
                          .get("message", {}).get("content", "").strip()
                      or "OpenAI returned no content.")
            if _overlay_window:
                _overlay_window.set_response(answer)
            _cache_set(prompt, answer)
            return answer
        except Exception as exc:
            return f"OpenAI error: {exc}"

    return f"Unsupported AI_ENGINE: '{SETTINGS['ai_engine']}'."


# =============================================================================
#  COMMAND PROCESSOR
# =============================================================================

def process_commands() -> None:
    log("Command processor started.")
    if SETTINGS["ai_engine"] == "ollama":
        threading.Thread(target=_get_ollama_client, daemon=True).start()
    if HAS_FASTER_WHISPER and SETTINGS.get("stt_engine") == "faster_whisper":
        threading.Thread(target=_get_whisper_model, daemon=True).start()

    while not shutdown_event.is_set():
        try:
            prompt = command_queue.get(timeout=0.25)
        except queue.Empty:
            continue
        if not prompt:
            command_queue.task_done()
            continue

        print(f"\n[You] {prompt}")
        if _overlay_window:
            _overlay_window.set_prompt(prompt)
            _overlay_window.show_stop_btn()

        # Clear stop flag right before we start — ensures a clean slate even
        # if a stale event was left over from a previous stopped request.
        _generation_stop_event.clear()
        _generation_in_progress.set()

        answer      = ""
        was_stopped = False
        try:
            answer      = ask_ai_streaming(prompt)
            was_stopped = _generation_stop_event.is_set()
        except Exception as exc:
            answer = f"Error: {exc}"
            if _overlay_window:
                _overlay_window.set_response(answer)
        finally:
            _generation_in_progress.clear()
            if _overlay_window:
                _overlay_window.hide_stop_btn()
            _generation_stop_event.clear()

        # FIX-H: only restore if there is actually text — empty set_response("") wipes
        # partially-streamed text already visible on screen
        if was_stopped and _overlay_window and _overlay_window._current_answer:
            _overlay_window.set_response(_overlay_window._current_answer)

        print(f"[Assistant] {answer[:120]}")

        _conv_history.append({"role": "user",      "content": prompt})
        _conv_history.append({"role": "assistant", "content": answer})

        if len(_conv_history) > 40:
            _summarize_history()

        if _overlay_window:
            _overlay_window.add_history(prompt, answer)
            _overlay_window.set_status("Ready", "#00c864")
            _rendering_event.clear()
            _p, _a = prompt, answer
            threading.Thread(
                target=lambda p=_p, a=_a: _push_followups(p, a),
                daemon=True,
            ).start()

        speak(answer)
        command_queue.task_done()


def _summarize_history() -> None:
    global _conv_history
    try:
        old = _conv_history[:-10]; recent = _conv_history[-10:]
        if not old:
            return
        payload = ("Summarize this conversation very concisely in 3-5 bullet points:\n\n"
                   + "\n".join(f"{m['role']}: {m['content'][:200]}" for m in old))
        client  = _get_ollama_client()
        resp    = client.chat(
            model=SETTINGS["model"],
            messages=[{"role": "user", "content": payload}],
            options={"num_ctx": 2048, "num_predict": 200},
        )
        _conv_history = [
            {"role": "system", "content": f"[Summary: {resp.message.content.strip()}]"},
            *recent,
        ]
        log("History summarized")
    except Exception as exc:
        log(f"history summarize: {exc}")


def _push_followups(prompt: str, answer: str) -> None:
    followups = generate_followups(prompt, answer)
    if followups and _overlay_window:
        _overlay_window.set_followups(list(followups))


# =============================================================================
#  VOICE TRIGGER
# =============================================================================

_listen_lock = threading.Lock()


def on_trigger() -> None:
    """Called by global hotkey (Ctrl+L) and tray icon.
    Delegates to the overlay's PTT toggle if available,
    otherwise falls back to a direct listen_ptt() call."""
    if _overlay_window:
        # Route through UI toggle so button state stays consistent
        _overlay_window.communicate.show_window.emit()
        # FIX: QTimer.singleShot is NOT thread-safe from non-Qt threads.
        # Use the show_window signal (already QueuedConnection) to safely
        # bounce onto the Qt thread, then call _on_mic via another queued signal.
        # We reuse ptt_state_changed(True) as a "start PTT" trigger — False
        # is "stop", True means we want to *start* if not already listening,
        # which mirrors exactly what _on_mic does on first click.
        # Simpler: emit a dedicated trigger via update_status, which just queues
        # a call. Actually the cleanest solution is a dedicated signal:
        _overlay_window.communicate.trigger_ptt.emit()
        return

    # Headless / no-overlay path
    if not _listen_lock.acquire(blocking=False):
        log("Already listening — ignoring duplicate trigger")
        return
    try:
        _on_trigger_inner()
    finally:
        _listen_lock.release()


# Cache for ambient noise calibration — avoids 500ms blocking calibration on every listen call.
# Recalibrated automatically every 5 minutes or when energy drifts significantly.
_ambient_energy_cache: Optional[float] = None
_ambient_energy_ts: float = 0.0
_AMBIENT_CACHE_TTL = 300.0   # seconds before forced recalibration


def _on_trigger_inner() -> None:
    if shutdown_event.is_set():
        return

    # FIX 3: Run get_active_window_title in a fire-and-forget thread so it
    # never delays the mic opening. osascript on macOS can take 100-300ms.
    def _maybe_switch_persona():
        try:
            title = get_active_window_title()
            if title:
                auto_switch_persona(title)
        except Exception:
            pass
    threading.Thread(target=_maybe_switch_persona, daemon=True).start()

    listen_timeout = SETTINGS.get("listen_timeout_sec", 60)
    if _overlay_window:
        _overlay_window.communicate.update_status.emit(
            f"🎙 Listening… ({listen_timeout}s max)", "#ff6600")

    text = listen_once(timeout=listen_timeout)

    # NOTE: reset_mic_btn is emitted exclusively by _mic_thread's finally block.
    # Removed duplicate emit here to prevent race condition where mic button
    # resets before transcribed text is placed in the input box.

    if text:
        if _overlay_window:
            _overlay_window.set_input(text)
            _overlay_window.set_status("✏  Review & press Enter to send", "#ffcc00")
        else:
            command_queue.put(text)
    else:
        if _overlay_window:
            _overlay_window.set_status("Ready", "#00c864")


# =============================================================================
#  SCREEN SHARE INVISIBILITY
# =============================================================================

def _apply_screen_share_invisibility(win_id: int) -> None:
    os_name = platform.system()
    if os_name == "Darwin":
        try:
            from AppKit import NSApplication, NSWindowSharingNone  # type: ignore
            for w in NSApplication.sharedApplication().windows():
                try: w.setSharingType_(NSWindowSharingNone)
                except Exception: pass
        except ImportError:
            print("[WARNING] pip install pyobjc-framework-Cocoa")
        except Exception: pass
    elif os_name == "Windows":
        try:
            import ctypes
            ctypes.windll.user32.SetWindowDisplayAffinity(int(win_id), 0x00000011)
        except Exception: pass


def _force_always_on_top_macos(win_id: int) -> None:
    """Keep window on top WITHOUT activating it (no focus steal on macOS)."""
    if platform.system() != "Darwin":
        return
    try:
        from AppKit import NSApplication  # type: ignore
        for w in NSApplication.sharedApplication().windows():
            try:
                # NSFloatingWindowLevel=5 keeps it on top; orderFront_ does NOT
                # activate — unlike makeKeyAndOrderFront_ which would steal focus.
                w.setLevel_(5)
                w.setCanHide_(False)
                w.setCollectionBehavior_(1 << 3 | 1 << 6)
                w.orderFront_(None)   # raise without activating
            except Exception:
                pass
    except Exception:
        pass


def _raise_no_activate(win_id: int) -> None:
    """Bring the overlay window to the front on all platforms WITHOUT stealing focus.

    Qt's raise_() on Windows sends WM_SETFOCUS internally which activates the window.
    We bypass Qt here and call the OS APIs directly with the no-activate flags.
    """
    os_name = platform.system()

    if os_name == "Windows":
        try:
            import ctypes
            hwnd = int(win_id)
            SWP_NOSIZE      = 0x0001
            SWP_NOMOVE      = 0x0002
            SWP_NOACTIVATE  = 0x0010   # ← key flag: don't activate / steal focus
            HWND_TOPMOST    = -1
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE
            )
        except Exception:
            pass

    elif os_name == "Darwin":
        _force_always_on_top_macos(win_id)   # already uses orderFront_

    else:
        # Linux/X11 — use _NET_WM_STATE_ABOVE via xprop if available,
        # otherwise fall back to Qt raise_() (best effort on X11)
        try:
            import subprocess as _sp
            _sp.run(
                ["wmctrl", "-i", "-r", hex(int(win_id)), "-b", "add,above"],
                timeout=0.5, capture_output=True
            )
        except Exception:
            pass   # wmctrl not installed — silent fallback


# =============================================================================
#  PyQt5 OVERLAY
# =============================================================================

if HAS_PYQT6:

    # ── Signals ──────────────────────────────────────────────────────────────

    class Communicate(QObject):
        update_status    = pyqtSignal(str, str)
        update_prompt    = pyqtSignal(str)
        update_response  = pyqtSignal(str)
        append_token     = pyqtSignal(str)
        show_window      = pyqtSignal()
        hide_window      = pyqtSignal()
        clear_text       = pyqtSignal()
        set_input_text   = pyqtSignal(str)
        reset_mic_btn    = pyqtSignal()
        ptt_state_changed = pyqtSignal(bool)  # True=recording started, False=finished
        trigger_ptt       = pyqtSignal()           # fire-and-forget: calls _on_mic on Qt thread
        ptt_level         = pyqtSignal(int)            # RMS level 0-100 for live recording meter
        # FIX-B: int signal (not bool) — bool signals to int slots silently dropped
        set_mute_btn     = pyqtSignal(int)
        update_tokens    = pyqtSignal(int)
        add_history_item = pyqtSignal(str, str)
        panic_toggle     = pyqtSignal()
        set_followups    = pyqtSignal(list)
        add_rag_doc      = pyqtSignal(str, str)
        # FIX-A: int signal (not bool) — prevents PyQt5 silent-drop on bool→bool slots
        set_stop_btn     = pyqtSignal(int)
        # New signals for PTT/auto-listen mode changes
        auto_listen_changed = pyqtSignal(bool)   # True=active, False=stopped


    # ── Settings Panel ───────────────────────────────────────────────────────

    class SettingsPanel(QWidget):
        closed = pyqtSignal()

        def __init__(self, parent_overlay: "StealthOverlay") -> None:
            super().__init__()
            self.ov = parent_overlay
            self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setFixedSize(460, 680)

            pg = parent_overlay.geometry()
            self.move(max(0, pg.x() - 480), pg.y())

            outer = QFrame(self)
            outer.setGeometry(0, 0, 460, 680)
            outer.setStyleSheet("""
                QFrame { background:rgba(18,18,18,252); border-radius:14px;
                         border:1.5px solid rgba(0,195,95,160); }
            """)

            lay = QVBoxLayout(outer)
            lay.setContentsMargins(18, 14, 18, 14)
            lay.setSpacing(10)

            hdr = QHBoxLayout()
            ttl = QLabel("⚙  Settings")
            ttl.setFont(QFont("Menlo", 13, QFont.Weight.Bold))
            ttl.setStyleSheet("color:#cccccc;")
            hdr.addWidget(ttl); hdr.addStretch()
            close = QPushButton("✕")
            close.setFixedSize(24, 24)
            close.setStyleSheet("""QPushButton{background:rgba(255,80,80,200);color:#111;
                border-radius:12px;font-weight:bold;}
                QPushButton:hover{background:rgba(255,50,50,255);}""")
            close.clicked.connect(self._close)
            hdr.addWidget(close)
            lay.addLayout(hdr)
            lay.addWidget(self._div())

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
            inner = QWidget(); inner.setStyleSheet("background:transparent;")
            inner_lay = QVBoxLayout(inner); inner_lay.setSpacing(8)

            inner_lay.addWidget(self._lbl("AI Model"))
            self.model_combo = QComboBox()
            self.model_combo.addItems(SETTINGS["available_models"])
            idx = self.model_combo.findText(SETTINGS["model"])
            if idx >= 0: self.model_combo.setCurrentIndex(idx)
            self._style_combo(self.model_combo)
            self.model_combo.currentTextChanged.connect(self._on_model_change)
            inner_lay.addWidget(self.model_combo)

            inner_lay.addWidget(self._lbl("Persona"))
            self.persona_combo = QComboBox()
            self.persona_combo.addItems(list(PERSONAS.keys()))
            pidx = self.persona_combo.findText(SETTINGS["persona"])
            if pidx >= 0: self.persona_combo.setCurrentIndex(pidx)
            self._style_combo(self.persona_combo)
            self.persona_combo.currentTextChanged.connect(self._on_persona_change)
            inner_lay.addWidget(self.persona_combo)
            inner_lay.addWidget(self._div())

            inner_lay.addWidget(self._lbl("Speech-to-Text Engine"))
            self.stt_combo = QComboBox()
            self.stt_combo.addItems(["faster_whisper", "google", "vosk"])
            sidx = self.stt_combo.findText(SETTINGS.get("stt_engine", "faster_whisper"))
            if sidx >= 0: self.stt_combo.setCurrentIndex(sidx)
            self._style_combo(self.stt_combo)
            self.stt_combo.currentTextChanged.connect(self._on_stt_change)
            inner_lay.addWidget(self.stt_combo)

            inner_lay.addWidget(self._lbl("Whisper Model Size"))
            self.whisper_combo = QComboBox()
            self.whisper_combo.addItems(["tiny", "base", "small", "medium"])
            widx = self.whisper_combo.findText(SETTINGS.get("whisper_model", "base"))
            if widx >= 0: self.whisper_combo.setCurrentIndex(widx)
            self._style_combo(self.whisper_combo)
            self.whisper_combo.currentTextChanged.connect(self._on_whisper_change)
            inner_lay.addWidget(self.whisper_combo)
            inner_lay.addWidget(self._div())

            inner_lay.addWidget(self._lbl("Text-to-Speech Engine"))
            self.tts_combo = QComboBox()
            self.tts_combo.addItems(["pyttsx3", "elevenlabs", "coqui"])
            tidx = self.tts_combo.findText(SETTINGS.get("tts_engine", "pyttsx3"))
            if tidx >= 0: self.tts_combo.setCurrentIndex(tidx)
            self._style_combo(self.tts_combo)
            self.tts_combo.currentTextChanged.connect(self._on_tts_engine_change)
            inner_lay.addWidget(self.tts_combo)

            inner_lay.addWidget(self._lbl("ElevenLabs API Key"))
            self.el_key = QLineEdit(SETTINGS.get("elevenlabs_key", ""))
            self.el_key.setPlaceholderText("sk-...")
            self.el_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.el_key.setFont(QFont("Menlo", 10))
            self.el_key.setStyleSheet("""QLineEdit{background:rgba(25,25,25,220);
                color:#eee;border:1px solid rgba(50,50,50,160);border-radius:6px;padding:5px;}""")
            self.el_key.textChanged.connect(
                lambda t: (SETTINGS.update({"elevenlabs_key": t}), save_settings(SETTINGS)))
            inner_lay.addWidget(self.el_key)
            inner_lay.addWidget(self._div())

            inner_lay.addWidget(self._lbl("TTS Speed"))
            self.rate_sl = self._slider(50, 350, SETTINGS["tts_rate"])
            self.rate_sl.valueChanged.connect(self._on_rate_change)
            inner_lay.addWidget(self.rate_sl)

            inner_lay.addWidget(self._lbl("TTS Volume"))
            self.vol_sl = self._slider(0, 100, int(SETTINGS["tts_volume"] * 100))
            self.vol_sl.valueChanged.connect(self._on_vol_change)
            inner_lay.addWidget(self.vol_sl)

            inner_lay.addWidget(self._lbl("Overlay Opacity"))
            self.opacity_sl = self._slider(30, 100, SETTINGS["opacity"])
            self.opacity_sl.valueChanged.connect(self._on_opacity_change)
            inner_lay.addWidget(self.opacity_sl)

            inner_lay.addWidget(self._lbl("Font Size"))
            fs_row = QHBoxLayout()
            self.font_lbl = QLabel(str(SETTINGS["font_size"]))
            self.font_lbl.setFixedWidth(30)
            self.font_lbl.setStyleSheet("color:#aaa;")
            for label, delta in [("−", -1), ("+", 1)]:
                b = QPushButton(label)
                b.setFixedSize(32, 32)
                b.setStyleSheet("""QPushButton{background:rgba(50,50,50,200);
                    color:#fff;border-radius:6px;font-size:18px;}
                    QPushButton:hover{background:rgba(80,80,80,220);}""")
                b.clicked.connect(lambda _, d=delta: self._change_font(d))
                fs_row.addWidget(b)
            fs_row.addWidget(self.font_lbl); fs_row.addStretch()
            inner_lay.addLayout(fs_row)
            inner_lay.addWidget(self._div())

            self.auto_send_cb  = self._checkbox("Auto-send after voice",       SETTINGS["auto_send"])
            self.clipboard_cb  = self._checkbox("Clipboard monitor",           SETTINGS["clipboard_monitor"])
            self.continuous_cb = self._checkbox("Continuous listening (VAD)",  SETTINGS.get("continuous_listen", False))
            self.web_search_cb = self._checkbox("Web search (auto + /search)", SETTINGS.get("web_search_enabled", True))
            self.rag_cb        = self._checkbox("RAG (use my documents)",      SETTINGS.get("rag_enabled", False))
            self.code_exec_cb  = self._checkbox("Code execution sandbox",      SETTINGS.get("code_exec_enabled", True))
            self.rest_api_cb   = self._checkbox(
                f"REST API (port {SETTINGS.get('rest_api_port',7788)})",
                SETTINGS.get("rest_api_enabled", False))

            for cb, key in [
                (self.auto_send_cb,  "auto_send"),
                (self.clipboard_cb,  "clipboard_monitor"),
                (self.continuous_cb, "continuous_listen"),
                (self.web_search_cb, "web_search_enabled"),
                (self.rag_cb,        "rag_enabled"),
                (self.code_exec_cb,  "code_exec_enabled"),
            ]:
                cb.stateChanged.connect(
                    lambda state, k=key: (SETTINGS.update({k: bool(state)}), save_settings(SETTINGS)))
                inner_lay.addWidget(cb)
            self.rest_api_cb.stateChanged.connect(self._on_rest_api_toggle)
            inner_lay.addWidget(self.rest_api_cb)
            inner_lay.addWidget(self._div())

            inner_lay.addWidget(self._lbl("🎙 Listen timeout (seconds)"))
            self.listen_timeout_sl = self._slider(5, 120, SETTINGS.get("listen_timeout_sec", 60))
            self.listen_timeout_sl.valueChanged.connect(self._on_listen_timeout_change)
            inner_lay.addWidget(self.listen_timeout_sl)
            self._listen_timeout_val = QLabel(f"{SETTINGS.get('listen_timeout_sec',60)}s")
            self._listen_timeout_val.setStyleSheet("color:#aaa;font-size:10px;")
            inner_lay.addWidget(self._listen_timeout_val)
            self.listen_timeout_sl.valueChanged.connect(
                lambda v: self._listen_timeout_val.setText(f"{v}s"))

            inner_lay.addWidget(self._lbl("🎙 Phrase limit (0 = until natural pause)"))
            self.phrase_limit_sl = self._slider(0, 120, SETTINGS.get("phrase_time_limit_sec", 0))
            self.phrase_limit_sl.valueChanged.connect(self._on_phrase_limit_change)
            inner_lay.addWidget(self.phrase_limit_sl)
            self._phrase_limit_val = QLabel(
                "No limit" if SETTINGS.get("phrase_time_limit_sec", 0) == 0
                else f"{SETTINGS.get('phrase_time_limit_sec',0)}s")
            self._phrase_limit_val.setStyleSheet("color:#aaa;font-size:10px;")
            inner_lay.addWidget(self._phrase_limit_val)
            self.phrase_limit_sl.valueChanged.connect(
                lambda v: self._phrase_limit_val.setText("No limit" if v == 0 else f"{v}s"))
            inner_lay.addWidget(self._div())

            inner_lay.addWidget(self._lbl("Auto-clear after inactivity (minutes, 0=off)"))
            self.autoclear_sl = self._slider(0, 30, SETTINGS.get("auto_clear_minutes", 0))
            self.autoclear_sl.valueChanged.connect(self._on_autoclear_change)
            inner_lay.addWidget(self.autoclear_sl)
            inner_lay.addStretch()
            inner_lay.addWidget(self._div())

            rag_btn = QPushButton("📂 Load Document into RAG")
            rag_btn.setFixedHeight(34); rag_btn.setFont(QFont("Menlo", 10))
            rag_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            rag_btn.setStyleSheet("""QPushButton{background:rgba(20,60,80,200);color:#88ddff;
                border:1px solid rgba(0,120,180,150);border-radius:8px;}
                QPushButton:hover{background:rgba(30,80,110,220);}""")
            rag_btn.clicked.connect(self._load_rag_doc)
            inner_lay.addWidget(rag_btn)
            inner_lay.addWidget(QLabel(""))

            scroll.setWidget(inner)
            lay.addWidget(scroll)

            note = QLabel("Settings saved automatically  •  ~/.ai_assistant/settings.json")
            note.setFont(QFont("Menlo", 8)); note.setStyleSheet("color:#333;")
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(note)

        @staticmethod
        def _lbl(t: str) -> QLabel:
            l = QLabel(t); l.setFont(QFont("Menlo", 9, QFont.Weight.Bold))
            l.setStyleSheet("color:#777;"); return l

        @staticmethod
        def _div() -> QFrame:
            d = QFrame(); d.setFrameShape(QFrame.Shape.HLine)
            d.setStyleSheet("background:rgba(40,40,40,200);"); d.setFixedHeight(1)
            return d

        @staticmethod
        def _slider(mn, mx, val) -> QSlider:
            s = QSlider(Qt.Orientation.Horizontal); s.setRange(mn, mx); s.setValue(val)
            s.setStyleSheet("""
                QSlider::groove:horizontal{background:rgba(40,40,40,200);height:4px;border-radius:2px;}
                QSlider::handle:horizontal{background:#00c864;width:14px;height:14px;
                    border-radius:7px;margin:-5px 0;}
                QSlider::sub-page:horizontal{background:rgba(0,180,90,180);border-radius:2px;}
            """)
            return s

        @staticmethod
        def _checkbox(text: str, checked: bool) -> QCheckBox:
            cb = QCheckBox(text); cb.setChecked(checked)
            cb.setFont(QFont("Menlo", 10))
            cb.setStyleSheet("color:#aaaaaa; spacing:8px;")
            return cb

        @staticmethod
        def _style_combo(cb: QComboBox) -> None:
            cb.setFont(QFont("Menlo", 10))
            cb.setStyleSheet("""
                QComboBox{background:rgba(28,28,28,230);color:#eee;
                    border:1.5px solid rgba(0,170,80,140);border-radius:8px;padding:5px 10px;}
                QComboBox:hover{border-color:rgba(0,210,110,220);}
                QComboBox QAbstractItemView{background:rgba(22,22,22,240);color:#eee;
                    selection-background-color:rgba(0,160,80,180);}
            """)

        def _on_model_change(self, m: str) -> None:
            SETTINGS["model"] = m; save_settings(SETTINGS); reload_ollama_client()
            self.ov.set_status(f"Model → {m}", "#00aaff")
            self.ov.model_indicator.setText(f"▸ {m}  •  {SETTINGS['persona']}")
            QTimer.singleShot(3000, lambda: self.ov.set_status("Ready", "#00c864"))

        def _on_persona_change(self, p: str) -> None:
            SETTINGS["persona"] = p; save_settings(SETTINGS)
            self.ov.model_indicator.setText(f"▸ {SETTINGS['model']}  •  {p}")

        def _on_stt_change(self, e: str) -> None:
            SETTINGS["stt_engine"] = e; save_settings(SETTINGS)

        def _on_whisper_change(self, s: str) -> None:
            global _whisper_model
            SETTINGS["whisper_model"] = s; save_settings(SETTINGS)
            _whisper_model = None
            # PERF: reload the new model in background so next listen is instant
            _preload_whisper_model()

        def _on_tts_engine_change(self, e: str) -> None:
            SETTINGS["tts_engine"] = e; save_settings(SETTINGS)

        def _on_rate_change(self, v: int) -> None:
            SETTINGS["tts_rate"] = v
            if _HAS_TTS and _tts_engine is not None:
                _tts_engine.setProperty("rate", v)
            save_settings(SETTINGS)

        def _on_vol_change(self, v: int) -> None:
            vol = v / 100; SETTINGS["tts_volume"] = vol
            if _HAS_TTS and _tts_engine is not None:
                _tts_engine.setProperty("volume", vol)
            save_settings(SETTINGS)

        def _on_opacity_change(self, v: int) -> None:
            SETTINGS["opacity"] = v; self.ov.setWindowOpacity(v / 100); save_settings(SETTINGS)

        def _change_font(self, d: int) -> None:
            fs = max(9, min(22, SETTINGS["font_size"] + d))
            SETTINGS["font_size"] = fs; self.font_lbl.setText(str(fs))
            self.ov._apply_font_size(fs); save_settings(SETTINGS)

        def _on_autoclear_change(self, v: int) -> None:
            SETTINGS["auto_clear_minutes"] = v; save_settings(SETTINGS)

        def _on_listen_timeout_change(self, v: int) -> None:
            SETTINGS["listen_timeout_sec"] = v; save_settings(SETTINGS)

        def _on_phrase_limit_change(self, v: int) -> None:
            SETTINGS["phrase_time_limit_sec"] = v; save_settings(SETTINGS)

        def _on_rest_api_toggle(self, state: int) -> None:
            SETTINGS["rest_api_enabled"] = bool(state); save_settings(SETTINGS)
            if state:
                port = SETTINGS.get("rest_api_port", 7788)
                threading.Thread(target=start_rest_api, args=(port,), daemon=True).start()

        def _load_rag_doc(self) -> None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Load Document", str(Path.home()),
                "Text Files (*.txt *.md *.py *.java *.json);;All Files (*)")
            if not path: return
            try:
                text   = Path(path).read_text(encoding="utf-8", errors="ignore")
                doc_id = Path(path).name
                ok = rag_add_document(text, doc_id, {"source": doc_id, "path": path})
                self.ov.set_status(
                    f"RAG: {doc_id} loaded ✓" if ok else "RAG load failed",
                    "#00c864" if ok else "#ff4444")
            except Exception as exc:
                self.ov.set_status(f"Load error: {exc}", "#ff4444")

        def _close(self) -> None:
            self.hide(); self.closed.emit()

        def mousePressEvent(self, e) -> None:
            if e.button() == Qt.MouseButton.LeftButton:
                self._dp = e.globalPos() - self.frameGeometry().topLeft()
            super().mousePressEvent(e)

        def mouseMoveEvent(self, e) -> None:
            if e.buttons() == Qt.MouseButton.LeftButton:
                self.move(e.globalPos() - self._dp)
            super().mouseMoveEvent(e)


    # ── Main Overlay ─────────────────────────────────────────────────────────

    class StealthOverlay(QWidget):

        def __init__(self) -> None:
            super().__init__()
            self.communicate        = Communicate()
            self._streaming         = False
            self._is_listening      = False
            self._ptt_active        = False
            self._think_dots        = 0
            self._settings_panel: Optional[SettingsPanel] = None
            self._history_items: List[Dict] = []
            self._current_prompt    = ""
            self._current_answer    = ""
            self._search_visible    = False
            self.drag_pos           = QPoint()
            self.is_visible         = False
            self._user_scrolled_up  = False   # True when user manually scrolled up during stream

            C = self.communicate
            C.update_status.connect(self._set_status,       Qt.ConnectionType.QueuedConnection)
            C.update_prompt.connect(self._set_prompt,       Qt.ConnectionType.QueuedConnection)
            C.update_response.connect(self._set_response,   Qt.ConnectionType.QueuedConnection)
            C.append_token.connect(self._append_token,      Qt.ConnectionType.QueuedConnection)
            C.show_window.connect(self._show,               Qt.ConnectionType.QueuedConnection)
            C.hide_window.connect(self._hide,               Qt.ConnectionType.QueuedConnection)
            C.clear_text.connect(self._clear,               Qt.ConnectionType.QueuedConnection)
            C.set_input_text.connect(self._set_input,       Qt.ConnectionType.QueuedConnection)
            C.reset_mic_btn.connect(self._on_mic_reset,     Qt.ConnectionType.QueuedConnection)
            C.ptt_state_changed.connect(self._on_ptt_state,   Qt.ConnectionType.QueuedConnection)
            C.trigger_ptt.connect(self._on_mic,           Qt.ConnectionType.QueuedConnection)
            C.ptt_level.connect(self._on_ptt_level,       Qt.ConnectionType.QueuedConnection)
            C.set_mute_btn.connect(self._update_mute_btn,   Qt.ConnectionType.QueuedConnection)
            C.update_tokens.connect(self._update_tokens,    Qt.ConnectionType.QueuedConnection)
            C.add_history_item.connect(self._add_history,   Qt.ConnectionType.QueuedConnection)
            C.panic_toggle.connect(self._panic,             Qt.ConnectionType.QueuedConnection)
            C.set_followups.connect(self._set_followups,    Qt.ConnectionType.QueuedConnection)
            # FIX-A: signal is now int — slot typed @pyqtSlot(int)
            C.set_stop_btn.connect(self._set_stop_visible,  Qt.ConnectionType.QueuedConnection)
            C.auto_listen_changed.connect(self._on_auto_listen_changed, Qt.ConnectionType.QueuedConnection)

            self._think_timer = QTimer()
            self._think_timer.timeout.connect(self._tick_thinking)

            self._clip_timer = QTimer()
            self._clip_timer.timeout.connect(self._check_clipboard)
            self._clip_timer.start(800)

            self._autoclear_timer = QTimer()
            self._autoclear_timer.timeout.connect(self._check_autoclear)
            self._autoclear_timer.start(30_000)
            self._last_activity = time.time()

            # ── Keep-alive: re-raise every 2s so window never disappears ─────
            # Fixes: overlay vanishing when another app takes focus on Windows/macOS
            self._keepalive_timer = QTimer()
            self._keepalive_timer.timeout.connect(self._keepalive)
            self._keepalive_timer.start(2000)

            self._init_ui()
            # No auto-focus on startup — don't steal focus from other apps

        # ── UI BUILD ─────────────────────────────────────────────────────────

        def _init_ui(self) -> None:
            # FIX: WindowDoesNotAcceptFocus is an OS-level flag that prevents the
            # ENTIRE window from receiving keyboard input — including child widgets
            # like input_box. Removed it. WA_ShowWithoutActivating is sufficient
            # to prevent focus-stealing on show() without blocking typing.
            # WA_X11DoNotAcceptFocus also removed for the same reason on Linux.
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)   # no auto focus-steal on show
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
            self.setStyleSheet("background: transparent;")
            self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            try:
                self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
            except Exception:
                pass

            W, H   = 1020, 780
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(0, screen.x() + screen.width()  - W - 20)
            y = max(0, screen.y() + screen.height() - H - 20)
            self.setGeometry(x, y, W, H)
            self.setMinimumSize(680, 520)

            self_layout = QVBoxLayout(self)
            self_layout.setContentsMargins(0, 0, 0, 0)
            self_layout.setSpacing(0)

            self.container = QFrame(self)
            self.container.setObjectName("outer")
            self.container.setStyleSheet("""
                QFrame#outer {
                    background: #1e1e2e;
                    border-radius: 16px;
                    border: 1px solid #2a2a3e;
                }
            """)
            self_layout.addWidget(self.container)

            root = QVBoxLayout(self.container)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            root.addWidget(self._build_titlebar())

            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.setStyleSheet(
                "QSplitter::handle{background:rgba(35,35,35,200);width:1px;}")
            splitter.addWidget(self._build_sidebar())
            splitter.addWidget(self._build_main_area())
            splitter.setSizes([210, 810])
            root.addWidget(splitter, stretch=1)

            grip = QSizeGrip(self)
            grip.setStyleSheet("background:transparent;")
            root.addWidget(grip, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

        # ── Title bar ────────────────────────────────────────────────────────

        def _build_titlebar(self) -> QFrame:
            tbar = QFrame()
            tbar.setFixedHeight(52)
            tbar.setObjectName("tbar")
            tbar.setStyleSheet("""
                QFrame#tbar {
                    background: #1e1e2e;
                    border-top-left-radius: 16px;
                    border-top-right-radius: 16px;
                    border-bottom: 1px solid #2a2a3e;
                }
            """)
            tb = QHBoxLayout(tbar)
            tb.setContentsMargins(16, 0, 16, 0)
            tb.setSpacing(8)

            close = QPushButton()
            close.setFixedSize(14, 14)
            close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            close.setToolTip("Quit  Ctrl+Q")
            close.setStyleSheet("""
                QPushButton { background:#ff5f57; border-radius:7px; border:none; }
                QPushButton:hover { background:#ff3b30; }
            """)
            close.clicked.connect(self._on_close)
            tb.addWidget(close)
            tb.addSpacing(8)

            avatar = QLabel("✦")
            avatar.setFont(QFont("Arial", 16))
            avatar.setStyleSheet("color:#a855f7; background:transparent;")
            tb.addWidget(avatar)

            title_col = QVBoxLayout(); title_col.setSpacing(0)
            title_lbl = QLabel("AI Interview Assistant")
            title_lbl.setFont(QFont("Arial", 13, QFont.Weight.Bold))
            title_lbl.setStyleSheet("color:#e2e8f0; background:transparent;")
            title_col.addWidget(title_lbl)
            self.model_indicator = QLabel(f"{SETTINGS['model']}  ·  {SETTINGS['persona']}")
            self.model_indicator.setFont(QFont("Arial", 9))
            self.model_indicator.setStyleSheet("color:#64748b; background:transparent;")
            title_col.addWidget(self.model_indicator)
            tb.addLayout(title_col)
            tb.addStretch()

            self.token_lbl = QLabel("0 tokens")
            self.token_lbl.setFont(QFont("Arial", 9))
            self.token_lbl.setStyleSheet("color:#475569; background:transparent;")
            tb.addWidget(self.token_lbl)
            tb.addSpacing(8)

            self.status_label = QLabel("● Ready")
            self.status_label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            self.status_label.setStyleSheet(
                "color:#00c864; background:transparent; min-width:160px;")
            self.status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            tb.addWidget(self.status_label)

            # ── STOP button ───────────────────────────────────────────────
            # FIX-A: always created here; shown/hidden via _set_stop_visible
            self.stop_btn = QPushButton("⏹  Stop")
            self.stop_btn.setFixedHeight(30)
            self.stop_btn.setMinimumWidth(90)
            self.stop_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.stop_btn.setToolTip("Stop response generation  Esc")
            self.stop_btn.setStyleSheet("""
                QPushButton {
                    background: #7f1d1d;
                    color: #fca5a5;
                    border: 1px solid #991b1b;
                    border-radius: 8px;
                    font-size: 11px;
                    font-weight: bold;
                    font-family: Arial, sans-serif;
                    padding: 0 12px;
                }
                QPushButton:hover { background: #991b1b; color:#fff; }
                QPushButton:pressed { background: #450a0a; }
            """)
            self.stop_btn.clicked.connect(self._on_stop_generation)
            self.stop_btn.hide()   # hidden until generation starts
            tb.addWidget(self.stop_btn)

            tb.addSpacing(4)

            def _icon_tb(emoji, tip, fn):
                b = QPushButton(emoji)
                b.setFixedSize(32, 32)
                b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                b.setToolTip(tip)
                b.setStyleSheet("""
                    QPushButton { background:transparent; color:#64748b;
                        border:none; border-radius:8px; font-size:15px; }
                    QPushButton:hover { background:#2a2a3e; color:#e2e8f0; }
                """)
                b.clicked.connect(fn)
                return b

            tb.addWidget(_icon_tb("🔍", "Search history  Ctrl+F", self._toggle_search))

            self.mute_btn = QPushButton("🔊")
            self.mute_btn.setFixedSize(32, 32)
            self.mute_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.mute_btn.setToolTip("Mute / unmute TTS  Ctrl+M")
            self.mute_btn.setStyleSheet(self._mute_style(False))
            self.mute_btn.clicked.connect(self._on_mute_toggle)
            tb.addWidget(self.mute_btn)

            tb.addWidget(_icon_tb("⚙", "Settings", self._open_settings))
            return tbar

        # ── Sidebar ──────────────────────────────────────────────────────────

        def _build_sidebar(self) -> QWidget:
            panel = QWidget()
            panel.setStyleSheet("background: #16162a;")
            panel.setMinimumWidth(200)
            panel.setMaximumWidth(260)

            lay = QVBoxLayout(panel)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(0)

            hdr = QFrame(); hdr.setFixedHeight(44)
            hdr.setStyleSheet("background:#1a1a2e; border-bottom:1px solid #2a2a3e;")
            hdr_lay = QHBoxLayout(hdr); hdr_lay.setContentsMargins(14, 0, 10, 0)
            hdr_lbl = QLabel("Conversations")
            hdr_lbl.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            hdr_lbl.setStyleSheet("color:#94a3b8; background:transparent;")
            hdr_lay.addWidget(hdr_lbl); hdr_lay.addStretch()
            new_btn = QPushButton("＋"); new_btn.setFixedSize(26, 26)
            new_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            new_btn.setToolTip("New conversation  Ctrl+D")
            new_btn.setStyleSheet("""
                QPushButton { background:#2a2a3e; color:#94a3b8;
                    border:none; border-radius:6px; font-size:16px; }
                QPushButton:hover { background:#3a3a5e; color:#e2e8f0; }
            """)
            new_btn.clicked.connect(self._clear)
            hdr_lay.addWidget(new_btn); lay.addWidget(hdr)

            self.search_bar = QLineEdit()
            self.search_bar.setPlaceholderText("Search…")
            self.search_bar.setFixedHeight(32)
            self.search_bar.setStyleSheet("""
                QLineEdit { background:#1e1e2e; color:#cbd5e1;
                    border:none; border-bottom:1px solid #2a2a3e;
                    padding:0 14px; font-size:11px; font-family:Arial,sans-serif; }
            """)
            self.search_bar.textChanged.connect(self._filter_history)
            self.search_bar.hide(); lay.addWidget(self.search_bar)

            tab_bar = QFrame(); tab_bar.setFixedHeight(36)
            tab_bar.setStyleSheet("background:#16162a; border-bottom:1px solid #2a2a3e;")
            tab_lay = QHBoxLayout(tab_bar)
            tab_lay.setContentsMargins(8, 4, 8, 0); tab_lay.setSpacing(4)

            def _tab(label, idx):
                b = QPushButton(label); b.setFixedHeight(26)
                b.setCheckable(True); b.setChecked(idx == 0)
                b.setFont(QFont("Arial", 9))
                b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                b.setStyleSheet("""
                    QPushButton { background:transparent; color:#64748b;
                        border:none; border-radius:6px; padding:0 10px; }
                    QPushButton:hover { color:#94a3b8; }
                    QPushButton:checked { background:#2a2a3e; color:#e2e8f0; }
                """)
                tab = "history" if idx == 0 else "pins"
                b.clicked.connect(lambda _, t=tab: self._show_sidebar_tab(t))
                return b

            self.hist_tab_btn = _tab("History", 0)
            self.pins_tab_btn = _tab("⭐ Pinned", 1)
            tab_lay.addWidget(self.hist_tab_btn); tab_lay.addWidget(self.pins_tab_btn)
            tab_lay.addStretch(); lay.addWidget(tab_bar)

            self.sidebar_stack = QStackedWidget()
            self.sidebar_stack.setStyleSheet("background:transparent;")

            list_css = """
                QListWidget { background:transparent; color:#94a3b8; border:none; outline:none; }
                QListWidget::item { padding:8px 14px;
                    border-bottom:1px solid rgba(42,42,62,120); }
                QListWidget::item:hover { background:rgba(42,42,62,180); color:#cbd5e1; }
                QListWidget::item:selected { background:#2a2a4e; color:#e2e8f0; }
            """

            hist_w = QWidget(); hist_w.setStyleSheet("background:transparent;")
            hl = QVBoxLayout(hist_w); hl.setContentsMargins(0,0,0,0); hl.setSpacing(0)
            self.history_list = QListWidget()
            self.history_list.setFont(QFont("Arial", 9))
            self.history_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.history_list.setStyleSheet(list_css)
            self.history_list.itemClicked.connect(self._on_history_click)
            hl.addWidget(self.history_list)
            btn_row = QHBoxLayout(); btn_row.setContentsMargins(8,6,8,6); btn_row.setSpacing(6)
            for txt, bg, fg, fn in [
                ("Export", "#1e3a2e", "#6ee7b7", self._on_export),
                ("Clear",  "#2d1b1b", "#fca5a5", self._clear_history),
            ]:
                btn_row.addWidget(self._small_btn(txt, bg, fg, fn))
            hl.addLayout(btn_row)
            self.sidebar_stack.addWidget(hist_w)

            pins_w = QWidget(); pins_w.setStyleSheet("background:transparent;")
            pl = QVBoxLayout(pins_w); pl.setContentsMargins(0,0,0,0)
            self.pins_list = QListWidget()
            self.pins_list.setFont(QFont("Arial", 9))
            self.pins_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.pins_list.setStyleSheet(list_css)
            self.pins_list.itemClicked.connect(self._on_pin_click)
            pl.addWidget(self.pins_list)
            self.sidebar_stack.addWidget(pins_w)
            self._reload_pins()

            lay.addWidget(self.sidebar_stack, stretch=1)
            return panel

        # ── Main chat area ───────────────────────────────────────────────────
        #
        # FIX-B / FIX-C: The main area is where input and output live.
        # Key layout changes:
        #   • User bubble (QTextEdit, read-only) — shows current question
        #   • Response area (QTextBrowser) — shows AI answer with markdown
        #   • Input bar (QLineEdit) at bottom with Mic + Send buttons
        #   • Default template pre-loaded via QTimer after UI is ready
        # The area uses a fixed splitter proportion so both regions are
        # always clearly visible without needing to scroll.

        def _build_main_area(self) -> QWidget:
            w = QWidget(); w.setStyleSheet("background: transparent;")
            lay = QVBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.setSpacing(0)

            # ── Chat viewport ─────────────────────────────────────────────
            chat_area = QWidget(); chat_area.setStyleSheet("background: #1e1e2e;")
            chat_lay  = QVBoxLayout(chat_area)
            chat_lay.setContentsMargins(0, 8, 0, 0); chat_lay.setSpacing(0)

            # ── User question bubble (right-aligned) ─────────────────────
            user_wrap = QFrame(); user_wrap.setStyleSheet("background:transparent;")
            uwl = QHBoxLayout(user_wrap); uwl.setContentsMargins(20, 4, 20, 4)
            uwl.addStretch()

            user_bubble = QFrame(); user_bubble.setObjectName("ub")
            user_bubble.setStyleSheet("""
                QFrame#ub {
                    background: #2d2d52;
                    border-radius: 18px;
                    border-bottom-right-radius: 4px;
                }
            """)
            ubl = QVBoxLayout(user_bubble); ubl.setContentsMargins(12, 6, 12, 6)

            self.prompt_text = QTextEdit()
            self.prompt_text.setReadOnly(True)
            self.prompt_text.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # no focus steal on click
            self.prompt_text.setFont(QFont("Arial", SETTINGS["font_size"] - 1))
            self.prompt_text.setMinimumHeight(36)
            self.prompt_text.setMaximumHeight(72)   # compact — 2 lines max before scroll
            self.prompt_text.setStyleSheet("""
                QTextEdit { background:transparent; color:#cbd5e1; border:none; padding:0;
                    selection-background-color:rgba(168,85,247,80); }
                QScrollBar:vertical { width:0px; }
            """)
            ubl.addWidget(self.prompt_text)
            user_bubble.setMaximumWidth(700)
            uwl.addWidget(user_bubble)
            chat_lay.addWidget(user_wrap)

            # ── Separator ─────────────────────────────────────────────────
            sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
            sep.setStyleSheet("background:#2a2a3e; margin:0 20px;"); sep.setFixedHeight(1)
            chat_lay.addWidget(sep)

            # ── AI response area ──────────────────────────────────────────
            ai_wrap = QFrame(); ai_wrap.setStyleSheet("background:transparent;")
            ai_lay  = QVBoxLayout(ai_wrap)
            ai_lay.setContentsMargins(20, 10, 20, 6); ai_lay.setSpacing(6)

            ai_hdr = QHBoxLayout(); ai_hdr.setSpacing(8)

            # FIX-C: show STAR-T badge in AI header to make default persona obvious
            ai_avatar = QLabel("✦")
            ai_avatar.setFont(QFont("Arial", 14))
            ai_avatar.setStyleSheet("color:#a855f7; background:transparent;")
            ai_hdr.addWidget(ai_avatar)

            ai_name = QLabel("Assistant  ·  STAR-T mode")
            ai_name.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            ai_name.setStyleSheet("color:#94a3b8; background:transparent;")
            ai_hdr.addWidget(ai_name)
            self._ai_name_lbl = ai_name   # keep ref so persona changes can update it

            ai_hdr.addStretch()

            for emoji, tip, fn in [
                ("⎘",  "Copy  Ctrl+C",         self._on_copy),
                ("⭐",  "Pin answer",            self._on_pin),
                ("▶",  "Run code  Ctrl+Shift+R", self._on_run_code),
                ("🔄", "Retry last question  Ctrl+R", self._on_retry),
                ("👍", "Good answer",            lambda: self._on_rate(1)),
                ("👎", "Bad answer",             lambda: self._on_rate(-1)),
            ]:
                btn = QPushButton(emoji); btn.setFixedSize(28, 28)
                btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                btn.setToolTip(tip)
                btn.setStyleSheet("""
                    QPushButton { background:transparent; color:#475569;
                        border:none; border-radius:6px; font-size:13px; }
                    QPushButton:hover { background:#2a2a3e; color:#94a3b8; }
                """)
                btn.clicked.connect(fn)
                if emoji == "⎘": self.copy_btn = btn
                if emoji == "⭐": self.pin_btn  = btn
                if emoji == "▶": self.run_btn  = btn
                ai_hdr.addWidget(btn)

            ai_lay.addLayout(ai_hdr)

            # ── Response browser — takes all remaining vertical space ─────
            self.response_text = QTextBrowser()
            self.response_text.setOpenExternalLinks(True)
            self.response_text.setFont(QFont("Arial", SETTINGS["font_size"]))
            # NoFocus — clicking response to scroll/select text must NOT steal
            # focus from the user's active app (IDE, browser, etc.)
            self.response_text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self.response_text.setStyleSheet("""
                QTextBrowser {
                    background: transparent;
                    color: #e2e8f0;
                    border: none;
                    padding: 0 4px;
                    selection-background-color: rgba(168,85,247,80);
                    line-height: 1.6;
                }
                QScrollBar:vertical { background:transparent; width:5px; }
                QScrollBar::handle:vertical { background:rgba(100,100,130,200);
                    border-radius:2px; min-height:20px; }
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            """)
            ai_lay.addWidget(self.response_text, stretch=1)   # ← stretch=1 fills space

            # Track whether user manually scrolled up so we don't fight them
            def _on_scroll_changed(value: int) -> None:
                sb = self.response_text.verticalScrollBar()
                # If scrollbar is at max, user is at bottom — resume auto-scroll
                self._user_scrolled_up = value < sb.maximum() - 10

            self.response_text.verticalScrollBar().valueChanged.connect(_on_scroll_changed)

            # ── Thinking progress bar (animated, hidden when idle) ────────
            #self.progress_bar = QProgressBar()
            self.progress_bar = QProgressBar()
            self.progress_bar.setRange(0, 0)   # indeterminate / marquee mode
            self.progress_bar.setFixedHeight(3)
            self.progress_bar.setTextVisible(False)
            self.progress_bar.setStyleSheet("""
                QProgressBar {
                    background: #2a2a3e;
                    border: none;
                    border-radius: 1px;
                }
                QProgressBar::chunk {
                    background: qlineargradient(
                        x1:0, y1:0, x2:1, y2:0,
                        stop:0 #6d28d9, stop:0.5 #a855f7, stop:1 #6d28d9);
                    border-radius: 1px;
                }
            """)
            self.progress_bar.hide()
            ai_lay.addWidget(self.progress_bar)
            self.exec_output = QTextEdit()
            self.exec_output.setReadOnly(True)
            self.exec_output.setFont(QFont("Menlo, Consolas, Courier New", 11))
            self.exec_output.setFixedHeight(0)
            self.exec_output.setStyleSheet("""
                QTextEdit { background:#0d1117; color:#f0c040;
                    border:1px solid #30363d; border-radius:8px; padding:10px; }
                QScrollBar:vertical { width:4px; }
                QScrollBar::handle:vertical { background:#30363d; }
            """)
            ai_lay.addWidget(self.exec_output)

            # ── Follow-up chips ───────────────────────────────────────────
            self.followup_frame = QFrame()
            self.followup_frame.setStyleSheet("background:transparent;")
            self.followup_frame.hide()
            self.followup_layout = QHBoxLayout(self.followup_frame)
            self.followup_layout.setContentsMargins(0, 4, 0, 0)
            self.followup_layout.setSpacing(8)
            ai_lay.addWidget(self.followup_frame)

            chat_lay.addWidget(ai_wrap, stretch=1)   # ← ai_wrap expands vertically
            lay.addWidget(chat_area, stretch=1)

            # ── Bottom input bar ──────────────────────────────────────────
            bottom_bar = QFrame()
            bottom_bar.setStyleSheet("""
                QFrame {
                    background: #1a1a2e;
                    border-top: 1.5px solid #2a2a3e;
                }
            """)
            bottom_lay = QVBoxLayout(bottom_bar)
            bottom_lay.setContentsMargins(16, 10, 16, 12)
            bottom_lay.setSpacing(8)

            # Template + snap row
            ctrl_row = QHBoxLayout(); ctrl_row.setSpacing(8)

            tmpl_lbl = QLabel("Template:")
            tmpl_lbl.setFont(QFont("Arial", 9))
            tmpl_lbl.setStyleSheet("color:#475569; background:transparent;")
            ctrl_row.addWidget(tmpl_lbl)

            self.template_combo = QComboBox()
            self.template_combo.addItem("None")
            self.template_combo.addItems(list(PROMPT_TEMPLATES.keys()))
            self.template_combo.setFont(QFont("Arial", 9))
            self.template_combo.setFixedHeight(26)
            self.template_combo.setMaximumWidth(185)
            self.template_combo.setStyleSheet("""
                QComboBox { background:#2a2a3e; color:#a78bfa;
                    border:1px solid #3a3a5e; border-radius:8px; padding:0 10px; }
                QComboBox:hover { border-color:#6d28d9; }
                QComboBox QAbstractItemView { background:#1e1e2e; color:#e2e8f0;
                    selection-background-color:#3a3a5e; border:1px solid #2a2a3e; }
            """)
            self.template_combo.currentTextChanged.connect(self._on_template_select)
            ctrl_row.addWidget(self.template_combo)

            # FIX-C: set /interview as default template index BEFORE the timer
            default_tmpl = SETTINGS.get("default_template", "/interview")
            di = self.template_combo.findText(default_tmpl)
            if di >= 0:
                # Block signals while setting index so _on_template_select
                # doesn't fire before input_box exists
                self.template_combo.blockSignals(True)
                self.template_combo.setCurrentIndex(di)
                self.template_combo.blockSignals(False)

            ctrl_row.addStretch()

            for label, tip, pos in [("↖","TL","tl"),("↗","TR","tr"),("↙","BL","bl"),("↘","BR","br")]:
                b = QPushButton(label); b.setFixedSize(22, 22); b.setToolTip(tip)
                b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                b.setStyleSheet("""
                    QPushButton { background:#2a2a3e; color:#475569;
                        border-radius:4px; border:none; font-size:12px; }
                    QPushButton:hover { background:#3a3a5e; color:#94a3b8; }
                """)
                b.clicked.connect(lambda _, p=pos: self._snap(p))
                ctrl_row.addWidget(b)

            bottom_lay.addLayout(ctrl_row)

            # ── Input pill ────────────────────────────────────────────────
            input_container = QFrame(); input_container.setObjectName("ibox")
            input_container.setStyleSheet("""
                QFrame#ibox {
                    background: #2a2a3e;
                    border: 1.5px solid #3a3a5e;
                    border-radius: 16px;
                }
                QFrame#ibox:focus-within { border-color: #6d28d9; }
            """)
            ic_lay = QHBoxLayout(input_container)
            ic_lay.setContentsMargins(16, 0, 8, 0); ic_lay.setSpacing(8)

            self.input_box = QLineEdit()
            self.input_box.setPlaceholderText(
                "Type or speak your interview question, then press Enter…")
            self.input_box.setFont(QFont("Arial", 13))
            self.input_box.setFixedHeight(48)
            self.input_box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            self.input_box.setStyleSheet("""
                QLineEdit { background:transparent; color:#e2e8f0;
                    border:none; padding:0;
                    selection-background-color:rgba(168,85,247,80); }
                QLineEdit::placeholder { color:#475569; }
            """)
            self.input_box.returnPressed.connect(self._on_send)
            self.input_box.textChanged.connect(self._on_input_changed)
            ic_lay.addWidget(self.input_box)

            # char counter label — shown inside pill right of input
            self.char_counter = QLabel("")
            self.char_counter.setFont(QFont("Arial", 8))
            self.char_counter.setFixedWidth(36)
            self.char_counter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.char_counter.setStyleSheet("color:#3a3a5e; background:transparent;")
            ic_lay.addWidget(self.char_counter)

            # ── PTT mic button ────────────────────────────────────────────
            # Click once = start recording (button shows ⏹ to indicate active)
            # Click again = stop recording and transcribe
            self.mic_btn = QPushButton("🎙")
            self.mic_btn.setFixedSize(34, 34)
            self.mic_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.mic_btn.setToolTip("Click to start recording  (Ctrl+L)\nClick again to stop & transcribe")
            self.mic_btn.setStyleSheet("""
                QPushButton { background:#3a3a5e; color:#94a3b8;
                    border:none; border-radius:10px; font-size:16px; }
                QPushButton:hover { background:#4a4a7e; color:#e2e8f0; }
            """)
            self.mic_btn.clicked.connect(self._on_mic)
            ic_lay.addWidget(self.mic_btn)

            # ── Auto-listen toggle button ─────────────────────────────────────
            # Continuously monitors mic with VAD; transcribes each utterance
            # automatically without any button interaction.
            self.auto_btn = QPushButton("🔁")
            self.auto_btn.setFixedSize(34, 34)
            self.auto_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.auto_btn.setToolTip("Auto-listen OFF — click to enable continuous listening")
            self.auto_btn.setStyleSheet("""
                QPushButton { background:#3a3a5e; color:#94a3b8;
                    border:none; border-radius:10px; font-size:14px; }
                QPushButton:hover { background:#4a4a7e; color:#e2e8f0; }
            """)
            self.auto_btn.clicked.connect(self._on_auto_listen_btn)
            ic_lay.addWidget(self.auto_btn)

            self.send_btn = QPushButton("↑")
            self.send_btn.setFixedSize(34, 34)
            self.send_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.send_btn.setToolTip("Send  Enter")
            self.send_btn.setFont(QFont("Arial", 16, QFont.Weight.Bold))
            self.send_btn.setStyleSheet("""
                QPushButton { background:#6d28d9; color:#fff;
                    border:none; border-radius:10px; }
                QPushButton:hover { background:#7c3aed; }
                QPushButton:pressed { background:#5b21b6; }
            """)
            self.send_btn.clicked.connect(self._on_send)
            ic_lay.addWidget(self.send_btn)

            # ── Physical STOP button — always visible inside the input pill ──
            # Dim when idle, lights up red when generation is running.
            # This is the PRIMARY stop control; Esc key also works.
            self.inline_stop_btn = QPushButton("⏹")
            self.inline_stop_btn.setFixedSize(34, 34)
            self.inline_stop_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.inline_stop_btn.setToolTip("Stop generating  Esc")
            self.inline_stop_btn.setFont(QFont("Arial", 14))
            self.inline_stop_btn.setEnabled(False)   # disabled until generating
            self.inline_stop_btn.setStyleSheet(self._stop_idle_style())
            self.inline_stop_btn.clicked.connect(self._on_stop_generation)
            ic_lay.addWidget(self.inline_stop_btn)

            bottom_lay.addWidget(input_container)

            foot_row = QHBoxLayout(); foot_row.setSpacing(12)

            self.clr_btn = QPushButton("🗑  Clear")
            self.clr_btn.setFixedHeight(24)
            self.clr_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            self.clr_btn.setFont(QFont("Arial", 9))
            self.clr_btn.setStyleSheet("""
                QPushButton { background:transparent; color:#475569; border:none; padding:0 4px; }
                QPushButton:hover { color:#ef4444; }
            """)
            self.clr_btn.clicked.connect(self._clear)
            foot_row.addWidget(self.clr_btn)
            foot_row.addStretch()

            legend = QLabel(
                "Ctrl+L = PTT start/stop  │  Ctrl+A = auto-listen  │  Ctrl+R = retry  │  Ctrl+M = mute  │  Ctrl+Shift+H = hide"
            )
            legend.setFont(QFont("Arial", 8))
            legend.setStyleSheet("color:#334155; background:transparent;")
            foot_row.addWidget(legend)
            bottom_lay.addLayout(foot_row)

            lay.addWidget(bottom_bar)

            # FIX-C: After UI is fully built, pre-fill input box with STAR-T prefix
            # Use QTimer so input_box is guaranteed to exist
            QTimer.singleShot(200, self._apply_default_template)

            return w

        def _apply_default_template(self) -> None:
            """Pre-fill the input box with the default template prefix on startup."""
            default_tmpl = SETTINGS.get("default_template", "/interview")
            if default_tmpl in PROMPT_TEMPLATES:
                tpl = PROMPT_TEMPLATES[default_tmpl]
                if isinstance(tpl, tuple):
                    tpl = tpl[0]
                self.input_box.setPlaceholderText(
                    "Type your interview question after the template prefix, then press Enter…")
                self.input_box.blockSignals(True)
                self.input_box.setText(tpl)
                self.input_box.blockSignals(False)
                self.input_box.setCursorPosition(len(tpl))
                # No setFocus() — don't steal from user's current app

        # ── Style helpers ─────────────────────────────────────────────────────

        @staticmethod
        def _stop_idle_style() -> str:
            """Stop button appearance when no generation is running."""
            return """
                QPushButton {
                    background: #1e1e2e;
                    color: #3a3a5e;
                    border: 1.5px solid #2a2a3e;
                    border-radius: 10px;
                    font-size: 14px;
                }
            """

        @staticmethod
        def _stop_active_style() -> str:
            """Stop button appearance when generation is running — bright red."""
            return """
                QPushButton {
                    background: #7f1d1d;
                    color: #fca5a5;
                    border: 1.5px solid #ef4444;
                    border-radius: 10px;
                    font-size: 14px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background: #991b1b;
                    color: #ffffff;
                    border-color: #f87171;
                }
                QPushButton:pressed {
                    background: #450a0a;
                }
            """

        @staticmethod
        def _mute_style(muted: bool) -> str:
            if muted:
                return """QPushButton { background:#2d1b1b; color:#fca5a5;
                    border:1px solid #7f1d1d; border-radius:8px; font-size:14px; }
                    QPushButton:hover { background:#3f1f1f; }"""
            return """QPushButton { background:transparent; color:#64748b;
                    border:none; border-radius:8px; font-size:14px; }
                    QPushButton:hover { background:#2a2a3e; color:#94a3b8; }"""

        def _small_btn(self, text, bg, fg, fn) -> QPushButton:
            b = QPushButton(text); b.setFixedHeight(28)
            b.setFont(QFont("Arial", 9)); b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setStyleSheet(f"""
                QPushButton {{ background:{bg}; color:{fg}; border-radius:6px;
                    padding:0 10px; border:1px solid rgba(255,255,255,15); }}
                QPushButton:hover {{ color:#ffffff; }}
            """)
            b.clicked.connect(fn); return b

        @staticmethod
        def _div() -> QFrame:
            d = QFrame(); d.setFrameShape(QFrame.Shape.HLine)
            d.setStyleSheet("background:#2a2a3e;"); d.setFixedHeight(1); return d

        # ── Drag — without activating the window ─────────────────────────────

        def mousePressEvent(self, e) -> None:
            if e.button() == Qt.MouseButton.LeftButton:
                self.drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            # FIX: must call super() so Qt routes the click event to child widgets
            # (e.g. focusing input_box when the user clicks on it).
            # Previously suppressed with a comment "would activate the window" —
            # that is not true; super().mousePressEvent does NOT activate the window,
            # it only delivers the event to child widgets for normal interaction.
            super().mousePressEvent(e)

        def mouseMoveEvent(self, e) -> None:
            if e.buttons() == Qt.MouseButton.LeftButton:
                self.move(e.globalPos() - self.drag_pos)
            super().mouseMoveEvent(e)

        # ── Thinking animation ────────────────────────────────────────────────

        def _start_thinking(self) -> None:
            self._think_dots = 0
            self._think_timer.start(400)
            self.stop_btn.show()
            # Physical inline stop button — enable and light up red
            self.inline_stop_btn.setEnabled(True)
            self.inline_stop_btn.setStyleSheet(self._stop_active_style())
            # Animated progress bar
            self.progress_bar.show()

        def _stop_thinking(self) -> None:
            self._think_timer.stop()
            # Only hide stop controls if generation is truly finished
            if not _generation_in_progress.is_set():
                self.stop_btn.hide()
                self.inline_stop_btn.setEnabled(False)
                self.inline_stop_btn.setStyleSheet(self._stop_idle_style())
            self.progress_bar.hide()

        def _tick_thinking(self) -> None:
            self._think_dots = (self._think_dots + 1) % 4
            d = "●" * self._think_dots + "○" * (3 - self._think_dots)
            self.status_label.setText(f"● Thinking {d}")
            self.status_label.setStyleSheet(
                "color:#aa44ff; background:transparent; min-width:140px;")

        # ── Clipboard monitor ─────────────────────────────────────────────────

        def _check_clipboard(self) -> None:
            global _clipboard_last
            self._last_activity = time.time()

            if HAS_OCR and SETTINGS.get("clipboard_monitor"):
                ocr_text = ocr_clipboard_image()
                if ocr_text and ocr_text != _clipboard_last:
                    _clipboard_last = ocr_text
                    if not self.input_box.text().strip():
                        self.input_box.blockSignals(True)
                        self.input_box.setText(ocr_text)
                        self.input_box.blockSignals(False)
                        self.set_status("OCR from image ✓", "#ffaa00")
                        QTimer.singleShot(2000, lambda: self.set_status("Ready", "#00c864"))
                    return

            if not SETTINGS.get("clipboard_monitor"):
                return
            try:
                text = QApplication.clipboard().text().strip()
                if text and text != _clipboard_last and len(text) > 5:
                    _clipboard_last = text
                    if not self.input_box.text().strip():
                        self.input_box.blockSignals(True)
                        self.input_box.setText(text)
                        self.input_box.blockSignals(False)
                        self.set_status("Clipboard pasted ✓", "#ffaa00")
                        QTimer.singleShot(2000, lambda: self.set_status("Ready", "#00c864"))
            except Exception:
                pass

        # ── Auto-clear ────────────────────────────────────────────────────────

        def _check_autoclear(self) -> None:
            mins = SETTINGS.get("auto_clear_minutes", 0)
            if mins <= 0: return
            if time.time() - self._last_activity > mins * 60:
                self._clear()
                self.set_status("Auto-cleared (inactivity)", "#555555")

        # ── Snap ─────────────────────────────────────────────────────────────

        def _snap(self, pos: str) -> None:
            s = QApplication.primaryScreen().geometry()
            W, H = self.width(), self.height(); m = 20
            positions = {
                "tl": (m, m),
                "tr": (s.width()-W-m, m),
                "bl": (m, s.height()-H-60),
                "br": (s.width()-W-m, s.height()-H-60),
            }
            self.move(*positions.get(pos, (m, m)))

        # ── Send / Mic / Mute / Stop ──────────────────────────────────────────

        def _on_send(self) -> None:
            raw = self.input_box.text().strip()
            if not raw: return
            self._last_activity = time.time()
            # Clear stop event HERE — before the prompt enters the queue,
            # so any previous stop state is reset cleanly for this new request.
            _generation_stop_event.clear()

            display_text = raw
            ai_text      = raw
            for key, tpl in PROMPT_TEMPLATES.items():
                if raw.lower().startswith(key + " ") or raw.lower() == key:
                    rest = raw[len(key):].strip()
                    if isinstance(tpl, tuple): tpl = tpl[0]
                    ai_text      = tpl + rest
                    display_text = rest or raw
                    break

            self.input_box.clear()
            # Restore input_box style in case it was amber from listen
            self.input_box.setStyleSheet("""
                QLineEdit { background:transparent; color:#e2e8f0;
                    border:none; padding:0;
                    selection-background-color:rgba(168,85,247,80); }
                QLineEdit::placeholder { color:#475569; }
            """)
            self.template_combo.setCurrentIndex(0)
            self.followup_frame.hide()
            self.exec_output.setFixedHeight(0)
            self.set_prompt(display_text)
            command_queue.put(ai_text)
            self._start_thinking()

        # ── PTT / Auto-listen state ───────────────────────────────────────────
        # _is_listening : True while PTT recording thread is running
        # _ptt_recording: True = mic is open and capturing, False = idle
        # These are set only on the UI thread via signals.

        def _on_mic(self) -> None:
            """Push-to-Talk toggle — always called on the Qt thread.
            First click  → open mic, start recording, button turns ⏹ red.
            Second click → set stop_event; background thread transcribes & fills box.
            """
            if _auto_listen_active.is_set():
                self._show_toast("Auto-listen is on — turn it off first", "#ffaa00")
                return

            if not self._is_listening:
                # ── START: update UI immediately, then start background thread ──
                # Set flag here on Qt thread before the thread starts — avoids race.
                self._is_listening = True
                self._set_mic_active(True)
                threading.Thread(target=self._ptt_thread, daemon=True,
                                 name="ptt-record").start()
            else:
                # ── STOP: signal the recorder to finish ───────────────────────
                _ptt_stop_event.set()
                # Show busy state — _on_ptt_state(False) will restore full idle UI
                self.mic_btn.setText("⏳")
                self.mic_btn.setEnabled(False)
                self.mic_btn.setToolTip("Transcribing…")

        def _ptt_thread(self) -> None:
            """Background thread: records until _ptt_stop_event, then transcribes.
            All UI updates go through signals so they execute on the Qt thread."""
            try:
                text = listen_ptt()   # blocks until _ptt_stop_event is set
                if text:
                    self.communicate.set_input_text.emit(text)
                    self.communicate.update_status.emit(
                        "✏  Review & press Enter to send", "#ffcc00")
                else:
                    self.communicate.update_status.emit("Ready", "#00c864")
            except Exception as exc:
                log(f"_ptt_thread: {exc}")
                self.communicate.update_status.emit("Mic error — check console", "#ff4444")
            finally:
                # FIX: emit signal instead of writing _is_listening directly.
                # The slot _on_ptt_state runs on the Qt thread, avoiding the data race.
                self.communicate.ptt_state_changed.emit(False)

        @pyqtSlot(bool)
        def _on_ptt_state(self, recording: bool) -> None:
            """Qt-thread slot: update _is_listening and button state after PTT ends."""
            self._is_listening = recording
            if not recording:
                self._set_mic_active(False)
                self.mic_btn.setEnabled(True)

        @pyqtSlot(int)
        def _on_ptt_level(self, level: int) -> None:
            """Update mic button opacity/label to show live RMS while recording."""
            if not self._is_listening:
                return
            # Map 0-100 to a simple bar using block chars so user can see audio
            bars = min(5, level // 20)
            bar_str = "█" * bars + "░" * (5 - bars)
            self.mic_btn.setToolTip(f"Recording  {bar_str}  click ⏹ to stop & transcribe")

        @pyqtSlot()
        def _on_mic_reset(self) -> None:
            """Legacy reset slot kept for compatibility."""
            self._set_mic_active(False)

        def _set_mic_active(self, active: bool) -> None:
            if active:
                self.mic_btn.setText("⏹")
                self.mic_btn.setToolTip("Click to stop recording & transcribe")
                self.mic_btn.setStyleSheet(
                    """QPushButton{background:rgba(220,38,38,240);color:#fff;
                    border-radius:10px;border:2px solid rgba(248,113,113,200);
                    font-size:14px;font-weight:bold;}
                    QPushButton:hover{background:rgba(239,68,68,255);}""")
                self.container.setStyleSheet("""QFrame#outer{background:#1e1e2e;
                    border-radius:16px;border:2px solid rgba(240,55,55,190);}""")
            else:
                self.mic_btn.setText("🎙")
                self.mic_btn.setToolTip("Click to start recording  (Ctrl+L)")
                self.mic_btn.setStyleSheet(
                    """QPushButton{background:#3a3a5e;color:#94a3b8;
                    border-radius:10px;font-size:16px;}
                    QPushButton:hover{background:#4a4a7e;color:#e2e8f0;}""")
                self.container.setStyleSheet("""QFrame#outer{background:#1e1e2e;
                    border-radius:16px;border:1px solid #2a2a3e;}""")

        def _on_auto_listen_btn(self) -> None:
            """Toggle continuous auto-listen mode on/off."""
            if _auto_listen_active.is_set():
                stop_auto_listen()
                # UI updated via auto_listen_changed signal emitted by the loop
            else:
                if self._is_listening:
                    self._show_toast("Stop PTT recording first", "#ffaa00")
                    return
                start_auto_listen()
                self.communicate.auto_listen_changed.emit(True)

        @pyqtSlot(bool)
        def _on_auto_listen_changed(self, active: bool) -> None:
            """Update auto-listen button appearance (called from any thread via signal)."""
            if active:
                self.auto_btn.setText("🟢")
                self.auto_btn.setToolTip("Auto-listen ON — click to stop")
                self.auto_btn.setStyleSheet(
                    """QPushButton{background:rgba(5,150,105,220);color:#fff;
                    border-radius:10px;border:2px solid rgba(52,211,153,180);
                    font-size:14px;}
                    QPushButton:hover{background:rgba(4,120,87,240);}""")
            else:
                self.auto_btn.setText("🔁")
                self.auto_btn.setToolTip(
                    "Auto-listen OFF — click to enable continuous listening")
                self.auto_btn.setStyleSheet(
                    """QPushButton{background:#3a3a5e;color:#94a3b8;
                    border-radius:10px;font-size:14px;}
                    QPushButton:hover{background:#4a4a7e;color:#e2e8f0;}""")

        def _on_mute_toggle(self) -> None:
            global _tts_muted
            _tts_muted = not _tts_muted
            SETTINGS["tts_muted"] = _tts_muted
            save_settings(SETTINGS)
            self._update_mute_btn(int(_tts_muted))
            self._show_toast("🔇 Muted" if _tts_muted else "🔊 Unmuted",
                             "#fca5a5" if _tts_muted else "#86efac")

        @pyqtSlot(int)
        def _update_mute_btn(self, muted_int: int) -> None:
            muted = bool(muted_int)
            self.mute_btn.setText("🔇" if muted else "🔊")
            self.mute_btn.setStyleSheet(self._mute_style(muted))
            self.mute_btn.setToolTip("Unmute  Ctrl+M" if muted else "Mute  Ctrl+M")

        def _on_stop_generation(self) -> None:
            if not _generation_in_progress.is_set():
                self._show_toast("Nothing is generating", "#94a3b8")
                return
            _generation_stop_event.set()
            self.inline_stop_btn.setEnabled(False)
            self.inline_stop_btn.setStyleSheet(self._stop_idle_style())
            self.stop_btn.setEnabled(False)
            self.stop_btn.setText("Stopping…")
            self._stop_thinking()
            self._set_status("Stopping…", "#ffaa44")
            self._show_toast("Stopping generation…", "#fbbf24")
            try:
                if _HAS_TTS:
                    if _IS_MACOS:
                        # On macOS we use 'say' subprocess — kill any running instance
                        subprocess.run(["killall", "say"], capture_output=True)
                    elif _tts_engine is not None:
                        _tts_engine.stop()
            except Exception:
                pass
            QTimer.singleShot(2000, lambda: self._set_status("Ready", "#00c864"))
            print("[DEBUG] Generation stop requested by user")

        @pyqtSlot(int)
        def _set_stop_visible(self, visible_int: int) -> None:
            """Called via signal from worker thread.
            1 = generation started → activate stop controls.
            0 = generation finished → deactivate stop controls."""
            if visible_int:
                self.stop_btn.setEnabled(True)
                self.stop_btn.setText("⏹  Stop")
                self.stop_btn.show()
                self.inline_stop_btn.setEnabled(True)
                self.inline_stop_btn.setStyleSheet(self._stop_active_style())
            else:
                self.stop_btn.setEnabled(False)
                self.stop_btn.setText("⏹  Stop")
                self.stop_btn.hide()
                self.inline_stop_btn.setEnabled(False)
                self.inline_stop_btn.setStyleSheet(self._stop_idle_style())

        # ── Action buttons ────────────────────────────────────────────────────

        def _on_copy(self) -> None:
            raw = self.response_text.toPlainText()
            if raw:
                QApplication.clipboard().setText(raw)
                self._show_toast("Copied to clipboard ✓")
                self.copy_btn.setText("✓")
                QTimer.singleShot(1800, lambda: self.copy_btn.setText("⎘"))

        def _on_pin(self) -> None:
            if self._current_prompt and self._current_answer:
                save_pin(self._current_prompt, self._current_answer)
                self._reload_pins()
                self._show_toast("Answer pinned ⭐")
                self.pin_btn.setText("✓")
                QTimer.singleShot(1800, lambda: self.pin_btn.setText("⭐"))

        def _on_rate(self, rating: int) -> None:
            if self._current_prompt:
                save_rating(self._current_prompt, self._current_answer, rating)
                self.set_status(f"{'👍' if rating > 0 else '👎'} Rated", "#ffaa00")
                QTimer.singleShot(1500, lambda: self.set_status("Ready", "#00c864"))

        def _on_run_code(self) -> None:
            raw    = self.response_text.toPlainText()
            blocks = extract_code_blocks(raw)
            if not blocks:
                self.set_status("No code blocks found", "#ffaa00"); return
            lang, code = blocks[0]
            if lang not in ("python", "py", ""):
                self.set_status(f"Can only run Python (found: {lang})", "#ffaa00"); return
            self.set_status("Running code…", "#aa44ff")
            def _run():
                out = run_code_sandbox(code)
                # FIX-G: removed spurious set_input_text.emit("") that cleared input box
                self.exec_output.setFixedHeight(120)
                self.exec_output.setPlainText(f"$ python\n{out}")
                self.set_status("Code executed ✓", "#00c864")
            threading.Thread(target=_run, daemon=True).start()

        def _on_export(self) -> None:
            if not _conv_history: return
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Conversation",
                str(Path.home() / f"ai_conversation_{ts}.md"),
                "Markdown (*.md);;Text (*.txt)")
            if not path: return
            lines = [f"# AI Conversation — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
            for msg in _conv_history:
                role = "**You**" if msg["role"] == "user" else "**Assistant**"
                lines.append(f"\n{role}:\n{msg['content']}\n")
            lines.append(f"\n---\n*Session tokens: ~{_session_tokens}*\n")
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self._show_toast("Exported ✓", "#00aaff")
            self.set_status("Exported ✓", "#00aaff")
            QTimer.singleShot(2500, lambda: self.set_status("Ready", "#00c864"))

        def _on_retry(self) -> None:
            """Re-send the last question. Ctrl+R."""
            if not self._current_prompt:
                self._show_toast("No previous question to retry", "#ffaa00")
                return
            self.input_box.setText(self._current_prompt)
            self._on_send()

        def _on_input_changed(self, text: str) -> None:
            """Update char counter and reset inactivity timer on every keystroke."""
            setattr(self, "_last_activity", time.time())
            n = len(text)
            if n == 0:
                self.char_counter.setText("")
                self.char_counter.setStyleSheet("color:#3a3a5e; background:transparent;")
            elif n > 500:
                self.char_counter.setText(f"{n}")
                self.char_counter.setStyleSheet("color:#f87171; background:transparent;")
            elif n > 200:
                self.char_counter.setText(f"{n}")
                self.char_counter.setStyleSheet("color:#fbbf24; background:transparent;")
            else:
                self.char_counter.setText(f"{n}")
                self.char_counter.setStyleSheet("color:#475569; background:transparent;")

        def _show_toast(self, message: str, color: str = "#00c864") -> None:
            """Show a floating toast notification that fades after 2.5 s."""
            toast = QLabel(message, self)
            toast.setFont(QFont("Arial", 11, QFont.Weight.Bold))
            toast.setStyleSheet(f"""
                QLabel {{
                    background: rgba(18,18,30,235);
                    color: {color};
                    border: 1.5px solid {color};
                    border-radius: 10px;
                    padding: 8px 18px;
                }}
            """)
            toast.adjustSize()
            # Center horizontally, 80px from bottom
            tx = (self.width() - toast.width()) // 2
            ty = self.height() - toast.height() - 80
            toast.move(tx, ty)
            toast.raise_()
            toast.show()
            # Fade out and destroy after 2.5 s
            QTimer.singleShot(2500, toast.deleteLater)

        def _on_template_select(self, key: str) -> None:
            if key == "None" or key not in PROMPT_TEMPLATES: return
            if not hasattr(self, "input_box"): return
            tpl = PROMPT_TEMPLATES[key]
            if isinstance(tpl, tuple): tpl = tpl[0]
            self.input_box.setText(tpl)
            self.input_box.setCursorPosition(len(tpl))
            # Raise and focus so user can type immediately after selecting template
            self._show()
            self.input_box.setFocus(Qt.FocusReason.OtherFocusReason)

        def _on_history_click(self, item: QListWidgetItem) -> None:
            idx = self.history_list.row(item)
            if 0 <= idx < len(self._history_items):
                e = self._history_items[idx]
                self.prompt_text.setPlainText(e.get("prompt", ""))
                self._render_response(e.get("response", ""))

        def _on_pin_click(self, item: QListWidgetItem) -> None:
            pins = load_pins(); idx = self.pins_list.row(item)
            if 0 <= idx < len(pins):
                p = pins[idx]
                self.prompt_text.setPlainText(p.get("prompt", ""))
                self._render_response(p.get("answer", ""))

        def _show_sidebar_tab(self, tab: str) -> None:
            self.sidebar_stack.setCurrentIndex(0 if tab == "history" else 1)
            self.hist_tab_btn.setChecked(tab == "history")
            self.pins_tab_btn.setChecked(tab == "pins")

        def _toggle_search(self) -> None:
            self._search_visible = not self._search_visible
            if self._search_visible:
                self.search_bar.show()
                # Only grab focus if overlay already has it
                if self.isActiveWindow():
                    self.search_bar.setFocus()
            else:
                self.search_bar.hide(); self.search_bar.clear()
                self._filter_history("")

        def _filter_history(self, text: str) -> None:
            for i in range(self.history_list.count()):
                item = self.history_list.item(i)
                item.setHidden(False if not text else
                               text.lower() not in item.text().lower())

        def _reload_pins(self) -> None:
            self.pins_list.clear()
            for p in load_pins():
                short = p.get("prompt", "")[:45] + ("…" if len(p.get("prompt",""))>45 else "")
                self.pins_list.addItem(QListWidgetItem(f"⭐ {short}"))

        def _open_settings(self) -> None:
            if self._settings_panel is None:
                self._settings_panel = SettingsPanel(self)
                # FIX: WindowDoesNotAcceptFocus removed — would block all inputs in settings panel
                self._settings_panel.setWindowFlags(
                    Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                    | Qt.WindowType.Tool)
                self._settings_panel.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self._settings_panel.show()
            _raise_no_activate(self._settings_panel.winId())

        def _on_close(self) -> None:
            shutdown_event.set()
            if _qt_app: _qt_app.quit()

        def _panic(self) -> None:
            global _panic_hidden
            if not _panic_hidden:
                _panic_hidden = True
                try:
                    if _HAS_TTS:
                        if _IS_MACOS:
                            subprocess.run(["killall", "say"], capture_output=True)
                        elif _tts_engine is not None:
                            _tts_engine.stop()
                except Exception: pass
                self._stop_thinking(); self._streaming = False
                self.prompt_text.clear(); self.response_text.clear()
                self.input_box.clear()
                self.setWindowOpacity(0.0); self.is_visible = False
                print("[PANIC] Hidden")
            else:
                _panic_hidden = False; self.is_visible = True
                self.setWindowOpacity(SETTINGS["opacity"] / 100)
                self._set_status("Ready", "#00c864")
                _raise_no_activate(self.winId())
                # Do not call activateWindow — user's current app keeps focus

        # ── Follow-up chips ───────────────────────────────────────────────────

        @pyqtSlot(list)
        def _set_followups(self, suggestions: list) -> None:
            while self.followup_layout.count():
                ww = self.followup_layout.takeAt(0).widget()
                if ww: ww.deleteLater()
            if not suggestions:
                self.followup_frame.hide(); return
            for s in suggestions[:3]:
                chip = QPushButton(f"↩ {s}")
                chip.setFont(QFont("Menlo", 9))
                chip.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                chip.setToolTip(s)
                chip.setStyleSheet("""QPushButton{background:rgba(0,80,40,180);color:#88ffcc;
                    border:1px solid rgba(0,140,70,150);border-radius:12px;padding:4px 10px;}
                    QPushButton:hover{background:rgba(0,110,55,220);}""")
                chip.clicked.connect(lambda _, q=s: self._send_followup(q))
                self.followup_layout.addWidget(chip)
            self.followup_layout.addStretch()
            self.followup_frame.show()

        def _send_followup(self, question: str) -> None:
            self.input_box.setText(question); self._on_send()

        # ── Keyboard shortcuts ────────────────────────────────────────────────

        def keyPressEvent(self, e) -> None:
            key  = e.key()
            ctrl = bool(e.modifiers() & Qt.KeyboardModifier.ControlModifier)
            shft = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)

            if key == Qt.Key.Key_L and ctrl:            self._on_mic();           e.accept(); return
            if key == Qt.Key.Key_A and ctrl:            self._on_auto_listen_btn(); e.accept(); return
            if key == Qt.Key.Key_M and ctrl:            self._on_mute_toggle();   e.accept(); return
            if key == Qt.Key.Key_Q and ctrl:            self._on_close();         e.accept(); return
            if key == Qt.Key.Key_D and ctrl:            self._clear();            e.accept(); return
            if key == Qt.Key.Key_E and ctrl:            self._on_export();        e.accept(); return
            if key == Qt.Key.Key_R and ctrl and shft:   self._on_run_code();      e.accept(); return
            if key == Qt.Key.Key_R and ctrl and not shft: self._on_retry();       e.accept(); return
            if key == Qt.Key.Key_F and ctrl:            self._toggle_search();    e.accept(); return
            if key == Qt.Key.Key_H and ctrl and shft:   self._panic();            e.accept(); return
            if key == Qt.Key.Key_C and ctrl and not self.input_box.hasFocus():
                self._on_copy(); e.accept(); return
            if key == Qt.Key.Key_Space and not self.input_box.hasFocus():
                if not self._ptt_active and not self._is_listening:
                    self._ptt_active = True; self._on_mic()
                e.accept(); return
            if key == Qt.Key.Key_Escape:
                # Stop generation if running (check inline_stop_btn which is always present)
                if self.inline_stop_btn.isEnabled():
                    self._on_stop_generation(); e.accept(); return
                if self.input_box.hasFocus():
                    self.input_box.clear()
                else:
                    # Refocus input_box so the user can type straight away
                    self.input_box.setFocus(Qt.FocusReason.OtherFocusReason)
                e.accept(); return
            super().keyPressEvent(e)

        def keyReleaseEvent(self, e) -> None:
            if e.key() == Qt.Key.Key_Space and not self.input_box.hasFocus():
                self._ptt_active = False; e.accept(); return
            super().keyReleaseEvent(e)

        # ── Thread-safe public API ────────────────────────────────────────────

        def set_status(self, s: str, c: str = "#00c864") -> None:
            self.communicate.update_status.emit(s, c)
        def set_prompt(self, t: str) -> None:
            self.communicate.update_prompt.emit(t)
        def set_response(self, t: str) -> None:
            self.communicate.update_response.emit(t)
        def append_token(self, t: str) -> None:
            self.communicate.append_token.emit(t)
        def clear(self) -> None:
            self.communicate.clear_text.emit()
        def show(self) -> None:
            self.communicate.show_window.emit()
        def hide(self) -> None:
            self.communicate.hide_window.emit()
        def set_input(self, t: str) -> None:
            self.communicate.set_input_text.emit(t)
        def set_mute(self, m: bool) -> None:
            self.communicate.set_mute_btn.emit(int(m))
        def show_stop_btn(self) -> None:
            self.communicate.set_stop_btn.emit(1)   # FIX-A: emit int 1
        def hide_stop_btn(self) -> None:
            self.communicate.set_stop_btn.emit(0)   # FIX-A: emit int 0
        def update_tokens(self, n: int) -> None:
            self.communicate.update_tokens.emit(n)
        def add_history(self, prompt: str, response: str) -> None:
            short = prompt[:42] + ("…" if len(prompt) > 42 else "")
            self.communicate.add_history_item.emit(short, response)
            self._history_items.append({"prompt": prompt, "response": response})
            if len(self._history_items) > 200:
                self._history_items = self._history_items[-200:]
        def trigger_panic(self) -> None:
            self.communicate.panic_toggle.emit()
        def set_followups(self, suggestions: list) -> None:
            self.communicate.set_followups.emit(list(suggestions))

        # ── Qt-thread slots ───────────────────────────────────────────────────

        @pyqtSlot(str, str)
        def _set_status(self, s: str, c: str) -> None:
            # Only stop the thinking animation when generation is NOT in progress.
            # If we called _stop_thinking() unconditionally, every intermediate
            # status update ("Searching web…", "Thinking…") would hide the stop
            # button and kill the progress bar mid-generation.
            if not _generation_in_progress.is_set():
                self._stop_thinking()
            self.status_label.setText(f"● {s}")
            self.status_label.setStyleSheet(
                f"color:{c}; background:transparent; min-width:160px; font-weight:bold;")

        @pyqtSlot(str)
        def _set_prompt(self, t: str) -> None:
            self._current_prompt = t
            self.prompt_text.setPlainText(t)
            # Clear the response area immediately so the old answer
            # is never visible alongside the new question.
            self.response_text.clear()
            self._current_answer = ""
            self._streaming      = False
            self.exec_output.setFixedHeight(0)
            self.followup_frame.hide()

        def _render_response(self, text: str) -> None:
            self.response_text.setHtml(render_markdown(text))
            # After markdown render, scroll to top so user reads from beginning
            sb = self.response_text.verticalScrollBar()
            sb.setValue(0)
            self._user_scrolled_up = False

        @pyqtSlot(str)
        def _set_response(self, t: str) -> None:
            self._stop_thinking()
            _rendering_event.set()
            self._streaming      = False
            self._current_answer = t
            self._render_response(t)
            if SETTINGS.get("auto_reveal_on_response", False) and not self.is_visible:
                self._show()
            QTimer.singleShot(300, _rendering_event.clear)

        @pyqtSlot(str)
        def _append_token(self, t: str) -> None:
            if not self._streaming:
                self._streaming = True
                self._stop_thinking()
                self._current_answer = ""
                self.response_text.clear()
                self._user_scrolled_up = False   # reset scroll tracking for new response
                _rendering_event.set()
                if SETTINGS.get("auto_reveal_on_response", False) and not self.is_visible:
                    self._show()

            self._current_answer += t

            if t.endswith("*⏹ Stopped.*") or "⏹ Stopped" in t:
                self._streaming = False
                self._render_response(self._current_answer)
                QTimer.singleShot(300, _rendering_event.clear)
                return

            # Insert token at cursor position
            self.response_text.moveCursor(QTextCursor.MoveOperation.End)
            cursor = self.response_text.textCursor()
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#00c864"))
            fmt.setFont(QFont("Menlo", SETTINGS["font_size"]))
            cursor.insertText(t, fmt)

            # Auto-scroll to follow generation — but only if user hasn't scrolled up
            if not self._user_scrolled_up:
                sb = self.response_text.verticalScrollBar()
                sb.setValue(sb.maximum())
                self.response_text.ensureCursorVisible()

        @pyqtSlot()
        def _clear(self) -> None:
            self._stop_thinking()
            self.prompt_text.clear(); self.response_text.clear()
            self.input_box.clear();   self.exec_output.setFixedHeight(0)
            self.followup_frame.hide(); self._streaming = False
            self._current_prompt = ""; self._current_answer = ""
            self._set_status("Ready", "#00c864")

        def _clear_history(self) -> None:
            _conv_history.clear()
            self.history_list.clear(); self._history_items.clear()
            self._clear()
            self.set_status("History cleared", "#ffaa00")
            QTimer.singleShot(2000, lambda: self.set_status("Ready", "#00c864"))

        @pyqtSlot(str)
        def _set_input(self, t: str) -> None:
            """Fill input box after voice transcription and focus it so the
            user can immediately press Enter to send."""
            # Do NOT blockSignals — textChanged must fire so char counter updates
            self.input_box.setText(t)
            self.input_box.setCursorPosition(len(t))
            # Focus input box so Enter sends immediately after listen
            self.input_box.setFocus(Qt.FocusReason.OtherFocusReason)
            # Amber highlight for 3 s so user sees transcribed text is ready
            self.input_box.setStyleSheet("""
                QLineEdit { background:rgba(255,170,0,25); color:#e2e8f0;
                    border:none; padding:0;
                    selection-background-color:rgba(168,85,247,80); }
                QLineEdit::placeholder { color:#475569; }
            """)
            QTimer.singleShot(3000, lambda: self.input_box.setStyleSheet("""
                QLineEdit { background:transparent; color:#e2e8f0;
                    border:none; padding:0;
                    selection-background-color:rgba(168,85,247,80); }
                QLineEdit::placeholder { color:#475569; }
            """))
            # BUG FIX 4: auto_send setting was never applied after voice transcription.
            # The setting existed with a UI checkbox but had no effect. Now if enabled,
            # a short delay lets the user see the transcribed text before it auto-sends.
            if SETTINGS.get("auto_send") and t.strip():
                QTimer.singleShot(600, self._on_send)

        @pyqtSlot(int)
        def _update_tokens(self, n: int) -> None:
            turns = len(_conv_history) // 2
            self.token_lbl.setText(f"~{n:,} tok  •  {turns} turns")

        @pyqtSlot(str, str)
        def _add_history(self, short: str, _r: str) -> None:
            item = QListWidgetItem(short)
            item.setToolTip(short)
            self.history_list.addItem(item)
            self.history_list.scrollToBottom()

        def _keepalive(self) -> None:
            """Keep passive flags intact without repeatedly re-raising the window."""
            if not self.is_visible or _panic_hidden:
                return
            # FIX: WindowDoesNotAcceptFocus removed from enforced flags.
            flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                     | Qt.WindowType.Tool)
            if self.windowFlags() != flags:
                self.setWindowFlags(flags)
                self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                super().show()
            if SETTINGS.get("aggressive_keepalive", False):
                _raise_no_activate(self.winId())

        @pyqtSlot()
        def _show(self) -> None:
            self.is_visible = True
            # FIX: WindowDoesNotAcceptFocus removed — see _init_ui comment.
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool
            )
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_AlwaysStackOnTop)
            try:
                self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
            except Exception:
                pass
            super().show()
            opacity = SETTINGS.get("opacity", 96) / 100
            self.setWindowOpacity(opacity)
            _apply_screen_share_invisibility(self.winId())
            _raise_no_activate(self.winId())

        @pyqtSlot()
        def _hide(self) -> None:
            """Hide by opacity only — keeps window in memory for instant re-show."""
            self.is_visible = False
            self.setWindowOpacity(0.0)

        def _apply_font_size(self, fs: int) -> None:
            f = QFont("Menlo", fs)
            self.prompt_text.setFont(f); self.response_text.setFont(f)
            self.input_box.setFont(f)
            self.history_list.setFont(QFont("Menlo", max(8, fs-3)))

        def changeEvent(self, e) -> None:
            if e.type() == QEvent.Type.WindowStateChange:
                if self.windowState() & Qt.WindowState.WindowMinimized:
                    self.setWindowState(Qt.WindowState.WindowNoState)
                    _raise_no_activate(self.winId())
            super().changeEvent(e)

        def closeEvent(self, e) -> None:
            e.ignore()


# =============================================================================
#  SYSTEM TRAY
# =============================================================================

def _create_tray() -> None:
    global _tray_icon
    if not HAS_PYQT6 or not QSystemTrayIcon.isSystemTrayAvailable():
        return
    try:
        px = QPixmap(32, 32); px.fill(QColor(0, 0, 0, 0))
        p  = QPainter(px); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(0, 200, 100)); p.setPen(QColor(0, 140, 65))
        p.drawEllipse(4, 4, 24, 24); p.end()
        icon = QIcon(px)

        _tray_icon = QSystemTrayIcon(icon)
        menu = QMenu()

        for act in [
            ("Show",           lambda: _overlay_window and _overlay_window.show()),
            ("Hide",           lambda: _overlay_window and _overlay_window.hide()),
            None,
            ("🎙 Listen",      lambda: threading.Thread(target=on_trigger, daemon=True).start()),
            ("🔇 Toggle Mute", _global_mute_toggle),
            ("🚨 Panic",       lambda: _overlay_window and _overlay_window.trigger_panic()),
            None,
            ("⚙ Settings",    lambda: _overlay_window and _overlay_window._open_settings()),
            ("⬇ Export",      lambda: _overlay_window and _overlay_window._on_export()),
            None,
            ("Quit",           lambda: (shutdown_event.set(), _qt_app and _qt_app.quit())),
        ]:
            if act is None:
                menu.addSeparator()
            else:
                label, fn = act
                a = QAction(label); a.triggered.connect(fn); menu.addAction(a)

        _tray_icon.setContextMenu(menu)
        _tray_icon.setToolTip("AI Stealth Assistant")
        _tray_icon.activated.connect(
            lambda r: (_overlay_window and _overlay_window.show())
            if r == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        _tray_icon.show()
        print("[INFO] System tray active")
    except Exception as exc:
        print(f"[WARNING] Tray: {exc}")


def _global_mute_toggle() -> None:
    global _tts_muted
    _tts_muted = not _tts_muted
    SETTINGS["tts_muted"] = _tts_muted
    save_settings(SETTINGS)
    if _overlay_window:
        _overlay_window.set_mute(_tts_muted)
    print(f"[INFO] TTS {'muted' if _tts_muted else 'unmuted'}")


# =============================================================================
#  OVERLAY FACTORY
# =============================================================================

def create_overlay_ui() -> "Optional[StealthOverlay]":
    global _overlay_window, _qt_app
    if not HAS_PYQT6:
        print("[ERROR] PyQt6 not available"); return None

    # FIX-F: parenthesised multi-line condition (was broken backslash continuation)
    if (platform.system() == "Linux"
            and not os.environ.get("DISPLAY")
            and not os.environ.get("WAYLAND_DISPLAY")):
        print("[WARNING] No DISPLAY/WAYLAND_DISPLAY — headless, no overlay")
        return None

    try:
        if QApplication.instance() is None:
            # M1 macOS: set Retina / HiDPI attributes BEFORE constructing QApplication
            if platform.system() == "Darwin":
                QApplication.setHighDpiScaleFactorRoundingPolicy(
                    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
            _qt_app = QApplication(sys.argv)
            _qt_app.setQuitOnLastWindowClosed(False)
            if platform.system() == "Darwin":
                # Prevent macOS from intercepting Cmd+Q and other menu shortcuts
                _qt_app.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, True)
        else:
            _qt_app = QApplication.instance()
        _overlay_window = StealthOverlay()
        _create_tray()
        return _overlay_window
    except Exception as exc:
        import traceback
        print(f"[ERROR] Overlay: {exc}"); traceback.print_exc()
        return None


# =============================================================================
#  HOTKEYS
# =============================================================================

def _to_pynput_hotkey(hotkey: str) -> str:
    m = {"ctrl":"<ctrl>","control":"<ctrl>","shift":"<shift>",
         "alt":"<alt>","option":"<alt>","cmd":"<cmd>","command":"<cmd>",
         "win":"<cmd>","windows":"<cmd>","space":"<space>","enter":"<enter>"}
    parts = [p.strip().lower() for p in hotkey.split("+") if p.strip()]
    return "+".join(m.get(p, p) for p in parts)


def register_hotkey() -> bool:
    global _pynput_listener
    if pynput_keyboard is None:
        log("pynput not installed."); return False

    def _safe(fn):
        def wrapper(*args, **kwargs):
            try: fn()
            except Exception as exc: log(f"Hotkey: {exc}")
        return wrapper

    try:
        hs = _to_pynput_hotkey(getattr(config, "HOTKEY", "ctrl+shift+space"))

        def _listen():
            if _overlay_window: _overlay_window.show()
            threading.Thread(target=on_trigger, daemon=True).start()

        def _screenshot_vision():
            def _run():
                img = capture_screenshot_b64()
                if img: command_queue.put("/vision Describe what you see on my screen")
            threading.Thread(target=_run, daemon=True).start()

        hotkeys = {
            hs:                  _safe(lambda: threading.Thread(target=on_trigger, daemon=True).start()),
            "<ctrl>+l":          _safe(_listen),           # ← matches UI shortcut Ctrl+L
            "<ctrl>+<shift>+l":  _safe(_listen),           # ← legacy alias kept
            "<ctrl>+<shift>+m":  _safe(_global_mute_toggle),
            "<ctrl>+<shift>+h":  _safe(lambda: _overlay_window and _overlay_window.trigger_panic()),
            "<ctrl>+<shift>+s":  _safe(_screenshot_vision),
        }

        _pynput_listener = pynput_keyboard.GlobalHotKeys(hotkeys)
        _pynput_listener.start()
        print("[INFO] Global hotkeys registered")
        return True

    except Exception as exc:
        print(f"[WARNING] Global hotkeys unavailable: {exc}")
        print("[TIP] pip install --upgrade pynput")
        return False


def cleanup_hotkeys() -> None:
    global _pynput_listener
    if _pynput_listener:
        try: _pynput_listener.stop()
        except Exception: pass


# =============================================================================
#  WAKE-WORD LOOP
# =============================================================================

def wake_word_loop() -> None:
    if not getattr(config, "USE_WAKE_WORD", False): return
    if pvporcupine is None or not getattr(config, "PORCUPINE_ACCESS_KEY", ""): return
    try:
        import pyaudio
    except Exception: return
    porcupine = pa = stream = None
    try:
        porcupine = pvporcupine.create(
            access_key=config.PORCUPINE_ACCESS_KEY,
            keywords=getattr(config, "WAKE_WORD_KEYWORDS", ["computer"]))
        pa     = pyaudio.PyAudio()
        stream = pa.open(rate=porcupine.sample_rate, channels=1,
                         format=pyaudio.paInt16, input=True,
                         frames_per_buffer=porcupine.frame_length)
        while not shutdown_event.is_set():
            pcm    = stream.read(porcupine.frame_length, exception_on_overflow=False)
            result = porcupine.process(memoryview(pcm).cast("h"))
            if result >= 0:
                if _overlay_window: _overlay_window.show()
                text = listen_once(timeout=getattr(config, "LISTEN_TIMEOUT_SECONDS", 8))
                if text:
                    if _overlay_window: _overlay_window.set_input(text)
                    else: command_queue.put(text)
                time.sleep(0.2)
    except Exception as exc:
        log(f"Wake-word: {exc}")
    finally:
        for obj, fn in [(stream, lambda o: (o.stop_stream(), o.close())),
                        (pa,     lambda o: o.terminate()),
                        (porcupine, lambda o: o.delete())]:
            if obj:
                try: fn(obj)
                except Exception: pass


# =============================================================================
#  TERMINAL FALLBACK
# =============================================================================

def typed_input_loop() -> None:
    print("\n" + "=" * 68)
    print("  🤖  AI Stealth Assistant  —  Ultra Full Featured")
    print("=" * 68)
    print("  ⚡ STT:    faster-whisper" if HAS_FASTER_WHISPER else "  ⚠  STT: Google")
    print("  🌐 Search: DuckDuckGo enabled" if HAS_DDG else "  ⚠  Search: disabled")
    print("  📚 RAG:    ChromaDB ready" if HAS_CHROMA else "  ⚠  RAG:    disabled")
    print("  🎨 Markdown rendering" if HAS_MARKDOWN else "  ⚠  Markdown disabled")
    print(f"\n  Model:  {SETTINGS['model']}  |  Persona: {SETTINGS['persona']}")
    print(f"  Settings: {SETTINGS_FILE}")
    print("\n  Terminal commands: q  show  hide  clear  mute  unmute  test")
    print("  ai <message>  — send message to AI")
    print("  search <q>    — web search")
    print("=" * 68 + "\n")

    TERMINAL_COMMANDS = {
        "q","quit","exit","show","hide","clear","mute","unmute",
        "history","export","test","cache-stats","cache-clear","vision","help","?",
    }

    while not shutdown_event.is_set():
        try:
            typed = input("Term> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not typed: continue
        cmd = typed.lower()

        if cmd in ("q", "quit", "exit"):
            shutdown_event.set()
            if _qt_app: _qt_app.quit()
            break
        elif cmd == "show":
            if _overlay_window: _overlay_window.show()
        elif cmd == "hide":
            if _overlay_window: _overlay_window.hide()
        elif cmd == "clear":
            if _overlay_window: _overlay_window.clear()
            _conv_history.clear(); print("✓ Cleared")
        elif cmd == "mute":
            global _tts_muted
            _tts_muted = True; SETTINGS["tts_muted"] = True; save_settings(SETTINGS)
            if _overlay_window: _overlay_window.set_mute(True); print("✓ Muted 🔇")
        elif cmd == "unmute":
            _tts_muted = False; SETTINGS["tts_muted"] = False; save_settings(SETTINGS)
            if _overlay_window: _overlay_window.set_mute(False); print("✓ Unmuted 🔊")
        elif cmd == "history":
            for i, m in enumerate(_conv_history):
                print(f"  [{i}] {'You' if m['role']=='user' else 'AI'}: {m['content'][:80]}")
        elif cmd == "export":
            if _overlay_window: _overlay_window._on_export()
        elif cmd == "cache-stats":
            print(f"  Cache: {len(_response_cache)} entries")
        elif cmd == "cache-clear":
            _response_cache.clear(); _save_cache(); print("✓ Cache cleared")
        elif cmd == "vision":
            command_queue.put("/vision What do you see on my screen?")
            if _overlay_window: _overlay_window.show()
        elif cmd in ("help", "?"):
            print("  q  show  hide  clear  mute  unmute  history  export")
            print("  test  search <q>  vision  cache-stats  cache-clear  ai <msg>")
        elif cmd.startswith("search "):
            query = typed[7:].strip()
            if query:
                command_queue.put(f"/search {query}")
                if _overlay_window: _overlay_window.show()
        elif cmd == "test":
            if _overlay_window:
                _overlay_window.set_status("System OK ✓", "#00c864")
        elif cmd.startswith("ai "):
            message = typed[3:].strip()
            if message:
                command_queue.put(message)
                if _overlay_window: _overlay_window.show()
                print(f"  → Sent: {message[:60]}")
        elif len(typed) <= 3 and cmd not in TERMINAL_COMMANDS:
            print(f"  ⚠  '{typed}' ignored. Use overlay or 'ai <message>'.")
        elif len(typed) >= 10 or cmd.startswith("/"):
            command_queue.put(typed)
            if _overlay_window: _overlay_window.show()


# =============================================================================
#  ENTRY POINT
# =============================================================================

def main() -> None:
    print("\n[INFO] Starting AI Stealth Assistant…")
    print(f"[INFO] Model: {SETTINGS['model']}  Persona: {SETTINGS['persona']}  "
          f"STT: {SETTINGS.get('stt_engine')}  TTS: {SETTINGS.get('tts_engine')}")

    # PERF: Kick off Whisper model loading immediately in a background thread so
    # the first voice trigger never blocks waiting for the model to load.
    _preload_whisper_model()

    # ── M1 / macOS dependency diagnostics ─────────────────────────────────────
    if _IS_MACOS:
        print("\n[DIAG] macOS / Apple Silicon dependency check:")
        try:
            import sounddevice as _sd
            devs = _sd.query_devices()
            inputs = [d for d in devs if d.get("max_input_channels", 0) > 0]
            print(f"  ✅ sounddevice  — {len(inputs)} input device(s) found")
        except ImportError:
            print("  ❌ sounddevice  — NOT installed.  Run: pip install sounddevice")
        except Exception as e:
            print(f"  ⚠️  sounddevice  — installed but error: {e}")

        try:
            import numpy
            print(f"  ✅ numpy        — {numpy.__version__}")
        except ImportError:
            print("  ❌ numpy        — NOT installed.  Run: pip install numpy")

        if HAS_FASTER_WHISPER:
            print(f"  ✅ faster-whisper — ready")
        else:
            print("  ⚠️  faster-whisper — not installed (will use Google STT)")
            print("     Run: pip install faster-whisper")

        if _PYTTSX3_AVAILABLE:
            print("  ✅ pyttsx3      — available (using macOS 'say' instead)")
        else:
            print("  ⚠️  pyttsx3      — not installed (TTS via 'say' command only)")

        # Test 'say' command
        try:
            r = subprocess.run(["say", "--version"], capture_output=True, timeout=3)
            print("  ✅ say command  — available for TTS")
        except Exception:
            print("  ⚠️  say command  — not found (unexpected on macOS)")
        print()
    # ── end diagnostics ───────────────────────────────────────────────────────

    overlay = create_overlay_ui()

    if overlay:
        overlay._show()
        # FIX-B: cast to int before calling int-typed slot
        overlay._update_mute_btn(int(_tts_muted))

        overlay.prompt_text.setPlainText(
            "Welcome!  Default persona: Interviewer (STAR-T) 🎤")

        # FIX-C: build welcome HTML that highlights the STAR-T framework clearly
        _m  = SETTINGS.get("model", "phi3:mini")
        _p  = SETTINGS.get("persona", "interviewer")
        welcome_html = (
            "<style>"
            "body{color:#e2e8f0;font-family:'Segoe UI',Arial,sans-serif;"
            "font-size:14px;line-height:1.7;background:transparent;margin:0;padding:8px 4px;}"
            "h2{color:#a78bfa;margin:0 0 10px;font-size:17px;font-weight:700;}"
            "h3{color:#7dd3fc;margin:12px 0 5px;font-size:12px;font-weight:700;"
            "text-transform:uppercase;letter-spacing:0.08em;}"
            "code{background:#2d2d52;color:#c4b5fd;padding:2px 7px;"
            "border-radius:5px;font-size:12px;font-family:'Menlo',monospace;}"
            "li{margin:3px 0;color:#cbd5e1;}"
            "b{color:#f1f5f9;}"
            ".star{display:inline-block;background:#1a2e1a;color:#86efac;"
            "padding:4px 12px;border-radius:20px;font-size:12px;margin:2px;"
            "font-weight:600;border:1px solid #166534;}"
            ".sub{color:#64748b;font-size:12px;}"
            ".hi{color:#fbbf24;font-weight:600;}"
            "</style>"
            "<h2>✦ AI Interview Assistant — STAR-T Mode Active</h2>"
            f"<p class='sub'>Model: <b>{_m}</b> &nbsp;·&nbsp; "
            f"Persona: <b class='hi'>{_p}</b> &nbsp;·&nbsp; Template: <b>/interview</b></p>"
            "<h3>STAR-T Answer Framework (default for all answers)</h3>"
            "<p>"
            "<span class='star'>S — Situation</span> "
            "<span class='star'>T — Task</span> "
            "<span class='star'>A — Action</span> "
            "<span class='star'>R — Result</span> "
            "<span class='star'>T — Takeaway</span>"
            "</p>"
            "<p class='sub'>Every behavioural answer is automatically structured this way. "
            "Technical questions get: Definition → Example → Trade-offs.</p>"
            "<h3>How to use</h3>"
            "<ol>"
            "<li>Press <b>Ctrl+L</b> or click the 🎙 mic button — speak your question</li>"
            "<li>Review the transcribed text in the input box</li>"
            "<li>Press <b>↑ Enter</b> to send — AI answers in STAR-T format</li>"
            "<li>Press <b>⏹ Stop</b> or <b>Esc</b> to cancel mid-generation</li>"
            "</ol>"
            "<h3>Key shortcuts</h3>"
            "<ul>"
            "<li><code>Ctrl+L</code> — listen &nbsp; "
            "<code>Esc</code> — stop generation &nbsp; "
            "<code>Ctrl+M</code> — mute TTS</li>"
            "<li><code>Ctrl+R</code> — retry last question &nbsp; "
            "<code>Ctrl+Shift+R</code> — run code</li>"
            "<li><code>Ctrl+Shift+H</code> — instant panic hide/show</li>"
            "<li><code>Ctrl+Shift+S</code> — screenshot + vision AI</li>"
            "</ul>"
            "<p class='sub'>Hidden from Zoom &amp; Teams screen share &nbsp;·&nbsp; "
            "Always on top &nbsp;·&nbsp; Never minimises</p>"
        )

        overlay.response_text.setHtml(welcome_html)
        overlay._set_status("Ready  ·  STAR-T mode", "#00c864")
        print("[INFO] Overlay opened — STAR-T interviewer mode active\n")
    else:
        print("[WARNING] No overlay — terminal only\n")

    worker = threading.Thread(target=process_commands, daemon=True)
    worker.start()

    wake_thread = None
    if getattr(config, "USE_WAKE_WORD", False):
        wake_thread = threading.Thread(target=wake_word_loop, daemon=True)
        wake_thread.start()

    if SETTINGS.get("continuous_listen") and HAS_VAD:
        threading.Thread(target=continuous_listen_loop, daemon=True).start()

    if SETTINGS.get("rest_api_enabled"):
        threading.Thread(target=start_rest_api,
                         args=(SETTINGS.get("rest_api_port", 7788),),
                         daemon=True).start()

    register_hotkey()

    input_thread = threading.Thread(target=typed_input_loop, daemon=True)
    input_thread.start()

    try:
        if _qt_app:
            _qt_app.exec()
        else:
            input_thread.join()
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted.")
    finally:
        shutdown_event.set()
        save_settings(SETTINGS)
        _save_cache()
        cleanup_hotkeys()
        worker.join(timeout=2)
        if wake_thread: wake_thread.join(timeout=2)
        input_thread.join(timeout=2)
        print("✓ Assistant stopped.\n")


if __name__ == "__main__":
    main()
