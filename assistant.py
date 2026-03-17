from __future__ import annotations

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
    if len(audio_data) < 8000:
        return None
    tmp = None
    try:
        import warnings
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_data)
            tmp = f.name
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=RuntimeWarning)
            segments, _ = model.transcribe(
                tmp, beam_size=1, language="en", vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=400),
            )
            text = " ".join(s.text for s in segments).strip()
        return text or None
    except Exception as exc:
        log(f"faster-whisper: {exc}")
        return None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


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
    if not _HAS_TTS or _tts_engine is None:
        return
    with _tts_lock:
        try:
            _tts_engine.say(text)
            _tts_engine.runAndWait()
        except RuntimeError as exc:
            log(f"pyttsx3 runloop (macOS): {exc}")
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


# =============================================================================
#  STT
# =============================================================================

def listen_once(timeout: float) -> Optional[str]:
    adj            = SETTINGS.get("ambient_adjust_sec", 0.1)
    listen_timeout = SETTINGS.get("listen_timeout_sec", 60)
    phrase_limit_s = SETTINGS.get("phrase_time_limit_sec", 0)
    phrase_limit   = phrase_limit_s if phrase_limit_s > 0 else None

    try:
        if _overlay_window:
            _overlay_window.set_status("🎙 Listening… (speak your question)", "#ffaa00")

        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=adj)
            try:
                audio = recognizer.listen(source, timeout=listen_timeout,
                                          phrase_time_limit=phrase_limit)
            except sr.WaitTimeoutError:
                if _overlay_window:
                    _overlay_window.set_status("Ready", "#00c864")
                return None

        if _overlay_window:
            _overlay_window.set_status("Transcribing…", "#00aaff")

        engine = SETTINGS.get("stt_engine", "faster_whisper")

        if engine == "faster_whisper" and HAS_FASTER_WHISPER:
            return transcribe_faster_whisper(audio.get_wav_data())

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
#  CONTINUOUS LISTENING
# =============================================================================

def continuous_listen_loop() -> None:
    if not HAS_VAD:
        return
    try:
        import pyaudio
        vad    = webrtcvad.Vad(2)
        pa     = pyaudio.PyAudio()
        RATE   = 16000
        CHUNK  = 480
        stream = pa.open(rate=RATE, channels=1, format=pyaudio.paInt16,
                         input=True, frames_per_buffer=CHUNK)

        frames = []; speaking = False; silent_cnt = 0
        SILENCE_LIMIT = 20

        while not shutdown_event.is_set() and SETTINGS.get("continuous_listen"):
            chunk     = stream.read(CHUNK, exception_on_overflow=False)
            is_speech = vad.is_speech(chunk, RATE)

            if is_speech:
                frames.append(chunk); speaking = True; silent_cnt = 0
            elif speaking:
                frames.append(chunk); silent_cnt += 1
                if silent_cnt > SILENCE_LIMIT:
                    audio_data = b"".join(frames)
                    frames = []; speaking = False; silent_cnt = 0
                    text = transcribe_faster_whisper(audio_data) if HAS_FASTER_WHISPER else None
                    if text and len(text) > 2:
                        if _overlay_window:
                            _overlay_window.set_input(text)
                        command_queue.put(text)
            time.sleep(0.001)

        stream.stop_stream(); stream.close(); pa.terminate()
    except Exception as exc:
        log(f"continuous_listen: {exc}")


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
            for chunk in stream:
                if _generation_stop_event.is_set():
                    log("Generation stopped by user")
                    if _overlay_window:
                        _overlay_window.communicate.append_token.emit("\n\n*⏹ Stopped.*")
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
    if not _listen_lock.acquire(blocking=False):
        log("Already listening — ignoring duplicate trigger")
        return
    try:
        _on_trigger_inner()
    finally:
        _listen_lock.release()


def _on_trigger_inner() -> None:
    if shutdown_event.is_set():
        return
    window_title = get_active_window_title()
    if window_title:
        auto_switch_persona(window_title)

    listen_timeout = SETTINGS.get("listen_timeout_sec", 60)
    if _overlay_window:
        _overlay_window.communicate.update_status.emit(
            f"🎙 Listening… ({listen_timeout}s max)", "#ff6600")

    text = listen_once(timeout=listen_timeout)

    if _overlay_window:
        _overlay_window.communicate.reset_mic_btn.emit()

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
    log("Starting HiddenAIAssistant.")

    worker = threading.Thread(target=process_commands, daemon=True)
    worker.start()

    wake_thread = None
    if getattr(config, "USE_WAKE_WORD", False):
        wake_thread = threading.Thread(target=wake_word_loop, daemon=True)
        wake_thread.start()

    print("Press Enter to start listening. Type q then Enter to quit.")

while True:
    cmd = input().strip().lower()
    if cmd == "q":
        break
    on_hotkey()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted.")
    finally:
        shutdown_event.set()
        keyboard.unhook_all_hotkeys()
        worker.join(timeout=2)
        if wake_thread: wake_thread.join(timeout=2)
        input_thread.join(timeout=2)
        print("✓ Assistant stopped.\n")


if __name__ == "__main__":
    main()
    
