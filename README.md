# HiddenAIAssistant

A background voice assistant that listens on a hotkey (or optional wake word), sends your spoken prompt to an AI backend, then reads the response out loud.

This guide gives full local setup instructions for **Windows**, **macOS**, and **Linux**.

---

## 1) What this app does

- Press a global hotkey (default: `Ctrl+Shift+A`) to start listening.
- Your speech is converted to text with `SpeechRecognition`.
- Text is sent to either:
  - **Ollama** (local model), or
  - **OpenAI-compatible API**.
- The answer is spoken by `pyttsx3`.
- Global hotkey backend: `keyboard` on Windows, `pynput` on macOS/Linux.
- Optional wake-word support via `pvporcupine`.

---

## 2) Prerequisites

- Python **3.10+** (3.11 recommended)
- Working microphone
- Internet connection (only if using Google STT or OpenAI API)
- OS audio permissions granted to terminal/Python

You will also need system audio libraries, especially for `pyaudio`.

### Windows prerequisites

1. Install Python from [python.org](https://www.python.org/downloads/) and check **Add Python to PATH**.
2. Install Microsoft C++ Build Tools **if** any package compilation fails.

### macOS prerequisites

```bash
xcode-select --install
brew install portaudio
```

### Ubuntu/Debian prerequisites

```bash
sudo apt update
sudo apt install -y python3-venv python3-dev portaudio19-dev espeak ffmpeg
```

---

## 3) Clone and enter project

```bash
git clone <your-repo-url>
cd interviewai
```

---

## 4) Create virtual environment and install dependencies

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
```

### macOS/Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

> If `pyaudio` fails to install, ensure PortAudio dev libraries are installed (see prerequisites), then retry.

---

## 5) Configure environment variables

The app reads configuration from environment variables defined in `config.py`.

### Quick start (Ollama + Google STT)

- No required variables for basic run.
- Defaults:
  - hotkey: `ctrl+shift+a`
  - STT: `google`
  - AI engine: `ollama`
  - model: `llama3.1`

You must have Ollama running locally if using default AI settings.

#### Start Ollama (separate terminal)

```bash
ollama serve
```

#### Pull a model (one time)

```bash
ollama pull llama3.1
```

### Optional: OpenAI backend

Set these variables before running:

#### macOS/Linux

```bash
export HIDDEN_AI_AI_ENGINE=openai
export OPENAI_API_KEY=your_api_key_here
export HIDDEN_AI_OPENAI_MODEL=gpt-4o-mini
# optional custom endpoint
# export HIDDEN_AI_OPENAI_BASE_URL=https://api.openai.com/v1
```

#### Windows (PowerShell)

```powershell
$env:HIDDEN_AI_AI_ENGINE = "openai"
$env:OPENAI_API_KEY = "your_api_key_here"
$env:HIDDEN_AI_OPENAI_MODEL = "gpt-4o-mini"
# optional:
# $env:HIDDEN_AI_OPENAI_BASE_URL = "https://api.openai.com/v1"
```

### Optional: Wake word

If you want wake-word support:

- Set `HIDDEN_AI_USE_WAKE_WORD=true`
- Set `PORCUPINE_ACCESS_KEY`
- Optionally set `HIDDEN_AI_WAKE_KEYWORDS` (comma-separated)

Example (macOS/Linux):

```bash
export HIDDEN_AI_USE_WAKE_WORD=true
export PORCUPINE_ACCESS_KEY=your_picovoice_key
export HIDDEN_AI_WAKE_KEYWORDS=jarvis
```

---

## 6) Run the assistant

```bash
python assistant.py
```

What to expect:

1. App starts and keeps running in the background loop.
2. Press `Ctrl+Shift+A` (or your configured hotkey).
3. Speak your prompt.
4. The assistant generates the answer in a worker thread and reads it out loud.

Stop with `Ctrl+C` only if running in a visible terminal session.

---

## 7) Run in background (optional)

### Windows

```powershell
pythonw.exe assistant.py
# Optional if using python.exe:
$env:HIDDEN_AI_HIDE_CONSOLE = "true"
python assistant.py
```

### macOS/Linux

```bash
nohup python3 assistant.py > assistant.log 2>&1 &
```

---

## 8) Common customization

Set variables as needed:

- `HIDDEN_AI_HOTKEY` (default `ctrl+shift+a`)
- `HIDDEN_AI_STT_ENGINE` (`google` or `vosk`)
- `HIDDEN_AI_VOSK_MODEL_PATH` (required for Vosk)
- `HIDDEN_AI_AI_ENGINE` (`ollama` or `openai`)
- `HIDDEN_AI_OLLAMA_MODEL` (default `llama3.1`)
- `HIDDEN_AI_LISTEN_TIMEOUT`
- `HIDDEN_AI_PHRASE_TIME_LIMIT`
- `HIDDEN_AI_TTS_RATE`
- `HIDDEN_AI_TTS_VOLUME`
- `HIDDEN_AI_DEBUG`
- `HIDDEN_AI_HIDE_CONSOLE` (`true` by default on Windows, hides terminal window)

---

## 9) Troubleshooting

### `ModuleNotFoundError`

Make sure your virtual environment is activated and dependencies installed:

```bash
pip install -r requirements.txt
```

### `pyaudio` fails with `portaudio.h` not found on macOS

Run:

```bash
brew update
brew install portaudio
export CFLAGS="-I$(brew --prefix portaudio)/include"
export LDFLAGS="-L$(brew --prefix portaudio)/lib"
export PKG_CONFIG_PATH="$(brew --prefix portaudio)/lib/pkgconfig"
pip install --no-cache-dir --force-reinstall PyAudio==0.2.14
```

Then retry:

```bash
pip install -r requirements.txt
```

### No microphone input

- Check OS microphone permissions for your terminal/python.
- Confirm the correct input device is active.

### No AI response

- If using Ollama: ensure `ollama serve` is running and model is pulled.
- If using OpenAI: verify `OPENAI_API_KEY` and internet connectivity.

### Hotkey not firing on Linux/macOS

The `keyboard` package may require elevated permissions or accessibility permissions depending on OS.

### Wake word not working

- Confirm `pvporcupine` is installed.
- Ensure `PORCUPINE_ACCESS_KEY` is valid.
- Verify `HIDDEN_AI_USE_WAKE_WORD=true`.

---

## 10) Minimal quick test

After setup, run:

```bash
python -c "import assistant; print('import OK')"
```

Then start the app:

```bash
python assistant.py
```

Press hotkey and speak to validate end-to-end behavior.


### macOS hotkey permission error

If hotkeys do not work on macOS, grant **Accessibility** permission to your terminal/python app in:

- System Settings → Privacy & Security → Accessibility

The assistant now falls back safely (no crash) and can still work with wake word if enabled.
