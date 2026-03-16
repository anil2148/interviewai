"""Configuration for HiddenAIAssistant.

Environment variable overrides are supported for the most common options.
"""

import os
import platform

# Runtime platform.
PLATFORM = "windows" if platform.system().lower().startswith("win") else platform.system().lower()

# Activation hotkey used by the `keyboard` package.
HOTKEY = os.getenv("HIDDEN_AI_HOTKEY", "ctrl+shift+a")

# Enable Picovoice Porcupine wake-word listener thread.
USE_WAKE_WORD = os.getenv("HIDDEN_AI_USE_WAKE_WORD", "false").lower() == "true"

# Picovoice Porcupine settings (only used if USE_WAKE_WORD=True).
PORCUPINE_ACCESS_KEY = os.getenv("PORCUPINE_ACCESS_KEY", "")
WAKE_WORD_KEYWORDS = [
    kw.strip() for kw in os.getenv("HIDDEN_AI_WAKE_KEYWORDS", "jarvis").split(",") if kw.strip()
]

# Speech-to-text engine: "google" (online) or "vosk" (offline).
STT_ENGINE = os.getenv("HIDDEN_AI_STT_ENGINE", "google").lower()

# Optional path to a Vosk model directory when STT_ENGINE="vosk".
VOSK_MODEL_PATH = os.getenv("HIDDEN_AI_VOSK_MODEL_PATH", "")

# AI engine: "ollama" (local/offline) or "openai" (online).
AI_ENGINE = os.getenv("HIDDEN_AI_AI_ENGINE", "ollama").lower()

# Ollama settings.
OLLAMA_MODEL = os.getenv("HIDDEN_AI_OLLAMA_MODEL", "llama3.1")
OLLAMA_HOST = os.getenv("HIDDEN_AI_OLLAMA_HOST", "http://localhost:11434")

# OpenAI-compatible settings.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("HIDDEN_AI_OPENAI_MODEL", "gpt-4o-mini")
OPENAI_BASE_URL = os.getenv("HIDDEN_AI_OPENAI_BASE_URL", "https://api.openai.com/v1")

# Microphone tuning.
LISTEN_TIMEOUT_SECONDS = float(os.getenv("HIDDEN_AI_LISTEN_TIMEOUT", "5"))
LISTEN_PHRASE_TIME_LIMIT_SECONDS = float(os.getenv("HIDDEN_AI_PHRASE_TIME_LIMIT", "12"))
AMBIENT_ADJUST_SECONDS = float(os.getenv("HIDDEN_AI_AMBIENT_SECONDS", "0.6"))

# TTS tuning for pyttsx3.
TTS_RATE = int(os.getenv("HIDDEN_AI_TTS_RATE", "190"))
TTS_VOLUME = float(os.getenv("HIDDEN_AI_TTS_VOLUME", "1.0"))

# Logging verbosity.
DEBUG = os.getenv("HIDDEN_AI_DEBUG", "true").lower() == "true"

# Hide Windows console window at startup for private background use.
HIDE_CONSOLE = os.getenv("HIDDEN_AI_HIDE_CONSOLE", "true").lower() == "true"
