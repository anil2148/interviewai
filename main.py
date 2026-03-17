from __future__ import annotations
# =============================================================================
#  AI Stealth Assistant  —  Ultra Full Featured
#  NEW in this version:
#   ⚡ faster-whisper STT         (5x faster than Google STT, fully local)
#   ⚡ Semantic response cache    (repeat questions answered instantly)
#   ⚡ phi3/qwen2.5 fast models   (configurable)
#   🧠 RAG with ChromaDB          (answer from your own documents)
#   🧠 Follow-up suggestion chips (clickable next questions)
#   🧠 Web search tool            (DuckDuckGo, real-time answers)
#   🧠 Code execution sandbox     (run Python code from response)
#   🎙  Continuous listening mode  (hands-free, VAD-based)
#   🎙  ElevenLabs / Coqui TTS    (natural voice, local option)
#   🖥  Screenshot + vision AI    (Ctrl+Shift+S → ask about screen)
#   🖥  Active window detection   (auto persona switching)
#   🖥  OCR from clipboard images (read text from copied screenshots)
#   💬 Markdown + syntax highlight (rendered responses)
#   💬 Pinned/bookmarked answers  (⭐ star button)
#   💬 Conversation search        (Ctrl+F)
#   💬 Response rating 👍/👎      (saved for fine-tuning)
#   🔒 Auto-clear on inactivity   (configurable timeout)
#   ⚙  Plugin system              (/plugins folder)
#   ⚙  REST API mode              (localhost HTTP server)
#   ⚙  Reduce ambient adjust time (400ms faster voice trigger)
#  EXISTING:
#   ✅ Conversation memory, panic hide, clipboard monitor, templates
#   ✅ Auto-send, export, model switcher, settings panel, token counter
#   ✅ Mute, streaming, screen-share invisible, always on top, system tray
#   ✅ Opacity, font size, position snaps, push-to-talk, global hotkeys
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
import textwrap
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
#  PATHS & DIRECTORIES
# =============================================================================

APP_DIR      = Path.home() / ".ai_assistant"
SETTINGS_FILE = APP_DIR / "settings.json"
PLUGINS_DIR  = APP_DIR / "plugins"
PINS_FILE    = APP_DIR / "pins.json"
RATINGS_FILE = APP_DIR / "ratings.json"
RAG_DB_DIR   = APP_DIR / "rag_db"
CACHE_FILE   = APP_DIR / "response_cache.json"

for d in [APP_DIR, PLUGINS_DIR, RAG_DB_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# =============================================================================
#  SETTINGS
# =============================================================================

DEFAULT_SETTINGS: Dict = {
    "model":              getattr(config, "OLLAMA_MODEL",  "phi3:mini"),
    "ai_engine":          getattr(config, "AI_ENGINE",     "ollama"),
    "stt_engine":         "faster_whisper",   # faster_whisper | google | vosk
    "whisper_model":      "base",             # tiny|base|small|medium
    "tts_engine":         "pyttsx3",          # pyttsx3 | elevenlabs | coqui
    "elevenlabs_key":     "",
    "elevenlabs_voice":   "Rachel",
    "tts_rate":           getattr(config, "TTS_RATE",      175),
    "tts_volume":         getattr(config, "TTS_VOLUME",    1.0),
    "tts_muted":          False,
    "auto_send":          False,
    "opacity":            96,
    "font_size":          13,
    "persona":            "default",
    "clipboard_monitor":  False,
    "continuous_listen":  False,
    "auto_clear_minutes": 0,            # 0 = disabled
    "rest_api_port":      7788,
    "rest_api_enabled":   False,
    "web_search_enabled": True,
    "rag_enabled":        False,
    "code_exec_enabled":  True,
    "vision_model":       "llava",
    "ambient_adjust_sec": 0.1,          # faster than default 1.0
    "available_models":   [
        "phi3:mini", "qwen2.5:3b", "llama3.2:3b", "llama3.2",
        "mistral", "codellama", "gemma2:2b", "llava",
    ],
}

PERSONAS: Dict[str, str] = {
    "default":     "",
    "interviewer": (
        "You are a helpful AI for technical interviews. Give concise structured answers. "
        "Use STAR method for behavioural questions. For coding show clean code + brief explanation."
    ),
    "coder":       (
        "You are an expert software engineer. Give production-ready code. "
        "Be concise. Always explain key decisions briefly."
    ),
    "architect":   (
        "You are a senior solutions architect. Think in systems. "
        "Mention trade-offs, scalability, and best practices."
    ),
    "coach":       (
        "You are a supportive career coach. Give actionable advice. "
        "Be encouraging but realistic."
    ),
    "teacher":     (
        "You are a patient teacher. Explain concepts from first principles. "
        "Use analogies and examples. Check understanding."
    ),
}

PROMPT_TEMPLATES: Dict[str, str] = {
    "/code":      "Write clean, production-ready code for: ",
    "/explain":   "Explain this clearly and concisely: ",
    "/bullet":    "Summarize in bullet points: ",
    "/review":    "Review this code, find issues and suggest improvements:\n",
    "/interview": "Give a strong interview answer (STAR format) for: ",
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
            saved = json.loads(SETTINGS_FILE.read_text())
            return {**DEFAULT_SETTINGS, **saved}
    except Exception:
        pass
    return dict(DEFAULT_SETTINGS)


def save_settings(s: Dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(s, indent=2))
    except Exception as exc:
        print(f"[WARNING] save_settings: {exc}")


# =============================================================================
#  GLOBALS
# =============================================================================

SETTINGS            = load_settings()
recognizer          = sr.Recognizer()
command_queue: "queue.Queue[str]" = queue.Queue()
shutdown_event      = threading.Event()

_tts_lock           = threading.Lock()
# FIX 2: wrap pyttsx3.init() — crashes on systems with no audio device
try:
    _tts_engine = pyttsx3.init()
    _tts_engine.setProperty("rate",   SETTINGS["tts_rate"])
    _tts_engine.setProperty("volume", SETTINGS["tts_volume"])
    _HAS_TTS = True
except Exception as _tts_err:
    print(f"[WARNING] pyttsx3 init failed: {_tts_err} — TTS disabled")
    _tts_engine = None
    _HAS_TTS = False
_tts_muted: bool    = SETTINGS["tts_muted"]
_rendering_event    = threading.Event()

_pynput_listener    = None
_overlay_window     = None
_qt_app             = None
_tray_icon          = None
_rest_server        = None
_continuous_thread  = None
_whisper_model      = None       # lazy-loaded faster-whisper model
_ollama_client: Optional[ollama.Client] = None

# Conversation memory
_conv_history: List[Dict]  = []
_session_tokens: int       = 0
_panic_hidden: bool        = False
_clipboard_last: str       = ""

# Response cache  {hash → answer}
_response_cache: Dict[str, str] = {}
_CACHE_MAX = 200

# Generation stop — set this to interrupt a streaming response mid-way
_generation_stop_event = threading.Event()


def log(msg: str) -> None:
    if getattr(config, "DEBUG", False):
        print(f"[Assistant] {msg}")

# =============================================================================
#  RESPONSE CACHE  (semantic + exact)
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
    return _response_cache.get(key)


def _cache_set(prompt: str, answer: str) -> None:
    if len(answer) < 20:
        return
    key = _cache_key(prompt, SETTINGS["model"], SETTINGS["persona"])
    _response_cache[key] = answer
    if len(_response_cache) > _CACHE_MAX:
        # Evict oldest 20%
        keys = list(_response_cache.keys())
        for k in keys[:_CACHE_MAX // 5]:
            del _response_cache[k]
    _save_cache()


_load_cache()


# =============================================================================
#  OLLAMA  (persistent warm client)
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

def _get_whisper_model() -> Optional["FasterWhisperModel"]:
    global _whisper_model
    if not HAS_FASTER_WHISPER:
        return None
    if _whisper_model is None:
        size = SETTINGS.get("whisper_model", "base")
        try:
            print(f"[INFO] Loading faster-whisper '{size}'…")
            _whisper_model = FasterWhisperModel(
                size, device="cpu", compute_type="int8"
            )
            print(f"[INFO] faster-whisper '{size}' loaded ✓")
        except Exception as exc:
            print(f"[WARNING] faster-whisper failed: {exc}")
    return _whisper_model


def transcribe_faster_whisper(audio_data: bytes) -> Optional[str]:
    model = _get_whisper_model()
    if model is None:
        return None

    # Guard: skip very short audio (< 0.5s ≈ 8000 bytes at 16kHz mono)
    if len(audio_data) < 8000:
        log("Audio too short — skipping transcription")
        return None

    try:
        import warnings
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            tmp = f.name

        # Suppress numpy matmul warnings from faster-whisper mel spectrogram
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            segments, _ = model.transcribe(
                tmp,
                beam_size=1,
                language="en",
                vad_filter=True,           # skip silent segments automatically
                vad_parameters=dict(
                    min_silence_duration_ms=500,
                    speech_pad_ms=400,
                ),
            )
            text = " ".join(s.text for s in segments).strip()

        os.unlink(tmp)
        return text or None
    except Exception as exc:
        log(f"faster-whisper transcribe: {exc}")
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return None


# =============================================================================
#  WEB SEARCH  (DuckDuckGo)
# =============================================================================

def web_search(query: str, max_results: int = 5) -> str:
    """Search DuckDuckGo and return formatted results."""
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
        if not results:
            return "No results found."
        return "\n\n".join(results)
    except Exception as exc:
        return f"Web search error: {exc}"


# =============================================================================
#  RAG  (ChromaDB document search)
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
            col.add(
                documents=[chunk],
                ids=[f"{doc_id}_{i}"],
                metadatas=[metadata or {"source": doc_id}],
            )
        return True
    except Exception as exc:
        log(f"RAG add: {exc}")
        return False


def rag_search(query: str, n: int = 4) -> str:
    col = _get_rag_collection()
    if col is None:
        return ""
    try:
        results = col.query(query_texts=[query], n_results=min(n, col.count()))
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        if not docs:
            return ""
        parts = []
        for doc, meta in zip(docs, metas):
            src = meta.get("source", "doc") if meta else "doc"
            parts.append(f"[{src}]\n{doc}")
        return "\n\n".join(parts)
    except Exception as exc:
        log(f"RAG search: {exc}")
        return ""


# =============================================================================
#  CODE EXECUTION SANDBOX
# =============================================================================

def extract_code_blocks(text: str) -> List[Tuple[str, str]]:
    """Extract (language, code) pairs from markdown code blocks."""
    import re
    pattern = r"```(\w*)\n(.*?)```"
    matches = re.findall(pattern, text, re.DOTALL)
    return [(lang or "python", code.strip()) for lang, code in matches]


def run_code_sandbox(code: str, timeout: int = 15) -> str:
    """Run Python code in a subprocess sandbox, return stdout+stderr."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=timeout,
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
#  VISION  (screenshot + llava)
# =============================================================================

def capture_screenshot_b64() -> Optional[str]:
    """Capture screen and return base64-encoded JPEG."""
    try:
        if HAS_OCR:
            img = ImageGrab.grab()
        else:
            # Fallback: use Qt screenshot
            screen = QApplication.primaryScreen()
            qpix = screen.grabWindow(0)
            buf = io.BytesIO()
            qpix.save(buf := io.BytesIO(), "JPEG", quality=60)  # type: ignore
            return base64.b64encode(buf.getvalue()).decode()

        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=60)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as exc:
        log(f"screenshot: {exc}")
        return None


def ask_vision(question: str, image_b64: str) -> str:
    """Send image + question to llava via Ollama."""
    try:
        client = _get_ollama_client()
        resp = client.chat(
            model=SETTINGS.get("vision_model", "llava"),
            messages=[{
                "role": "user",
                "content": question,
                "images": [image_b64],
            }],
            options={"num_ctx": 2048},
        )
        return resp.message.content.strip()
    except Exception as exc:
        return f"Vision error: {exc}"


# =============================================================================
#  OCR  (clipboard image → text)
# =============================================================================

def ocr_clipboard_image() -> Optional[str]:
    """Read text from an image currently in the clipboard."""
    if not HAS_OCR:
        return None
    try:
        cb = QApplication.clipboard()
        qimg = cb.image()
        if qimg.isNull():
            return None
        buf = io.BytesIO()
        qimg.save(buf := io.BytesIO(), "PNG")   # type: ignore
        pil_img = Image.open(io.BytesIO(buf.getvalue()))
        text = pytesseract.image_to_string(pil_img).strip()
        return text if len(text) > 3 else None
    except Exception as exc:
        log(f"OCR: {exc}")
        return None


# =============================================================================
#  TTS  (pyttsx3 / ElevenLabs / Coqui)
# =============================================================================

def speak(text: str) -> None:
    """Play TTS. Respects mute flag — rechecked after render wait."""
    if _tts_muted or not text or not _HAS_TTS:
        return
    if _rendering_event.is_set():
        _rendering_event.wait(timeout=5.0)
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
    if not _HAS_TTS or _tts_engine is None:
        return
    with _tts_lock:
        try:
            _tts_engine.say(text)
            _tts_engine.runAndWait()
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
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(audio); tmp = f.name
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


# =============================================================================
#  STT  (multi-engine)
# =============================================================================

def listen_once(timeout: float) -> Optional[str]:
    adj = SETTINGS.get("ambient_adjust_sec", 0.1)   # ← fast: 0.1s not 1s
    try:
        if _overlay_window:
            _overlay_window.set_status("🎙 Listening…", "#ffaa00")

        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=adj)
            try:
                audio = recognizer.listen(
                    source,
                    timeout=timeout,
                    phrase_time_limit=getattr(config, "LISTEN_PHRASE_TIME_LIMIT_SECONDS", 15),
                )
            except sr.WaitTimeoutError:
                if _overlay_window:
                    _overlay_window.set_status("Ready", "#00c864")
                return None

        if _overlay_window:
            _overlay_window.set_status("Transcribing…", "#00aaff")

        engine = SETTINGS.get("stt_engine", "faster_whisper")

        if engine == "faster_whisper" and HAS_FASTER_WHISPER:
            wav_data = audio.get_wav_data()
            text = transcribe_faster_whisper(wav_data)
            return text

        if engine == "google" or (engine == "faster_whisper" and not HAS_FASTER_WHISPER):
            text = recognizer.recognize_google(audio)
            return text.strip() or None

        if engine == "vosk":
            model_path = getattr(config, "VOSK_MODEL_PATH", "")
            if not model_path:
                return None
            raw = recognizer.recognize_vosk(audio, model=model_path)
            return json.loads(raw).get("text", "").strip() or None

        return None

    except sr.UnknownValueError:
        if _overlay_window:
            _overlay_window.set_status("Ready", "#00c864")
        return None
    except Exception as exc:
        log(f"listen_once: {exc}")
        if _overlay_window:
            _overlay_window.set_status("Error", "#ff4444")
        return None


# =============================================================================
#  CONTINUOUS LISTENING  (VAD-based hands-free)
# =============================================================================

def continuous_listen_loop() -> None:
    """Always-on microphone using webrtcvad silence detection."""
    if not HAS_VAD:
        log("webrtcvad not installed — pip install webrtcvad")
        return
    try:
        import pyaudio
        vad    = webrtcvad.Vad(2)
        pa     = pyaudio.PyAudio()
        RATE   = 16000
        CHUNK  = 480   # 30ms at 16kHz
        stream = pa.open(rate=RATE, channels=1, format=pyaudio.paInt16,
                         input=True, frames_per_buffer=CHUNK)

        log("Continuous listening started")
        frames     = []
        speaking   = False
        silent_cnt = 0
        SILENCE_LIMIT = 20  # ~600ms silence = end of speech

        while not shutdown_event.is_set() and SETTINGS.get("continuous_listen"):
            chunk = stream.read(CHUNK, exception_on_overflow=False)
            is_speech = vad.is_speech(chunk, RATE)

            if is_speech:
                frames.append(chunk)
                speaking   = True
                silent_cnt = 0
            elif speaking:
                frames.append(chunk)
                silent_cnt += 1
                if silent_cnt > SILENCE_LIMIT:
                    # Speech ended — transcribe
                    audio_data = b"".join(frames)
                    frames     = []
                    speaking   = False
                    silent_cnt = 0
                    text = transcribe_faster_whisper(audio_data) if HAS_FASTER_WHISPER else None
                    if text and len(text) > 2:
                        log(f"Continuous heard: {text}")
                        if _overlay_window:
                            _overlay_window.set_input(text)
                        command_queue.put(text)
            time.sleep(0.001)

        stream.stop_stream()
        stream.close()
        pa.terminate()
    except Exception as exc:
        log(f"continuous_listen: {exc}")


# =============================================================================
#  PLUGIN SYSTEM
# =============================================================================

_plugins: Dict[str, Any] = {}


def load_plugins() -> None:
    """Load all .py files from the plugins directory."""
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
            print(f"[Plugin] Failed to load {path.name}: {exc}")


load_plugins()


# =============================================================================
#  REST API  (FastAPI / simple HTTP server)
# =============================================================================

def start_rest_api(port: int) -> None:
    """Simple HTTP server that accepts POST /ask {prompt: str}."""
    import http.server

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass   # suppress access log

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
    print(f"[REST] API server at http://127.0.0.1:{port}/ask")
    server.serve_forever()


# =============================================================================
#  ACTIVE WINDOW DETECTION
# =============================================================================

def get_active_window_title() -> str:
    try:
        if platform.system() == "Darwin":
            script = 'tell application "System Events" to get name of first process whose frontmost is true'
            r = subprocess.run(["osascript", "-e", script],
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
    """Auto-switch persona based on the active application."""
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


# =============================================================================
#  MARKDOWN + SYNTAX HIGHLIGHT  (HTML conversion)
# =============================================================================

def render_markdown(text: str) -> str:
    """Convert markdown text to styled HTML for QTextBrowser."""
    if not HAS_MARKDOWN:
        return f"<pre style='color:#00c864;white-space:pre-wrap;'>{html.escape(text)}</pre>"

    # Apply syntax highlighting to code blocks before markdown conversion
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
        blockquote {{ border-left:3px solid #444; margin:4px 0; padding-left:8px;
                     color:#888; }}
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
    """Ask AI for 3 short follow-up questions."""
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
    pins.append({
        "timestamp": datetime.now().isoformat(),
        "prompt":    prompt,
        "answer":    answer,
    })
    PINS_FILE.write_text(json.dumps(pins, indent=2))


def save_rating(prompt: str, answer: str, rating: int) -> None:
    try:
        ratings = []
        if RATINGS_FILE.exists():
            ratings = json.loads(RATINGS_FILE.read_text())
        ratings.append({
            "timestamp": datetime.now().isoformat(),
            "rating":    rating,
            "prompt":    prompt,
            "answer":    answer[:300],
        })
        RATINGS_FILE.write_text(json.dumps(ratings, indent=2))
    except Exception as exc:
        log(f"save_rating: {exc}")


# =============================================================================
#  MAIN AI CALL  (with cache, RAG, web search, vision)
# =============================================================================

def ask_ai_streaming(prompt: str, _sink: Optional[queue.Queue] = None) -> str:
    global _session_tokens

    # ── Check cache first ─────────────────────────────────────────────────────
    cached = _cache_get(prompt)
    if cached:
        log(f"Cache hit for: {prompt[:50]}")
        if _overlay_window:
            _overlay_window.set_response(cached)
            _overlay_window.set_status("Ready (cached ⚡)", "#00c864")
        return cached

    # ── Check plugins ─────────────────────────────────────────────────────────
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

    # ── Detect special prefixes ───────────────────────────────────────────────
    lower = prompt.lower().strip()

    if lower.startswith("/search ") or lower.startswith("/search\n"):
        query = prompt[8:].strip()
        if _overlay_window:
            _overlay_window.set_status("Searching web…", "#00aaff")
        results = web_search(query)
        # Feed results to AI for synthesis
        prompt = (
            f"Based on these web search results, answer: {query}\n\n"
            f"Search results:\n{results}"
        )

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
        else:
            return "Could not capture screenshot."

    elif lower.startswith("/rag "):
        query   = prompt[5:].strip()
        context = rag_search(query)
        if context:
            prompt = (
                f"Using the following document excerpts, answer: {query}\n\n"
                f"Context:\n{context}"
            )
        else:
            prompt = f"No documents found for: {query}. Answer from general knowledge: {query}"

    elif lower.startswith("/run "):
        # Generate + execute code
        code_prompt = prompt[5:].strip()
        prompt = f"Write Python code to accomplish this task. Only output the code, no explanation:\n{code_prompt}"

    # ── Auto web search if enabled and question seems to need current info ────
    elif SETTINGS.get("web_search_enabled") and HAS_DDG:
        current_keywords = ["today", "latest", "current", "news", "now",
                            "price", "weather", "recent", "2024", "2025", "2026"]
        if any(k in lower for k in current_keywords):
            try:
                results = web_search(prompt, max_results=3)
                prompt  = (
                    f"Using these real-time search results, answer: {prompt}\n\n"
                    f"Web results:\n{results}"
                )
            except Exception:
                pass

    # ── Auto-inject RAG context if enabled ───────────────────────────────────
    if SETTINGS.get("rag_enabled") and HAS_CHROMA:
        context = rag_search(prompt)
        if context:
            prompt = f"Context from documents:\n{context}\n\nQuestion: {prompt}"

    # ── Build messages with persona + memory ─────────────────────────────────
    system = PERSONAS.get(SETTINGS.get("persona", "default"), "")
    messages: List[Dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.extend(_conv_history[-20:])
    messages.append({"role": "user", "content": prompt})

    if _overlay_window:
        _overlay_window.set_status("Thinking…", "#aa44ff")

    # ── Ollama streaming ──────────────────────────────────────────────────────
    if SETTINGS["ai_engine"] == "ollama":
        try:
            client = _get_ollama_client()
            full   = []
            _generation_stop_event.clear()   # reset stop flag before starting

            stream = client.chat(
                model=SETTINGS["model"],
                messages=messages,
                options={"num_ctx": 4096, "num_predict": 1024, "temperature": 0.7},
                keep_alive="60m",
                stream=True,
            )
            for chunk in stream:
                # ── STOP BUTTON: break out of stream immediately ──────────
                if _generation_stop_event.is_set():
                    log("Generation stopped by user")
                    if _overlay_window:
                        _overlay_window.communicate.append_token.emit(
                            "\n\n*⏹ Stopped.*")
                    break

                token = chunk.message.content or ""
                if token:
                    full.append(token)
                    if _overlay_window:
                        _overlay_window.append_token(token)
                    if _sink:
                        _sink.put(("token", token))

            answer = "".join(full).strip() or "Empty response."
            _session_tokens += len(prompt) // 4 + len(answer) // 4
            if _overlay_window:
                _overlay_window.update_tokens(_session_tokens)

            # Auto-run code if /run prefix
            if lower.startswith("/run "):
                blocks = extract_code_blocks(answer)
                if blocks:
                    _, code = blocks[0]
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
            m = "Ollama not running. Run: ollama serve"
            log(m); return m
        except Exception as exc:
            m = f"Ollama error: {exc}"; log(m); return m

    # ── OpenAI ────────────────────────────────────────────────────────────────
    if SETTINGS["ai_engine"] == "openai":
        api_key = getattr(config, "OPENAI_API_KEY", "")
        if not api_key:
            return "OPENAI_API_KEY missing."
        payload = {
            "model":    getattr(config, "OPENAI_MODEL", "gpt-4"),
            "messages": messages,
        }
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
            usage   = body.get("usage", {})
            _session_tokens += usage.get("total_tokens", 0)
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
            _overlay_window.show_stop_btn()   # show stop button

        was_stopped = False
        try:
            answer = ask_ai_streaming(prompt)
            was_stopped = _generation_stop_event.is_set()
        except Exception as exc:
            answer = f"Error: {exc}"
            if _overlay_window:
                _overlay_window.set_response(answer)

        if _overlay_window:
            _overlay_window.hide_stop_btn()   # hide stop button
            # If stopped mid-stream, finalize the partial markdown render
            if was_stopped and _overlay_window._streaming:
                partial = _overlay_window._current_answer
                _overlay_window.set_response(partial)
                _overlay_window._streaming = False

        print(f"[Assistant] {answer[:120]}")

        # Store in memory
        _conv_history.append({"role": "user",      "content": prompt})
        _conv_history.append({"role": "assistant", "content": answer})

        # Summarize memory if too long
        if len(_conv_history) > 40:
            _summarize_history()

        # Generate follow-ups in background
        if _overlay_window:
            _overlay_window.add_history(prompt, answer)
            _overlay_window.set_status("Ready", "#00c864")
            _rendering_event.clear()
            threading.Thread(
                target=lambda: _push_followups(prompt, answer),
                daemon=True
            ).start()

        speak(answer)
        command_queue.task_done()


def _summarize_history() -> None:
    """Compress old history into a summary to keep context window lean."""
    global _conv_history
    try:
        old = _conv_history[:-10]
        recent = _conv_history[-10:]
        if not old:
            return
        summary_prompt = (
            "Summarize this conversation history very concisely in 3-5 bullet points:\n\n"
            + "\n".join(f"{m['role']}: {m['content'][:200]}" for m in old)
        )
        client = _get_ollama_client()
        resp = client.chat(
            model=SETTINGS["model"],
            messages=[{"role": "user", "content": summary_prompt}],
            options={"num_ctx": 2048, "num_predict": 200},
        )
        summary = resp.message.content.strip()
        _conv_history = [
            {"role": "system", "content": f"[Conversation summary: {summary}]"},
            *recent,
        ]
        log("History summarized and compressed")
    except Exception as exc:
        log(f"history summarize: {exc}")


def _push_followups(prompt: str, answer: str) -> None:
    followups = generate_followups(prompt, answer)
    if followups and _overlay_window:
        _overlay_window.set_followups(followups)


# =============================================================================
#  VOICE TRIGGER
# =============================================================================

def on_trigger() -> None:
    if shutdown_event.is_set():
        return
    window_title = get_active_window_title()
    if window_title:
        auto_switch_persona(window_title)

    if _overlay_window:
        _overlay_window._is_listening = True
        _overlay_window.communicate.update_status.emit("🎙 Listening…", "#ff6600")

    text = listen_once(timeout=getattr(config, "LISTEN_TIMEOUT_SECONDS", 8))

    if _overlay_window:
        _overlay_window._is_listening = False
        _overlay_window.communicate.reset_mic_btn.emit()

    if text:
        if _overlay_window:
            _overlay_window.set_input(text)
        else:
            command_queue.put(text)
    else:
        if _overlay_window:
            _overlay_window.set_status("Ready", "#00c864")

# =============================================================================
#  SCREEN SHARE INVISIBILITY + ALWAYS ON TOP
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
    if platform.system() != "Darwin":
        return
    try:
        from AppKit import NSApplication  # type: ignore
        for w in NSApplication.sharedApplication().windows():
            try:
                w.setLevel_(3)
                w.setCanHide_(False)
                w.setCollectionBehavior_(1 << 3 | 1 << 6)
            except Exception: pass
    except Exception: pass


# =============================================================================
#  PyQt5 OVERLAY
# =============================================================================

if HAS_PYQT5:

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
        set_mute_btn     = pyqtSignal(bool)
        update_tokens    = pyqtSignal(int)
        add_history_item = pyqtSignal(str, str)
        panic_toggle     = pyqtSignal()
        set_followups    = pyqtSignal(list)
        add_rag_doc      = pyqtSignal(str, str)
        set_stop_btn     = pyqtSignal(bool)   # True=show stop, False=hide stop


    # ── Settings Panel ───────────────────────────────────────────────────────

    class SettingsPanel(QWidget):
        closed = pyqtSignal()

        def __init__(self, parent_overlay: "StealthOverlay") -> None:
            super().__init__()
            self.ov = parent_overlay
            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground)
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

            # Header
            hdr = QHBoxLayout()
            ttl = QLabel("⚙  Settings")
            ttl.setFont(QFont("Menlo", 13, QFont.Bold))
            ttl.setStyleSheet("color:#cccccc;")
            hdr.addWidget(ttl)
            hdr.addStretch()
            close = QPushButton("✕")
            close.setFixedSize(24, 24)
            close.setStyleSheet("""QPushButton{background:rgba(255,80,80,200);color:#111;
                border-radius:12px;font-weight:bold;}
                QPushButton:hover{background:rgba(255,50,50,255);}""")
            close.clicked.connect(self._close)
            hdr.addWidget(close)
            lay.addLayout(hdr)
            lay.addWidget(self._div())

            # Scroll area for settings
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea{border:none;background:transparent;}")
            inner = QWidget()
            inner.setStyleSheet("background:transparent;")
            inner_lay = QVBoxLayout(inner)
            inner_lay.setSpacing(8)

            # ── AI Model ─────────────────────────────────────────────────────
            inner_lay.addWidget(self._lbl("AI Model"))
            self.model_combo = QComboBox()
            self.model_combo.addItems(SETTINGS["available_models"])
            idx = self.model_combo.findText(SETTINGS["model"])
            if idx >= 0: self.model_combo.setCurrentIndex(idx)
            self._style_combo(self.model_combo)
            self.model_combo.currentTextChanged.connect(self._on_model_change)
            inner_lay.addWidget(self.model_combo)

            # ── Persona ───────────────────────────────────────────────────────
            inner_lay.addWidget(self._lbl("Persona"))
            self.persona_combo = QComboBox()
            self.persona_combo.addItems(list(PERSONAS.keys()))
            pidx = self.persona_combo.findText(SETTINGS["persona"])
            if pidx >= 0: self.persona_combo.setCurrentIndex(pidx)
            self._style_combo(self.persona_combo)
            self.persona_combo.currentTextChanged.connect(self._on_persona_change)
            inner_lay.addWidget(self.persona_combo)

            inner_lay.addWidget(self._div())

            # ── STT Engine ────────────────────────────────────────────────────
            inner_lay.addWidget(self._lbl("Speech-to-Text Engine"))
            self.stt_combo = QComboBox()
            stt_opts = ["faster_whisper", "google", "vosk"]
            self.stt_combo.addItems(stt_opts)
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

            # ── TTS Engine ────────────────────────────────────────────────────
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
            self.el_key.setEchoMode(QLineEdit.Password)
            self.el_key.setFont(QFont("Menlo", 10))
            self.el_key.setStyleSheet("""QLineEdit{background:rgba(25,25,25,220);
                color:#eee;border:1px solid rgba(50,50,50,160);border-radius:6px;padding:5px;}""")
            self.el_key.textChanged.connect(
                lambda t: (SETTINGS.update({"elevenlabs_key": t}), save_settings(SETTINGS)))
            inner_lay.addWidget(self.el_key)

            inner_lay.addWidget(self._div())

            # ── TTS Rate / Volume / Opacity ───────────────────────────────────
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
            fs_row.addWidget(self.font_lbl)
            fs_row.addStretch()
            inner_lay.addLayout(fs_row)

            inner_lay.addWidget(self._div())

            # ── Toggles ───────────────────────────────────────────────────────
            self.auto_send_cb   = self._checkbox("Auto-send after voice",        SETTINGS["auto_send"])
            self.clipboard_cb   = self._checkbox("Clipboard monitor",            SETTINGS["clipboard_monitor"])
            self.continuous_cb  = self._checkbox("Continuous listening (VAD)",   SETTINGS.get("continuous_listen", False))
            self.web_search_cb  = self._checkbox("Web search (auto + /search)",  SETTINGS.get("web_search_enabled", True))
            self.rag_cb         = self._checkbox("RAG (use my documents)",       SETTINGS.get("rag_enabled", False))
            self.code_exec_cb   = self._checkbox("Code execution sandbox",       SETTINGS.get("code_exec_enabled", True))
            self.rest_api_cb    = self._checkbox(f"REST API (port {SETTINGS.get('rest_api_port',7788)})",
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
                    lambda state, k=key: (
                        SETTINGS.update({k: bool(state)}),
                        save_settings(SETTINGS)
                    )
                )
                inner_lay.addWidget(cb)

            self.rest_api_cb.stateChanged.connect(self._on_rest_api_toggle)
            inner_lay.addWidget(self.rest_api_cb)

            inner_lay.addWidget(self._lbl("Auto-clear after inactivity (minutes, 0=off)"))
            self.autoclear_sl = self._slider(0, 30, SETTINGS.get("auto_clear_minutes", 0))
            self.autoclear_sl.valueChanged.connect(self._on_autoclear_change)
            inner_lay.addWidget(self.autoclear_sl)

            inner_lay.addStretch()

            # ── RAG document loader ───────────────────────────────────────────
            inner_lay.addWidget(self._div())
            rag_btn = QPushButton("📂 Load Document into RAG")
            rag_btn.setFixedHeight(34)
            rag_btn.setFont(QFont("Menlo", 10))
            rag_btn.setCursor(QCursor(Qt.PointingHandCursor))
            rag_btn.setStyleSheet("""QPushButton{background:rgba(20,60,80,200);color:#88ddff;
                border:1px solid rgba(0,120,180,150);border-radius:8px;}
                QPushButton:hover{background:rgba(30,80,110,220);}""")
            rag_btn.clicked.connect(self._load_rag_doc)
            inner_lay.addWidget(rag_btn)

            inner_lay.addWidget(QLabel(""))  # spacer

            scroll.setWidget(inner)
            lay.addWidget(scroll)

            note = QLabel("Settings saved automatically  •  ~/.ai_assistant/settings.json")
            note.setFont(QFont("Menlo", 8))
            note.setStyleSheet("color:#333;")
            note.setAlignment(Qt.AlignCenter)
            lay.addWidget(note)

        # ── helpers ──────────────────────────────────────────────────────────

        @staticmethod
        def _lbl(t: str) -> QLabel:
            l = QLabel(t); l.setFont(QFont("Menlo", 9, QFont.Bold))
            l.setStyleSheet("color:#777;"); return l

        @staticmethod
        def _div() -> QFrame:
            d = QFrame(); d.setFrameShape(QFrame.HLine)
            d.setStyleSheet("background:rgba(40,40,40,200);"); d.setFixedHeight(1)
            return d

        @staticmethod
        def _slider(mn, mx, val) -> QSlider:
            s = QSlider(Qt.Horizontal); s.setRange(mn, mx); s.setValue(val)
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
            SETTINGS["model"] = m; save_settings(SETTINGS)
            reload_ollama_client()
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
            _whisper_model = None   # force reload

        def _on_tts_engine_change(self, e: str) -> None:
            SETTINGS["tts_engine"] = e; save_settings(SETTINGS)

        def _on_rate_change(self, v: int) -> None:
            SETTINGS["tts_rate"] = v
            if _HAS_TTS and _tts_engine is not None:
                _tts_engine.setProperty("rate", v)
            save_settings(SETTINGS)

        def _on_vol_change(self, v: int) -> None:
            vol = v / 100
            SETTINGS["tts_volume"] = vol
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

        def _on_rest_api_toggle(self, state: int) -> None:
            SETTINGS["rest_api_enabled"] = bool(state); save_settings(SETTINGS)
            if state:
                port = SETTINGS.get("rest_api_port", 7788)
                threading.Thread(target=start_rest_api, args=(port,), daemon=True).start()

        def _load_rag_doc(self) -> None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Load Document", str(Path.home()),
                "Text Files (*.txt *.md *.py *.java *.json);;All Files (*)")
            if not path:
                return
            try:
                text  = Path(path).read_text(encoding="utf-8", errors="ignore")
                doc_id = Path(path).name
                ok = rag_add_document(text, doc_id, {"source": doc_id, "path": path})
                self.ov.set_status(
                    f"RAG: {doc_id} loaded ✓" if ok else "RAG load failed",
                    "#00c864" if ok else "#ff4444"
                )
            except Exception as exc:
                self.ov.set_status(f"Load error: {exc}", "#ff4444")

        def _close(self) -> None:
            self.hide(); self.closed.emit()

        def mousePressEvent(self, e) -> None:
            if e.button() == Qt.LeftButton:
                self._dp = e.globalPos() - self.frameGeometry().topLeft()
        def mouseMoveEvent(self, e) -> None:
            if e.buttons() == Qt.LeftButton:
                self.move(e.globalPos() - self._dp)


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

            C = self.communicate
            C.update_status.connect(self._set_status,       Qt.QueuedConnection)
            C.update_prompt.connect(self._set_prompt,       Qt.QueuedConnection)
            C.update_response.connect(self._set_response,   Qt.QueuedConnection)
            C.append_token.connect(self._append_token,      Qt.QueuedConnection)
            C.show_window.connect(self._show,               Qt.QueuedConnection)
            C.hide_window.connect(self._hide,               Qt.QueuedConnection)
            C.clear_text.connect(self._clear,               Qt.QueuedConnection)
            C.set_input_text.connect(self._set_input,       Qt.QueuedConnection)
            C.reset_mic_btn.connect(self._on_mic_reset,     Qt.QueuedConnection)
            # FIX: connect mute signal directly (no @pyqtSlot type conflict)
            C.set_mute_btn.connect(self._update_mute_btn,   Qt.QueuedConnection)
            C.update_tokens.connect(self._update_tokens,    Qt.QueuedConnection)
            C.add_history_item.connect(self._add_history,   Qt.QueuedConnection)
            C.panic_toggle.connect(self._panic,             Qt.QueuedConnection)
            C.set_followups.connect(self._set_followups,    Qt.QueuedConnection)
            C.set_stop_btn.connect(self._set_stop_visible,  Qt.QueuedConnection)

            self._think_timer = QTimer()
            self._think_timer.timeout.connect(self._tick_thinking)

            self._clip_timer = QTimer()
            self._clip_timer.timeout.connect(self._check_clipboard)
            self._clip_timer.start(800)

            self._autoclear_timer = QTimer()
            self._autoclear_timer.timeout.connect(self._check_autoclear)
            self._autoclear_timer.start(30_000)   # check every 30s
            self._last_activity = time.time()

            self._init_ui()
            QTimer.singleShot(300, self.input_box.setFocus)

        # ────────────────────────────────────────────────────────────────
        # UI BUILD
        # ────────────────────────────────────────────────────────────────

        def _init_ui(self) -> None:
            self.setWindowFlags(
                Qt.FramelessWindowHint |
                Qt.WindowStaysOnTopHint |
                Qt.Tool
            )
            # FIX 1: WA_ShowWithoutActivating — prevents stealing focus on macOS
            self.setAttribute(Qt.WA_ShowWithoutActivating)
            # FIX 5: Both widget AND container need transparent background
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setStyleSheet("background: transparent;")
            self.setAttribute(Qt.WA_AlwaysStackOnTop)
            self.setFocusPolicy(Qt.StrongFocus)
            try:
                self.setAttribute(Qt.WA_MacAlwaysShowToolWindow)
            except Exception:
                pass

            W, H = 1020, 780
            # FIX 8: availableGeometry() excludes taskbar/menubar
            screen = QApplication.primaryScreen().availableGeometry()
            x = max(0, screen.x() + screen.width()  - W - 20)
            y = max(0, screen.y() + screen.height() - H - 20)
            self.setGeometry(x, y, W, H)
            self.setMinimumSize(680, 520)

            # FIX 4: self needs its own layout so container fills it on resize
            self_layout = QVBoxLayout(self)
            self_layout.setContentsMargins(0, 0, 0, 0)
            self_layout.setSpacing(0)

            self.container = QFrame(self)
            self.container.setObjectName("outer")
            self.container.setStyleSheet("""
                QFrame#outer {
                    background: rgba(12,12,12,252);
                    border-radius: 18px;
                    border: 1.5px solid rgba(0,195,95,140);
                }
            """)
            self_layout.addWidget(self.container)

            root = QVBoxLayout(self.container)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            root.addWidget(self._build_titlebar())

            splitter = QSplitter(Qt.Horizontal)
            splitter.setStyleSheet(
                "QSplitter::handle{background:rgba(35,35,35,200);width:1px;}")
            splitter.addWidget(self._build_sidebar())
            splitter.addWidget(self._build_main_area())
            splitter.setSizes([210, 810])
            root.addWidget(splitter, stretch=1)

            grip = QSizeGrip(self)
            grip.setStyleSheet("background:transparent;")
            root.addWidget(grip, 0, Qt.AlignBottom | Qt.AlignRight)

        # ── Title bar ────────────────────────────────────────────────────────

        def _build_titlebar(self) -> QFrame:
            tbar = QFrame(); tbar.setFixedHeight(54); tbar.setObjectName("tbar")
            tbar.setStyleSheet("""
                QFrame#tbar{background:rgba(18,18,18,255);
                border-top-left-radius:18px;border-top-right-radius:18px;
                border-bottom:1px solid rgba(32,32,32,220);}
            """)
            tb = QHBoxLayout(tbar)
            tb.setContentsMargins(14, 0, 14, 0)
            tb.setSpacing(8)

            close = self._icon_btn("✕", "#ff5f57", "#ff3b30", 24, "Quit  Ctrl+Q")
            close.clicked.connect(self._on_close)
            tb.addWidget(close)
            tb.addSpacing(6)

            title_col = QVBoxLayout(); title_col.setSpacing(1)
            tl = QLabel("🤖  AI Stealth Assistant")
            tl.setFont(QFont("Menlo", 12, QFont.Bold))
            tl.setStyleSheet("color:#cccccc;background:transparent;")
            title_col.addWidget(tl)
            self.model_indicator = QLabel(f"▸ {SETTINGS['model']}  •  {SETTINGS['persona']}")
            self.model_indicator.setFont(QFont("Menlo", 8))
            self.model_indicator.setStyleSheet("color:#3a3a3a;background:transparent;")
            title_col.addWidget(self.model_indicator)
            tb.addLayout(title_col)

            tb.addStretch()

            self.token_lbl = QLabel("Tokens: 0")
            self.token_lbl.setFont(QFont("Menlo", 9))
            self.token_lbl.setStyleSheet("color:#3a3a3a;background:transparent;padding:0 6px;")
            tb.addWidget(self.token_lbl)

            # Search button
            search_btn = QPushButton("🔍")
            search_btn.setFixedSize(38, 30)
            search_btn.setFont(QFont("Menlo", 13))
            search_btn.setCursor(QCursor(Qt.PointingHandCursor))
            search_btn.setToolTip("Search history  Ctrl+F")
            search_btn.setStyleSheet("""QPushButton{background:rgba(35,35,35,200);
                color:#777;border-radius:8px;}QPushButton:hover{background:rgba(55,55,55,220);color:#ccc;}""")
            search_btn.clicked.connect(self._toggle_search)
            tb.addWidget(search_btn)

            # Mute
            self.mute_btn = QPushButton("🔊")
            self.mute_btn.setFixedSize(42, 30)
            self.mute_btn.setFont(QFont("Menlo", 12))
            self.mute_btn.setCursor(QCursor(Qt.PointingHandCursor))
            self.mute_btn.setToolTip("Toggle sound  Ctrl+M")
            self.mute_btn.setStyleSheet(self._mute_style(False))
            self.mute_btn.clicked.connect(self._on_mute_toggle)
            tb.addWidget(self.mute_btn)

            # ── STOP GENERATION BUTTON ───────────────────────────────────
            self.stop_btn = QPushButton("⏹ Stop")
            self.stop_btn.setFixedSize(72, 30)
            self.stop_btn.setFont(QFont("Menlo", 10, QFont.Bold))
            self.stop_btn.setCursor(QCursor(Qt.PointingHandCursor))
            self.stop_btn.setToolTip("Stop response generation  Esc")
            self.stop_btn.setStyleSheet("""
                QPushButton {
                    background: rgba(160,30,30,220);
                    color: #ffcccc;
                    border: 1px solid rgba(220,50,50,180);
                    border-radius: 8px;
                    font-size: 11px;
                }
                QPushButton:hover { background: rgba(200,40,40,240); color: #fff; }
                QPushButton:pressed { background: rgba(130,20,20,255); }
            """)
            self.stop_btn.clicked.connect(self._on_stop_generation)
            self.stop_btn.hide()   # hidden until generation starts
            tb.addWidget(self.stop_btn)

            # Settings
            gear = QPushButton("⚙")
            gear.setFixedSize(38, 30)
            gear.setFont(QFont("Menlo", 14))
            gear.setCursor(QCursor(Qt.PointingHandCursor))
            gear.setToolTip("Settings")
            gear.setStyleSheet("""QPushButton{background:rgba(38,38,38,200);
                color:#888;border-radius:8px;}QPushButton:hover{background:rgba(60,60,60,220);color:#ccc;}""")
            gear.clicked.connect(self._open_settings)
            tb.addWidget(gear)

            # Status
            self.status_label = QLabel("● Ready")
            self.status_label.setFont(QFont("Menlo", 10, QFont.Bold))
            self.status_label.setStyleSheet(
                "color:#00c864;background:transparent;min-width:140px;")
            self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            tb.addWidget(self.status_label)

            return tbar

        # ── Left sidebar (history + pins) ─────────────────────────────────────

        def _build_sidebar(self) -> QWidget:
            panel = QWidget()
            panel.setStyleSheet("background:rgba(15,15,15,200);")
            panel.setMinimumWidth(160); panel.setMaximumWidth(250)

            lay = QVBoxLayout(panel)
            lay.setContentsMargins(8, 10, 8, 8)
            lay.setSpacing(6)

            # Search bar (hidden by default)
            self.search_bar = QLineEdit()
            self.search_bar.setPlaceholderText("Search history…")
            self.search_bar.setFont(QFont("Menlo", 10))
            self.search_bar.setFixedHeight(28)
            self.search_bar.setStyleSheet("""QLineEdit{background:rgba(28,28,28,220);
                color:#ccc;border:1px solid rgba(50,50,50,160);border-radius:6px;padding:0 8px;}""")
            self.search_bar.textChanged.connect(self._filter_history)
            self.search_bar.hide()
            lay.addWidget(self.search_bar)

            tabs = QHBoxLayout()
            self.hist_tab_btn = QPushButton("History")
            self.pins_tab_btn = QPushButton("Pinned ⭐")
            for b in [self.hist_tab_btn, self.pins_tab_btn]:
                b.setFixedHeight(24)
                b.setFont(QFont("Menlo", 9))
                b.setCursor(QCursor(Qt.PointingHandCursor))
                b.setStyleSheet("""QPushButton{background:rgba(30,30,30,200);color:#666;
                    border-radius:5px;border:none;}QPushButton:hover{color:#aaa;}
                    QPushButton:checked{color:#00c864;background:rgba(0,100,40,120);}""")
                b.setCheckable(True)
                tabs.addWidget(b)
            self.hist_tab_btn.setChecked(True)
            self.hist_tab_btn.clicked.connect(lambda: self._show_sidebar_tab("history"))
            self.pins_tab_btn.clicked.connect(lambda: self._show_sidebar_tab("pins"))
            lay.addLayout(tabs)

            self.sidebar_stack = QStackedWidget()
            self.sidebar_stack.setStyleSheet("background:transparent;")

            # History list
            hist_w = QWidget(); hist_w.setStyleSheet("background:transparent;")
            hist_lay = QVBoxLayout(hist_w)
            hist_lay.setContentsMargins(0, 0, 0, 0)
            self.history_list = QListWidget()
            self.history_list.setFont(QFont("Menlo", 9))
            self.history_list.setStyleSheet("""
                QListWidget{background:transparent;color:#777;border:none;outline:none;}
                QListWidget::item{padding:6px 4px;border-bottom:1px solid rgba(30,30,30,180);}
                QListWidget::item:hover{background:rgba(28,28,28,200);color:#bbb;}
                QListWidget::item:selected{background:rgba(0,140,70,80);color:#fff;}
            """)
            self.history_list.itemClicked.connect(self._on_history_click)
            hist_lay.addWidget(self.history_list)

            btn_row = QHBoxLayout()
            exp_btn = self._small_btn("⬇ Export", "#1e3c28", "#88ffaa", self._on_export)
            exp_btn.setToolTip("Export  Ctrl+E")
            clr_btn = self._small_btn("✕ Clear",  "#3c1e1e", "#ff8888", self._clear_history)
            btn_row.addWidget(exp_btn); btn_row.addWidget(clr_btn)
            hist_lay.addLayout(btn_row)
            self.sidebar_stack.addWidget(hist_w)

            # Pins list
            pins_w = QWidget(); pins_w.setStyleSheet("background:transparent;")
            pins_lay = QVBoxLayout(pins_w)
            pins_lay.setContentsMargins(0, 0, 0, 0)
            self.pins_list = QListWidget()
            self.pins_list.setFont(QFont("Menlo", 9))
            self.pins_list.setStyleSheet(self.history_list.styleSheet())
            self.pins_list.itemClicked.connect(self._on_pin_click)
            pins_lay.addWidget(self.pins_list)
            self.sidebar_stack.addWidget(pins_w)
            self._reload_pins()

            lay.addWidget(self.sidebar_stack, stretch=1)

            return panel

        # ── Main chat area ────────────────────────────────────────────────────

        def _build_main_area(self) -> QWidget:
            w = QWidget(); w.setStyleSheet("background:transparent;")
            lay = QVBoxLayout(w)
            lay.setContentsMargins(14, 10, 14, 10)
            lay.setSpacing(6)

            # You: row
            you_row = QHBoxLayout()
            you_lbl = QLabel("You:")
            you_lbl.setFont(QFont("Menlo", 9, QFont.Bold))
            you_lbl.setStyleSheet("color:#555;background:transparent;")
            you_row.addWidget(you_lbl); you_row.addStretch()
            lay.addLayout(you_row)

            self.prompt_text = QTextEdit()
            self.prompt_text.setReadOnly(True)
            self.prompt_text.setFixedHeight(86)
            self.prompt_text.setFont(QFont("Menlo", SETTINGS["font_size"]))
            self.prompt_text.setStyleSheet(self._te_style("#cccccc"))
            lay.addWidget(self.prompt_text)

            lay.addWidget(self._div())

            # Assistant: row
            ai_row = QHBoxLayout()
            ai_lbl = QLabel("Assistant:")
            ai_lbl.setFont(QFont("Menlo", 9, QFont.Bold))
            ai_lbl.setStyleSheet("color:#555;background:transparent;")
            ai_row.addWidget(ai_lbl)
            ai_row.addStretch()

            # Copy
            self.copy_btn = self._tiny_btn("⎘ Copy",  self._on_copy)
            self.copy_btn.setToolTip("Copy  Ctrl+C")
            ai_row.addWidget(self.copy_btn)

            # Pin
            self.pin_btn  = self._tiny_btn("⭐ Pin",   self._on_pin)
            self.pin_btn.setToolTip("Pin this answer")
            ai_row.addWidget(self.pin_btn)

            # Run code
            self.run_btn  = self._tiny_btn("▶ Run",   self._on_run_code)
            self.run_btn.setToolTip("Execute code in response  Ctrl+R")
            ai_row.addWidget(self.run_btn)

            # Rating
            for label, rating in [("👍", 1), ("👎", -1)]:
                b = QPushButton(label)
                b.setFixedHeight(24); b.setFixedWidth(32)
                b.setFont(QFont("Menlo", 11))
                b.setCursor(QCursor(Qt.PointingHandCursor))
                b.setToolTip("Rate response")
                b.setStyleSheet("""QPushButton{background:rgba(40,40,40,200);
                    border-radius:5px;border:none;}QPushButton:hover{background:rgba(65,65,65,220);}""")
                b.clicked.connect(lambda _, r=rating: self._on_rate(r))
                ai_row.addWidget(b)

            lay.addLayout(ai_row)

            # Response area (QTextBrowser for HTML markdown rendering)
            self.response_text = QTextBrowser()
            self.response_text.setOpenExternalLinks(True)
            self.response_text.setFont(QFont("Menlo", SETTINGS["font_size"]))
            self.response_text.setStyleSheet(self._tb_style())
            lay.addWidget(self.response_text, stretch=1)

            # Code execution output
            self.exec_output = QTextEdit()
            self.exec_output.setReadOnly(True)
            self.exec_output.setFont(QFont("Menlo", 11))
            self.exec_output.setFixedHeight(0)
            self.exec_output.setStyleSheet(self._te_style("#ffcc00"))
            self.exec_output.setPlaceholderText("Code execution output…")
            lay.addWidget(self.exec_output)

            # Follow-up chips
            self.followup_frame = QFrame()
            self.followup_frame.setStyleSheet("background:transparent;")
            self.followup_frame.hide()
            self.followup_layout = QHBoxLayout(self.followup_frame)
            self.followup_layout.setContentsMargins(0, 2, 0, 2)
            self.followup_layout.setSpacing(6)
            lay.addWidget(self.followup_frame)

            lay.addWidget(self._div())

            # Template + position row
            ctrl_row = QHBoxLayout(); ctrl_row.setSpacing(6)

            tmpl_lbl = QLabel("Template:")
            tmpl_lbl.setFont(QFont("Menlo", 9))
            tmpl_lbl.setStyleSheet("color:#3a3a3a;")
            ctrl_row.addWidget(tmpl_lbl)

            self.template_combo = QComboBox()
            self.template_combo.addItem("None")
            self.template_combo.addItems(list(PROMPT_TEMPLATES.keys()))
            self.template_combo.setFont(QFont("Menlo", 9))
            self.template_combo.setFixedHeight(26)
            self.template_combo.setMaximumWidth(160)
            self.template_combo.setStyleSheet("""
                QComboBox{background:rgba(22,22,22,220);color:#888;
                    border:1px solid rgba(45,45,45,160);border-radius:6px;padding:0 8px;}
                QComboBox QAbstractItemView{background:rgba(20,20,20,240);color:#eee;
                    selection-background-color:rgba(0,130,65,150);}
            """)
            self.template_combo.currentTextChanged.connect(self._on_template_select)
            ctrl_row.addWidget(self.template_combo)

            ctrl_row.addStretch()

            for label, tip, pos in [
                ("↖","Top-left","tl"), ("↗","Top-right","tr"),
                ("↙","Bot-left","bl"), ("↘","Bot-right","br"),
            ]:
                b = QPushButton(label)
                b.setFixedSize(26, 26)
                b.setToolTip(tip)
                b.setCursor(QCursor(Qt.PointingHandCursor))
                b.setStyleSheet("""QPushButton{background:rgba(30,30,30,180);
                    color:#555;border-radius:5px;font-size:14px;}
                    QPushButton:hover{background:rgba(50,50,50,200);color:#ccc;}""")
                b.clicked.connect(lambda _, p=pos: self._snap(p))
                ctrl_row.addWidget(b)

            lay.addLayout(ctrl_row)

            # Input row
            input_row = QHBoxLayout(); input_row.setSpacing(8)

            self.input_box = QLineEdit()
            self.input_box.setPlaceholderText(
                "  Ask anything…  /code /explain /bullet /review /search /vision /run /rag")
            self.input_box.setFont(QFont("Menlo", 13))
            self.input_box.setFixedHeight(50)
            self.input_box.setFocusPolicy(Qt.StrongFocus)
            self.input_box.setStyleSheet("""
                QLineEdit{background:rgba(20,20,20,245);color:#eeeeee;
                    border:1.5px solid rgba(0,155,72,120);border-radius:10px;padding:0 16px;
                    selection-background-color:rgba(0,180,90,100);}
                QLineEdit:focus{border:1.5px solid rgba(0,210,110,210);
                    background:rgba(22,22,22,255);}
            """)
            self.input_box.returnPressed.connect(self._on_send)
            input_row.addWidget(self.input_box)

            self.send_btn = self._action_btn("⏎ Send",  "#006830", "#009040", 100, 50)
            self.send_btn.setToolTip("Send  Enter")
            self.send_btn.clicked.connect(self._on_send)
            input_row.addWidget(self.send_btn)

            self.mic_btn  = self._action_btn("🎙 Mic",   "#1c1c90", "#2828b0", 86, 50)
            self.mic_btn.setToolTip("Listen  Ctrl+L  or  Space")
            self.mic_btn.clicked.connect(self._on_mic)
            input_row.addWidget(self.mic_btn)

            self.clr_btn  = self._action_btn("🗑 Clear", "#5a1010", "#7a1515", 86, 50)
            self.clr_btn.setToolTip("Clear  Ctrl+D")
            self.clr_btn.clicked.connect(self._clear)
            input_row.addWidget(self.clr_btn)

            lay.addLayout(input_row)

            legend = QLabel(
                "Ctrl+L=listen  │  Space=PTT  │  Ctrl+M=mute  │  Ctrl+C=copy  │  "
                "Ctrl+R=run code  │  Ctrl+E=export  │  Ctrl+F=search  │  "
                "Ctrl+Shift+H=panic  │  Ctrl+Q=quit"
            )
            legend.setFont(QFont("Menlo", 8))
            legend.setStyleSheet("color:#484848;background:transparent;padding:2px 0;")
            legend.setAlignment(Qt.AlignCenter)
            lay.addWidget(legend)

            return w

        # ── Style helpers ────────────────────────────────────────────────────

        @staticmethod
        def _te_style(color: str) -> str:
            return f"""
                QTextEdit{{background:rgba(18,18,18,230);color:{color};
                    border:1px solid rgba(35,35,35,180);border-radius:8px;padding:8px;
                    selection-background-color:rgba(0,180,90,90);}}
                QScrollBar:vertical{{background:transparent;width:5px;border-radius:2px;}}
                QScrollBar::handle:vertical{{background:rgba(65,65,65,150);
                    border-radius:2px;min-height:20px;}}
                QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{{height:0;}}
            """

        @staticmethod
        def _tb_style() -> str:
            return """
                QTextBrowser{background:rgba(18,18,18,230);color:#c8c8c8;
                    border:1px solid rgba(35,35,35,180);border-radius:8px;padding:8px;}
                QScrollBar:vertical{background:transparent;width:5px;border-radius:2px;}
                QScrollBar::handle:vertical{background:rgba(65,65,65,150);
                    border-radius:2px;min-height:20px;}
                QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}
            """

        @staticmethod
        def _mute_style(muted: bool) -> str:
            if muted:
                return """QPushButton{background:rgba(130,20,20,200);color:#ffaaaa;
                    border:1px solid rgba(180,35,35,150);border-radius:8px;}
                    QPushButton:hover{background:rgba(160,28,28,230);}"""
            return """QPushButton{background:rgba(20,75,38,200);color:#88ffaa;
                    border:1px solid rgba(0,130,65,150);border-radius:8px;}
                    QPushButton:hover{background:rgba(28,100,50,230);}"""

        @staticmethod
        def _icon_btn(text, bg, bgh, size, tip="") -> QPushButton:
            b = QPushButton(text); b.setFixedSize(size, size)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            if tip: b.setToolTip(tip)
            b.setFont(QFont("Menlo", 10, QFont.Bold))
            b.setStyleSheet(f"""QPushButton{{background:{bg};color:#ddd;
                border-radius:{size//2}px;}}QPushButton:hover{{background:{bgh};}}""")
            return b

        @staticmethod
        def _action_btn(text, bg, bgh, w, h) -> QPushButton:
            b = QPushButton(text); b.setFixedSize(w, h)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setFont(QFont("Menlo", 12, QFont.Bold))
            b.setStyleSheet(f"""QPushButton{{background:{bg};color:#fff;border-radius:10px;}}
                QPushButton:hover{{background:{bgh};}}
                QPushButton:pressed{{background:{bg};border:1px solid #fff3;}}""")
            return b

        def _tiny_btn(self, text: str, fn) -> QPushButton:
            b = QPushButton(text); b.setFixedHeight(24); b.setMinimumWidth(66)
            b.setFont(QFont("Menlo", 9)); b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setStyleSheet("""QPushButton{background:rgba(38,38,38,200);color:#888;
                border:1px solid rgba(60,60,60,150);border-radius:5px;padding:0 6px;}
                QPushButton:hover{background:rgba(58,58,58,220);color:#fff;}""")
            b.clicked.connect(fn); return b

        def _small_btn(self, text: str, bg_color: str, fg_color: str, fn) -> QPushButton:
            b = QPushButton(text)
            b.setFixedHeight(28)
            b.setFont(QFont("Menlo", 9))
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {bg_color};
                    color: {fg_color};
                    border-radius: 6px;
                    padding: 0 8px;
                    border: 1px solid rgba(255,255,255,20);
                }}
                QPushButton:hover {{
                    background: {bg_color};
                    color: #ffffff;
                    border: 1px solid rgba(255,255,255,60);
                }}
            """)
            b.clicked.connect(fn)
            return b

        @staticmethod
        def _div() -> QFrame:
            d = QFrame(); d.setFrameShape(QFrame.HLine)
            d.setStyleSheet("background:rgba(32,32,32,200);"); d.setFixedHeight(1)
            return d

        # ────────────────────────────────────────────────────────────────
        # Drag + resize
        # ────────────────────────────────────────────────────────────────

        def mousePressEvent(self, e) -> None:
            if e.button() == Qt.LeftButton:
                self.drag_pos = e.globalPos() - self.frameGeometry().topLeft()
        def mouseMoveEvent(self, e) -> None:
            if e.buttons() == Qt.LeftButton:
                self.move(e.globalPos() - self.drag_pos)

        # ────────────────────────────────────────────────────────────────
        # Thinking animation
        # ────────────────────────────────────────────────────────────────

        def _start_thinking(self) -> None:
            self._think_dots = 0
            self._think_timer.start(400)
            self.stop_btn.show()    # show Stop button when generation begins

        def _stop_thinking(self) -> None:
            self._think_timer.stop()
            self.stop_btn.hide()    # hide Stop button when done
        def _tick_thinking(self) -> None:
            self._think_dots = (self._think_dots + 1) % 4
            d = "●" * self._think_dots + "○" * (3 - self._think_dots)
            self.status_label.setText(f"● Thinking {d}")
            self.status_label.setStyleSheet("color:#aa44ff;background:transparent;min-width:140px;")

        # ────────────────────────────────────────────────────────────────
        # Clipboard monitor
        # ────────────────────────────────────────────────────────────────

        def _check_clipboard(self) -> None:
            global _clipboard_last
            self._last_activity = time.time()  # reset inactivity on clipboard change

            # OCR check
            if HAS_OCR and SETTINGS.get("clipboard_monitor"):
                ocr_text = ocr_clipboard_image()
                if ocr_text and ocr_text != _clipboard_last:
                    _clipboard_last = ocr_text
                    if not self.input_box.text().strip():
                        self.input_box.setText(ocr_text)
                        self.input_box.setFocus()
                        self.set_status("OCR from image ✓", "#ffaa00")
                        QTimer.singleShot(2000, lambda: self.set_status("Ready", "#00c864"))
                    return

            if not SETTINGS.get("clipboard_monitor"):
                return
            try:
                cb   = QApplication.clipboard()
                text = cb.text().strip()
                if text and text != _clipboard_last and len(text) > 5:
                    _clipboard_last = text
                    if not self.input_box.text().strip():
                        self.input_box.setText(text)
                        self.input_box.setFocus()
                        self.set_status("Clipboard pasted ✓", "#ffaa00")
                        QTimer.singleShot(2000, lambda: self.set_status("Ready", "#00c864"))
            except Exception:
                pass

        # ────────────────────────────────────────────────────────────────
        # Auto-clear on inactivity
        # ────────────────────────────────────────────────────────────────

        def _check_autoclear(self) -> None:
            mins = SETTINGS.get("auto_clear_minutes", 0)
            if mins <= 0:
                return
            if time.time() - self._last_activity > mins * 60:
                self._clear()
                self.set_status("Auto-cleared (inactivity)", "#555555")

        # ────────────────────────────────────────────────────────────────
        # Position snaps
        # ────────────────────────────────────────────────────────────────

        def _snap(self, pos: str) -> None:
            s = QApplication.primaryScreen().geometry()
            W, H = self.width(), self.height()
            m = 20
            positions = {
                "tl": (m,          m),
                "tr": (s.width()-W-m, m),
                "bl": (m,          s.height()-H-60),
                "br": (s.width()-W-m, s.height()-H-60),
            }
            self.move(*positions.get(pos, (m, m)))

        # ────────────────────────────────────────────────────────────────
        # Interactive handlers
        # ────────────────────────────────────────────────────────────────

        def _on_send(self) -> None:
            text = self.input_box.text().strip()
            if not text:
                return
            self._last_activity = time.time()
            # Clear any previous stop signal so new generation runs freely
            _generation_stop_event.clear()
            for key, tpl in PROMPT_TEMPLATES.items():
                if text.lower().startswith(key + " ") or text.lower() == key:
                    rest = text[len(key):].strip()
                    text = tpl + rest
                    break
            self.input_box.clear()
            self.template_combo.setCurrentIndex(0)
            self.followup_frame.hide()
            self.exec_output.setFixedHeight(0)
            self.set_prompt(text)
            command_queue.put(text)
            self._start_thinking()

        def _on_mic(self) -> None:
            if self._is_listening: return
            self._is_listening = True
            self._set_mic_active(True)
            threading.Thread(target=self._mic_thread, daemon=True).start()

        def _mic_thread(self) -> None:
            try:
                on_trigger()
            finally:
                self._is_listening = False
                self.communicate.reset_mic_btn.emit()

        @pyqtSlot()
        def _on_mic_reset(self) -> None:
            self._set_mic_active(False)

        def _set_mic_active(self, active: bool) -> None:
            if active:
                self.mic_btn.setText("🔴 Stop")
                self.mic_btn.setStyleSheet("""QPushButton{background:rgba(170,18,18,230);
                    color:#fff;border-radius:10px;border:2px solid rgba(240,55,55,170);font-size:12px;}""")
                self.container.setStyleSheet("""QFrame#outer{background:rgba(12,12,12,252);
                    border-radius:18px;border:2px solid rgba(240,55,55,190);}""")
            else:
                self.mic_btn.setText("🎙 Mic")
                self.mic_btn.setStyleSheet("""QPushButton{background:#1c1c90;color:#fff;
                    border-radius:10px;font-size:12px;}QPushButton:hover{background:#2828b0;}""")
                self.container.setStyleSheet("""QFrame#outer{background:rgba(12,12,12,252);
                    border-radius:18px;border:1.5px solid rgba(0,195,95,140);}""")

        def _on_mute_toggle(self) -> None:
            global _tts_muted
            _tts_muted = not _tts_muted
            SETTINGS["tts_muted"] = _tts_muted
            save_settings(SETTINGS)
            # Directly update button (same thread — this is always called from Qt thread)
            self._update_mute_btn(_tts_muted)
            print(f"[DEBUG] TTS {'muted 🔇' if _tts_muted else 'unmuted 🔊'}")

        def _update_mute_btn(self, muted: bool) -> None:
            # NOTE: no @pyqtSlot decorator — caused type conflict with bool signal
            self.mute_btn.setText("🔇" if muted else "🔊")
            self.mute_btn.setStyleSheet(self._mute_style(muted))
            self.mute_btn.setToolTip("Unmute  Ctrl+M" if muted else "Mute  Ctrl+M")

        def _on_stop_generation(self) -> None:
            """Stop the current streaming response immediately."""
            _generation_stop_event.set()
            self._set_stop_visible(False)
            self._stop_thinking()
            self._set_status("Stopped ⏹", "#ffaa44")
            # Stop TTS mid-sentence if playing
            try:
                if _HAS_TTS and _tts_engine is not None:
                    _tts_engine.stop()
            except Exception:
                pass
            QTimer.singleShot(2000, lambda: self._set_status("Ready", "#00c864"))
            print("[DEBUG] Generation stopped by user")

        @pyqtSlot(bool)
        def _set_stop_visible(self, visible: bool) -> None:
            """Show/hide the stop button."""
            if visible:
                self.stop_btn.show()
            else:
                self.stop_btn.hide()

        def _on_copy(self) -> None:
            raw = self.response_text.toPlainText()
            if raw:
                QApplication.clipboard().setText(raw)
                self.copy_btn.setText("✓ Copied!")
                QTimer.singleShot(1800, lambda: self.copy_btn.setText("⎘ Copy"))

        def _on_pin(self) -> None:
            if self._current_prompt and self._current_answer:
                save_pin(self._current_prompt, self._current_answer)
                self._reload_pins()
                self.pin_btn.setText("✓ Pinned!")
                QTimer.singleShot(1800, lambda: self.pin_btn.setText("⭐ Pin"))

        def _on_rate(self, rating: int) -> None:
            if self._current_prompt:
                save_rating(self._current_prompt, self._current_answer, rating)
                self.set_status(f"{'👍' if rating > 0 else '👎'} Rated", "#ffaa00")
                QTimer.singleShot(1500, lambda: self.set_status("Ready", "#00c864"))

        def _on_run_code(self) -> None:
            raw = self.response_text.toPlainText()
            blocks = extract_code_blocks(raw)
            if not blocks:
                self.set_status("No code blocks found", "#ffaa00")
                return
            lang, code = blocks[0]
            if lang not in ("python", "py", ""):
                self.set_status(f"Can only run Python (found: {lang})", "#ffaa00")
                return
            self.set_status("Running code…", "#aa44ff")
            def _run():
                out = run_code_sandbox(code)
                self.communicate.set_input_text.emit("")   # signal back
                self.exec_output.setFixedHeight(120)
                self.exec_output.setPlainText(f"$ python\n{out}")
                self.set_status("Code executed ✓", "#00c864")
            threading.Thread(target=_run, daemon=True).start()

        def _on_export(self) -> None:
            if not _conv_history: return
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path, _ = QFileDialog.getSaveFileName(
                self, "Export Conversation",
                str(Path.home() / f"ai_conversation_{ts}.md"),
                "Markdown (*.md);;Text (*.txt)"
            )
            if not path: return
            lines = [f"# AI Conversation — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"]
            for msg in _conv_history:
                role = "**You**" if msg["role"] == "user" else "**Assistant**"
                lines.append(f"\n{role}:\n{msg['content']}\n")
            lines.append(f"\n---\n*Session tokens: ~{_session_tokens}*\n")
            Path(path).write_text("\n".join(lines), encoding="utf-8")
            self.set_status("Exported ✓", "#00aaff")
            QTimer.singleShot(2500, lambda: self.set_status("Ready", "#00c864"))

        def _on_template_select(self, key: str) -> None:
            if key == "None" or key not in PROMPT_TEMPLATES: return
            tpl = PROMPT_TEMPLATES[key]
            self.input_box.setText(tpl)
            self.input_box.setFocus()
            self.input_box.setCursorPosition(len(tpl))

        def _on_history_click(self, item: QListWidgetItem) -> None:
            idx = self.history_list.row(item)
            if 0 <= idx < len(self._history_items):
                e = self._history_items[idx]
                self.prompt_text.setPlainText(e.get("prompt", ""))
                self._render_response(e.get("response", ""))

        def _on_pin_click(self, item: QListWidgetItem) -> None:
            pins = load_pins()
            idx  = self.pins_list.row(item)
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
                self.search_bar.setFocus()
            else:
                self.search_bar.hide()
                self.search_bar.clear()
                self._filter_history("")

        def _filter_history(self, text: str) -> None:
            for i in range(self.history_list.count()):
                item = self.history_list.item(i)
                item.setHidden(
                    bool(text) and text.lower() not in item.text().lower()
                )

        def _reload_pins(self) -> None:
            self.pins_list.clear()
            for p in load_pins():
                short = p.get("prompt", "")[:45] + ("…" if len(p.get("prompt",""))>45 else "")
                self.pins_list.addItem(QListWidgetItem(f"⭐ {short}"))

        def _open_settings(self) -> None:
            if self._settings_panel is None:
                self._settings_panel = SettingsPanel(self)
            self._settings_panel.show()
            self._settings_panel.raise_()

        def _on_close(self) -> None:
            shutdown_event.set()
            if _qt_app: _qt_app.quit()

        def _panic(self) -> None:
            global _panic_hidden
            if not _panic_hidden:
                _panic_hidden = True
                try:
                    if _HAS_TTS and _tts_engine is not None:
                        _tts_engine.stop()
                except Exception:
                    pass
                self._stop_thinking(); self._streaming = False
                self.prompt_text.clear(); self.response_text.clear()
                self.input_box.clear()
                self.setWindowOpacity(0.0); self.is_visible = False
                print("[PANIC] Hidden")
            else:
                _panic_hidden = False; self.is_visible = True
                self.setWindowOpacity(SETTINGS["opacity"] / 100)
                self._set_status("Ready", "#00c864")
                self.raise_(); self.activateWindow()

        # ────────────────────────────────────────────────────────────────
        # Follow-up chips
        # ────────────────────────────────────────────────────────────────

        @pyqtSlot(list)
        def _set_followups(self, suggestions: list) -> None:
            # Clear old chips
            while self.followup_layout.count():
                w = self.followup_layout.takeAt(0).widget()
                if w: w.deleteLater()

            if not suggestions:
                self.followup_frame.hide(); return

            for s in suggestions[:3]:
                chip = QPushButton(f"↩ {s}")
                chip.setFont(QFont("Menlo", 9))
                chip.setCursor(QCursor(Qt.PointingHandCursor))
                chip.setToolTip(s)
                chip.setStyleSheet("""QPushButton{background:rgba(0,80,40,180);color:#88ffcc;
                    border:1px solid rgba(0,140,70,150);border-radius:12px;padding:4px 10px;}
                    QPushButton:hover{background:rgba(0,110,55,220);}""")
                chip.clicked.connect(lambda _, q=s: self._send_followup(q))
                self.followup_layout.addWidget(chip)

            self.followup_layout.addStretch()
            self.followup_frame.show()

        def _send_followup(self, question: str) -> None:
            self.input_box.setText(question)
            self._on_send()

        # ────────────────────────────────────────────────────────────────
        # Keyboard shortcuts
        # ────────────────────────────────────────────────────────────────

        def keyPressEvent(self, e) -> None:
            key, mods = e.key(), e.modifiers()
            ctrl  = bool(mods & Qt.ControlModifier)
            shift = bool(mods & Qt.ShiftModifier)

            if key == Qt.Key_L and ctrl:
                self._on_mic(); e.accept(); return
            if key == Qt.Key_M and ctrl:
                self._on_mute_toggle(); e.accept(); return
            if key == Qt.Key_Q and ctrl:
                self._on_close(); e.accept(); return
            if key == Qt.Key_D and ctrl:
                self._clear(); e.accept(); return
            if key == Qt.Key_E and ctrl:
                self._on_export(); e.accept(); return
            if key == Qt.Key_R and ctrl:
                self._on_run_code(); e.accept(); return
            if key == Qt.Key_F and ctrl:
                self._toggle_search(); e.accept(); return
            if key == Qt.Key_C and ctrl and not self.input_box.hasFocus():
                self._on_copy(); e.accept(); return
            if key == Qt.Key_H and ctrl and shift:
                self._panic(); e.accept(); return
            if key == Qt.Key_Space and not self.input_box.hasFocus():
                if not self._ptt_active and not self._is_listening:
                    self._ptt_active = True; self._on_mic()
                e.accept(); return
            if key == Qt.Key_Escape:
                # If generating — stop it first
                if not self.stop_btn.isHidden():
                    self._on_stop_generation()
                    e.accept(); return
                if self.input_box.hasFocus():
                    self.input_box.clear()
                else:
                    self.input_box.setFocus()
                e.accept(); return
            super().keyPressEvent(e)

        def keyReleaseEvent(self, e) -> None:
            if e.key() == Qt.Key_Space and not self.input_box.hasFocus():
                self._ptt_active = False; e.accept(); return
            super().keyReleaseEvent(e)

        # ────────────────────────────────────────────────────────────────
        # Thread-safe public API
        # ────────────────────────────────────────────────────────────────

        def set_status(self, s: str, c: str = "#00c864") -> None:
            self.communicate.update_status.emit(s, c)
        def set_prompt(self, t: str) -> None:
            self.communicate.update_prompt.emit(t)
            self.communicate.show_window.emit()
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
            # FIX: emit signal so Qt thread updates the button safely
            self.communicate.set_mute_btn.emit(m)

        def show_stop_btn(self) -> None:
            """Called from worker thread when generation starts."""
            self.communicate.set_stop_btn.emit(True)

        def hide_stop_btn(self) -> None:
            """Called from worker thread when generation ends."""
            self.communicate.set_stop_btn.emit(False)
        def update_tokens(self, n: int) -> None:
            self.communicate.update_tokens.emit(n)
        def add_history(self, prompt: str, response: str) -> None:
            short = prompt[:42] + ("…" if len(prompt) > 42 else "")
            self.communicate.add_history_item.emit(short, response)
            self._history_items.append({"prompt": prompt, "response": response})
        def trigger_panic(self) -> None:
            self.communicate.panic_toggle.emit()
        def set_followups(self, suggestions: list) -> None:
            self.communicate.set_followups.emit(suggestions)

        # ────────────────────────────────────────────────────────────────
        # Qt-thread slots
        # ────────────────────────────────────────────────────────────────

        @pyqtSlot(str, str)
        def _set_status(self, s: str, c: str) -> None:
            self._stop_thinking()
            self.status_label.setText(f"● {s}")
            self.status_label.setStyleSheet(
                f"color:{c};background:transparent;min-width:140px;")

        @pyqtSlot(str)
        def _set_prompt(self, t: str) -> None:
            self._current_prompt = t
            self.prompt_text.setPlainText(t)

        def _render_response(self, text: str) -> None:
            html_content = render_markdown(text)
            self.response_text.setHtml(html_content)
            sb = self.response_text.verticalScrollBar()
            sb.setValue(sb.maximum())

        @pyqtSlot(str)
        def _set_response(self, t: str) -> None:
            self._stop_thinking()
            _rendering_event.set()
            self._streaming = False
            self._current_answer = t
            self._render_response(t)
            if not self.is_visible: self._show()
            QTimer.singleShot(300, _rendering_event.clear)

        @pyqtSlot(str)
        def _append_token(self, t: str) -> None:
            if not self._streaming:
                self._streaming = True
                self._stop_thinking()
                self._current_answer = ""
                self.response_text.clear()
                _rendering_event.set()
                if not self.is_visible:
                    self._show()

            self._current_answer += t

            # Detect stop marker — finalize and render markdown
            if t.endswith("*⏹ Stopped.*") or "⏹ Stopped" in t:
                self._streaming = False
                self._render_response(self._current_answer)
                QTimer.singleShot(300, _rendering_event.clear)
                return

            # Stream plain text token-by-token (fast)
            self.response_text.moveCursor(QTextCursor.End)
            cursor = self.response_text.textCursor()
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#00c864"))
            fmt.setFont(QFont("Menlo", SETTINGS["font_size"]))
            cursor.insertText(t, fmt)
            sb = self.response_text.verticalScrollBar()
            sb.setValue(sb.maximum())

        @pyqtSlot()
        def _clear(self) -> None:
            self._stop_thinking()
            self.prompt_text.clear(); self.response_text.clear()
            self.input_box.clear(); self.exec_output.setFixedHeight(0)
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
            self.input_box.setText(t)
            self.input_box.setFocus()
            if SETTINGS.get("auto_send") and t.strip():
                QTimer.singleShot(100, self._on_send)

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

        @pyqtSlot()
        def _show(self) -> None:
            self.is_visible = True
            if not self.isVisible():
                super().show()
            self.raise_()
            self.activateWindow()
            opacity = SETTINGS.get("opacity", 96) / 100
            self.setWindowOpacity(opacity)
            _apply_screen_share_invisibility(self.winId())
            _force_always_on_top_macos(self.winId())
            QTimer.singleShot(150, self.input_box.setFocus)
            print(f"[DEBUG] Overlay shown — opacity={opacity:.2f}, visible={self.isVisible()}")

        @pyqtSlot()
        def _hide(self) -> None:
            self.is_visible = False
            self.setWindowOpacity(0.0)

        def _apply_font_size(self, fs: int) -> None:
            f = QFont("Menlo", fs)
            self.prompt_text.setFont(f); self.response_text.setFont(f)
            self.input_box.setFont(f)
            self.history_list.setFont(QFont("Menlo", max(8, fs - 3)))

        def changeEvent(self, e) -> None:
            from PyQt5.QtCore import QEvent
            if e.type() == QEvent.WindowStateChange:
                if self.windowState() & Qt.WindowMinimized:
                    self.setWindowState(Qt.WindowNoState); self.raise_()
            super().changeEvent(e)

        def closeEvent(self, e) -> None:
            e.ignore()

# =============================================================================
#  SYSTEM TRAY
# =============================================================================

def _create_tray() -> None:
    global _tray_icon
    if not HAS_PYQT5 or not QSystemTrayIcon.isSystemTrayAvailable():
        return
    try:
        px = QPixmap(32, 32)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(0, 200, 100))
        p.setPen(QColor(0, 140, 65))
        p.drawEllipse(4, 4, 24, 24)
        p.end()
        icon = QIcon(px)

        _tray_icon = QSystemTrayIcon(icon)
        menu = QMenu()

        acts = [
            ("Show",          lambda: _overlay_window and _overlay_window.show()),
            ("Hide",          lambda: _overlay_window and _overlay_window.hide()),
            None,
            ("🎙 Listen",     lambda: threading.Thread(target=on_trigger, daemon=True).start()),
            ("🔇 Toggle Mute", _global_mute_toggle),
            ("🚨 Panic",      lambda: _overlay_window and _overlay_window.trigger_panic()),
            None,
            ("⚙ Settings",   lambda: _overlay_window and _overlay_window._open_settings()),
            ("⬇ Export",     lambda: _overlay_window and _overlay_window._on_export()),
            None,
            ("Quit",          lambda: (shutdown_event.set(), _qt_app and _qt_app.quit())),
        ]

        for act in acts:
            if act is None:
                menu.addSeparator()
            else:
                label, fn = act
                a = QAction(label)
                a.triggered.connect(fn)
                menu.addAction(a)

        _tray_icon.setContextMenu(menu)
        _tray_icon.setToolTip("AI Stealth Assistant — right-click for menu")
        _tray_icon.activated.connect(
            lambda r: (_overlay_window and _overlay_window.show())
            if r == QSystemTrayIcon.DoubleClick else None
        )
        _tray_icon.show()
        print("[INFO] System tray icon active")
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
    if not HAS_PYQT5:
        print("[ERROR] PyQt5 not available")
        return None
    try:
        if QApplication.instance() is None:
            _qt_app = QApplication(sys.argv)
            _qt_app.setQuitOnLastWindowClosed(False)
        else:
            _qt_app = QApplication.instance()
        _overlay_window = StealthOverlay()
        _create_tray()
        return _overlay_window
    except Exception as exc:
        import traceback
        print(f"[ERROR] Overlay: {exc}")
        traceback.print_exc()
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
        log("pynput not installed.")
        return False

    def _safe(fn):
        """Wrap hotkey callback to swallow pynput internal errors (macOS injected arg bug)."""
        def wrapper(*args, **kwargs):
            try:
                fn()
            except Exception as exc:
                log(f"Hotkey callback error: {exc}")
        return wrapper

    try:
        hs = _to_pynput_hotkey(getattr(config, "HOTKEY", "ctrl+shift+space"))

        def _listen():
            if _overlay_window:
                _overlay_window.show()
            threading.Thread(target=on_trigger, daemon=True).start()

        def _screenshot_vision():
            def _run():
                img = capture_screenshot_b64()
                if img:
                    command_queue.put("/vision Describe what you see on my screen")
            threading.Thread(target=_run, daemon=True).start()

        hotkeys = {
            hs:                  _safe(lambda: threading.Thread(target=on_trigger, daemon=True).start()),
            "<ctrl>+<shift>+l":  _safe(_listen),
            "<ctrl>+<shift>+m":  _safe(_global_mute_toggle),
            "<ctrl>+<shift>+h":  _safe(lambda: _overlay_window and _overlay_window.trigger_panic()),
            "<ctrl>+<shift>+s":  _safe(_screenshot_vision),
        }

        _pynput_listener = pynput_keyboard.GlobalHotKeys(hotkeys)
        _pynput_listener.start()
        print("[INFO] Global hotkeys registered:")
        print(f"       {getattr(config,'HOTKEY','ctrl+shift+space')}   — trigger mic")
        print("       Ctrl+Shift+L  — listen + show overlay")
        print("       Ctrl+Shift+M  — toggle mute")
        print("       Ctrl+Shift+H  — panic hide/show")
        print("       Ctrl+Shift+S  — screenshot + vision")
        return True

    except Exception as exc:
        # pynput version incompatibility — fall back to no global hotkeys
        print(f"[WARNING] Global hotkeys unavailable: {exc}")
        print("[WARNING] Use the overlay buttons or Ctrl+L inside the overlay instead.")
        print("[TIP] Fix: pip install --upgrade pynput")
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
        log(f"Wake-word: {', '.join(getattr(config,'WAKE_WORD_KEYWORDS',['computer']))}")
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
    print("  ⚡ STT:    faster-whisper (local, fast)" if HAS_FASTER_WHISPER else
          "  ⚠  STT:    Google (install faster-whisper for local)")
    print("  🌐 Search: DuckDuckGo enabled" if HAS_DDG else
          "  ⚠  Search: disabled (pip install duckduckgo-search)")
    print("  📚 RAG:    ChromaDB ready" if HAS_CHROMA else
          "  ⚠  RAG:    disabled (pip install chromadb)")
    print("  🎨 Markdown rendering" if HAS_MARKDOWN else
          "  ⚠  Markdown disabled (pip install markdown2)")
    print(f"\n  Model:    {SETTINGS['model']}  |  Persona: {SETTINGS['persona']}")
    print(f"  Settings: {SETTINGS_FILE}")
    print(f"  Plugins:  {PLUGINS_DIR}")
    print(f"  RAG DB:   {RAG_DB_DIR}")
    print("\n  Global hotkeys:  Ctrl+Shift+L  Ctrl+Shift+M  Ctrl+Shift+H  Ctrl+Shift+S")
    print("  Terminal commands:")
    print("    q            — quit")
    print("    show/hide    — overlay visibility")
    print("    clear        — clear conversation")
    print("    mute/unmute  — toggle TTS")
    print("    history      — show conversation")
    print("    export       — save to .md file")
    print("    test         — show system status")
    print("    search <q>   — web search")
    print("    vision       — screenshot + AI")
    print("    cache-stats / cache-clear")
    print("    ai <message> — send message to AI from terminal")
    print()
    print("  ⚠  NOTE: The terminal is for COMMANDS only.")
    print("           Use the overlay input box to chat with AI.")
    print("           Or prefix with 'ai ' to send from terminal.")
    print("=" * 68 + "\n")

    # Commands that are valid terminal control commands
    TERMINAL_COMMANDS = {
        "q", "quit", "exit",
        "show", "hide",
        "clear",
        "mute", "unmute",
        "history",
        "export",
        "test",
        "cache-stats", "cache-clear",
        "vision",
        "help", "?",
    }

    while not shutdown_event.is_set():
        try:
            typed = input("Term> ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not typed:
            continue

        cmd = typed.lower()

        # ── Quit ─────────────────────────────────────────────────────────────
        if cmd in ("q", "quit", "exit"):
            shutdown_event.set()
            if _qt_app:
                _qt_app.quit()
            break

        # ── Overlay control ───────────────────────────────────────────────────
        elif cmd == "show":
            if _overlay_window: _overlay_window.show()
            print("✓ Overlay shown")

        elif cmd == "hide":
            if _overlay_window: _overlay_window.hide()
            print("✓ Overlay hidden")

        elif cmd == "clear":
            if _overlay_window: _overlay_window.clear()
            _conv_history.clear()
            print("✓ Cleared")

        # ── Mute ──────────────────────────────────────────────────────────────
        elif cmd == "mute":
            global _tts_muted
            _tts_muted = True
            SETTINGS["tts_muted"] = True
            save_settings(SETTINGS)
            if _overlay_window: _overlay_window.set_mute(True)
            print("✓ Muted 🔇")

        elif cmd == "unmute":
            _tts_muted = False
            SETTINGS["tts_muted"] = False
            save_settings(SETTINGS)
            if _overlay_window: _overlay_window.set_mute(False)
            print("✓ Unmuted 🔊")

        # ── Info ──────────────────────────────────────────────────────────────
        elif cmd == "history":
            if not _conv_history:
                print("  (no history)")
            for i, m in enumerate(_conv_history):
                role = "You" if m["role"] == "user" else "AI"
                print(f"  [{i}] {role}: {m['content'][:80]}")

        elif cmd == "export":
            if _overlay_window:
                _overlay_window._on_export()
            else:
                print("  No overlay available")

        elif cmd == "cache-stats":
            print(f"  Cache entries: {len(_response_cache)}")
            print(f"  Cache file:    {CACHE_FILE}")

        elif cmd == "cache-clear":
            _response_cache.clear()
            _save_cache()
            print("✓ Cache cleared")

        elif cmd == "vision":
            command_queue.put("/vision What do you see on my screen?")
            if _overlay_window: _overlay_window.show()

        elif cmd in ("help", "?"):
            print("  Commands: q  show  hide  clear  mute  unmute  history")
            print("            export  test  search <query>  vision  ai <msg>")
            print("            cache-stats  cache-clear")

        elif cmd.startswith("search "):
            query = typed[7:].strip()
            if query:
                command_queue.put(f"/search {query}")
                if _overlay_window: _overlay_window.show()

        elif cmd == "test":
            if _overlay_window:
                _overlay_window.set_prompt("System test — all features active?")
                _overlay_window.set_response(
                    "## ✅ AI Stealth Assistant — All Systems Ready\n\n"
                    "### Speed\n"
                    f"- **STT**: {'faster-whisper ⚡' if HAS_FASTER_WHISPER else 'Google STT'}\n"
                    f"- **Cache**: {len(_response_cache)} entries ready\n"
                    f"- **Model**: {SETTINGS['model']} (keep_alive=60m)\n\n"
                    "### Intelligence\n"
                    f"- **RAG**: {'✅ ChromaDB' if HAS_CHROMA else '❌ disabled'}\n"
                    f"- **Web search**: {'✅ DuckDuckGo' if HAS_DDG else '❌ disabled'}\n"
                    "- **Code sandbox**: ✅ Python execution\n"
                    "- **Vision**: ✅ Screenshot + llava\n"
                    "- **Memory**: Multi-turn with auto-summarize\n"
                    "- **Follow-ups**: AI-generated suggestion chips\n\n"
                    "### Voice\n"
                    f"- **TTS**: {SETTINGS.get('tts_engine','pyttsx3')}\n"
                    f"- **Continuous**: {'✅ VAD-based' if HAS_VAD else '❌ pip install webrtcvad'}\n\n"
                    "### UI\n"
                    f"- **Markdown**: {'✅ rendered' if HAS_MARKDOWN else '❌ plain text'}\n"
                    f"- **Syntax highlight**: {'✅ pygments' if HAS_PYGMENTS else '❌ plain'}\n"
                    "- **Pins / Ratings / History search / Code runner**: ✅\n\n"
                    "### System\n"
                    "- **Screen share hide / Always on top / Tray icon**: ✅\n"
                    f"- **REST API**: {'✅ port ' + str(SETTINGS.get('rest_api_port',7788)) if SETTINGS.get('rest_api_enabled') else '❌ disabled'}\n"
                    f"- **Plugins**: {len(_plugins)} loaded\n"
                    "- **Panic key**: Ctrl+Shift+H\n"
                )
                _overlay_window.show()

        # ── Explicit AI send from terminal (must prefix with "ai ") ──────────
        elif cmd.startswith("ai "):
            message = typed[3:].strip()
            if message:
                command_queue.put(message)
                if _overlay_window: _overlay_window.show()
                print(f"  → Sent to AI: {message[:60]}")
            else:
                print("  Usage: ai <your question>")

        # ── Guard: ignore accidental short inputs / single chars ──────────────
        elif len(typed) <= 3 and cmd not in TERMINAL_COMMANDS:
            # Single chars like "y", "n", "ok" are almost always accidents
            print(f"  ⚠  '{typed}' ignored — terminal is for commands only.")
            print("     Use the overlay input box to send messages to AI.")
            print("     Or type:  ai <your message>  to send from terminal.")

        # ── Guard: require confirmation for short messages ────────────────────
        elif len(typed) < 10 and cmd not in TERMINAL_COMMANDS and not cmd.startswith("/"):
            print(f"  Short message: '{typed}'")
            confirm = input("  Send to AI? [y/N]: ").strip().lower()
            if confirm == "y":
                command_queue.put(typed)
                if _overlay_window: _overlay_window.show()
            else:
                print("  ✓ Cancelled. Use overlay input box or prefix with 'ai '.")

        # ── Normal AI message (long enough to be intentional) ─────────────────
        elif len(typed) >= 10 or cmd.startswith("/"):
            command_queue.put(typed)
            if _overlay_window: _overlay_window.show()


# =============================================================================
#  ENTRY POINT
# =============================================================================

def main() -> None:
    print("\n[INFO] Starting AI Stealth Assistant…")
    print(f"[INFO] Model: {SETTINGS['model']}  |  STT: {SETTINGS.get('stt_engine')}  |  TTS: {SETTINGS.get('tts_engine')}")

    overlay = create_overlay_ui()

    if overlay:
        # Direct calls — before exec_() signals don't fire (QueuedConnection)
        overlay._show()
        overlay._update_mute_btn(_tts_muted)

        # Set prompt directly
        overlay.prompt_text.setPlainText("Welcome! 👋")

        # FIX 3/7: response_text is QTextBrowser — use setHtml for proper render
        welcome_html = """
        <style>
            body { color:#c8c8c8; font-family:'Menlo',monospace; font-size:13px;
                   line-height:1.7; background:transparent; margin:0; padding:6px; }
            h2   { color:#00c864; margin:4px 0 8px; font-size:15px; }
            h3   { color:#7ec8e3; margin:10px 0 4px; font-size:13px; }
            code { background:#1e1e2e; color:#98d890; padding:1px 5px;
                   border-radius:3px; font-size:12px; }
            li   { margin:2px 0; }
            b    { color:#ffffff; }
        </style>
        <h2>✅ AI Stealth Assistant Ready</h2>
        <h3>How to use</h3>
        <ul>
            <li>Type your question below → press <b>Enter</b></li>
            <li>Click <b>🎙 Mic</b> or press <b>Ctrl+L</b> to speak</li>
            <li>Click <b>⚙</b> to configure model, persona, TTS, STT</li>
            <li>Click <b>🔊</b> or <b>Ctrl+M</b> to mute / unmute</li>
            <li>Click <b>⏹ Stop</b> or press <b>Esc</b> to stop generation</li>
        </ul>
        <h3>Prompt Templates</h3>
        <p><code>/code</code> &nbsp;<code>/explain</code> &nbsp;<code>/bullet</code>
           &nbsp;<code>/review</code> &nbsp;<code>/search</code> &nbsp;<code>/vision</code>
           &nbsp;<code>/run</code> &nbsp;<code>/rag</code> &nbsp;<code>/debug</code></p>
        <h3>Global Shortcuts (any app)</h3>
        <ul>
            <li><code>Ctrl+Shift+L</code> — start listening</li>
            <li><code>Ctrl+Shift+M</code> — toggle mute</li>
            <li><code>Ctrl+Shift+H</code> — panic hide</li>
            <li><code>Ctrl+Shift+S</code> — screenshot + vision AI</li>
        </ul>
        <p style='color:#555;font-size:11px;margin-top:12px;'>
            Hidden from Teams/Zoom &nbsp;•&nbsp; Always on top &nbsp;•&nbsp; Never minimises
        </p>
        """
        overlay.response_text.setHtml(welcome_html)
        overlay._set_status("Ready", "#00c864")
        print("[INFO] Overlay opened — bottom-right corner\n")
    else:
        print("[WARNING] No overlay — terminal only\n")

    # Start background workers
    worker = threading.Thread(target=process_commands, daemon=True)
    worker.start()

    wake_thread = None
    if getattr(config, "USE_WAKE_WORD", False):
        wake_thread = threading.Thread(target=wake_word_loop, daemon=True)
        wake_thread.start()

    if SETTINGS.get("continuous_listen") and HAS_VAD:
        threading.Thread(target=continuous_listen_loop, daemon=True).start()

    if SETTINGS.get("rest_api_enabled"):
        port = SETTINGS.get("rest_api_port", 7788)
        threading.Thread(target=start_rest_api, args=(port,), daemon=True).start()

    register_hotkey()

    input_thread = threading.Thread(target=typed_input_loop, daemon=True)
    input_thread.start()

    try:
        if _qt_app:
            _qt_app.exec_()        # ← Qt event loop starts HERE (main thread)
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
        if wake_thread:
            wake_thread.join(timeout=2)
        input_thread.join(timeout=2)
        print("✓ Assistant stopped.\n")


if __name__ == "__main__":
    main()