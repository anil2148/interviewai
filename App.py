"""
app.py — FastAPI web service for the AI Stealth Assistant
==========================================================
Start with:  uvicorn app:app --host 0.0.0.0 --port $PORT

Endpoints
---------
GET  /                  health check + available models
GET  /settings          current settings as JSON
POST /settings          update one or more settings keys
POST /ask               send a prompt, get a full response (blocking)
WS   /ask/stream        send a prompt, receive tokens one-by-one via WebSocket
POST /transcribe        upload a WAV/WebM blob, get back the transcribed text
GET  /personas          list available personas
GET  /templates         list available prompt templates
DELETE /cache           clear the response cache
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
import threading
from typing import Any, Dict, Optional

# ── FastAPI ───────────────────────────────────────────────────────────────────
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Core agent logic (imported from the main module) ─────────────────────────
# upgraded_agent.py tries to import PyQt6 at module level but wraps it in try/except,
# so importing it headlessly on Render is safe — HAS_PYQT6 will be False.
# We set QT_QPA_PLATFORM before the import so Qt doesn't try to open a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    import upgraded_agent as agent
except Exception as exc:
    print(f"[FATAL] Could not import upgraded_agent: {exc}", file=sys.stderr)
    raise

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("app")

# =============================================================================
#  FastAPI app
# =============================================================================

app = FastAPI(
    title="AI Stealth Assistant API",
    description="REST + WebSocket interface to the AI Interview/Coding Assistant",
    version="1.0.0",
)

# Allow browser clients from any origin (tighten in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
#  Startup: warm up Whisper model in background so first /transcribe is instant
# =============================================================================

@app.on_event("startup")
async def _startup() -> None:
    log.info("Starting AI Assistant web service…")
    log.info(f"  Model   : {agent.SETTINGS.get('model')}")
    log.info(f"  Persona : {agent.SETTINGS.get('persona')}")
    log.info(f"  STT     : {agent.SETTINGS.get('stt_engine')}")
    # Pre-load and warm Whisper in a background thread so the event loop
    # is not blocked during startup.
    threading.Thread(target=agent._preload_whisper_model,
                     daemon=True, name="whisper-warmup").start()
    log.info("Whisper warm-up started in background ✓")


# =============================================================================
#  Request / Response models
# =============================================================================

class AskRequest(BaseModel):
    prompt: str
    persona: Optional[str] = None      # override persona for this request only
    template: Optional[str] = None     # e.g. "/interview" — prepends the template


class SettingsUpdate(BaseModel):
    updates: Dict[str, Any]


# =============================================================================
#  Endpoints
# =============================================================================

@app.get("/", summary="Health check")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "model": agent.SETTINGS.get("model"),
        "persona": agent.SETTINGS.get("persona"),
        "stt_engine": agent.SETTINGS.get("stt_engine"),
        "cache_entries": len(agent._response_cache),
    })


@app.get("/settings", summary="Get current settings")
async def get_settings() -> JSONResponse:
    # Strip keys that are irrelevant / sensitive in web context
    safe = {k: v for k, v in agent.SETTINGS.items()
            if k not in ("elevenlabs_key",)}
    return JSONResponse(safe)


@app.post("/settings", summary="Update settings")
async def update_settings(body: SettingsUpdate) -> JSONResponse:
    allowed = {
        "model", "persona", "stt_engine", "whisper_model",
        "tts_engine", "tts_rate", "tts_volume", "tts_muted",
        "web_search_enabled", "rag_enabled", "auto_clear_minutes",
        "listen_timeout_sec", "pause_threshold_sec",
    }
    applied: Dict[str, Any] = {}
    for key, value in body.updates.items():
        if key not in allowed:
            raise HTTPException(status_code=400,
                                detail=f"Setting '{key}' cannot be changed via API")
        agent.SETTINGS[key] = value
        applied[key] = value

    agent.save_settings(agent.SETTINGS)

    # If the Whisper model size changed, reload it in the background
    if "whisper_model" in applied:
        agent._whisper_model = None
        threading.Thread(target=agent._preload_whisper_model,
                         daemon=True).start()

    return JSONResponse({"updated": applied})


@app.get("/personas", summary="List available personas")
async def list_personas() -> JSONResponse:
    return JSONResponse({"personas": list(agent.PERSONAS.keys())})


@app.get("/templates", summary="List available prompt templates")
async def list_templates() -> JSONResponse:
    return JSONResponse({"templates": list(agent.PROMPT_TEMPLATES.keys())})


@app.post("/ask", summary="Send a prompt and get the full response (blocking)")
async def ask(body: AskRequest) -> JSONResponse:
    """
    Sends prompt to the LLM and returns the complete answer.
    For streaming use the WebSocket endpoint /ask/stream instead.
    """
    prompt = _build_prompt(body)

    # Run the blocking LLM call in a thread pool so the event loop stays free
    loop = asyncio.get_event_loop()
    try:
        answer = await loop.run_in_executor(
            None, lambda: agent.ask_ai_streaming(prompt)
        )
    except Exception as exc:
        log.error(f"/ask error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return JSONResponse({"answer": answer, "prompt": body.prompt})


@app.websocket("/ask/stream")
async def ask_stream(ws: WebSocket) -> None:
    """
    WebSocket streaming endpoint.

    Client sends JSON:  {"prompt": "...", "persona": "...", "template": "..."}
    Server streams back JSON frames:
      {"type": "token",  "text": "..."}   — one per generated token
      {"type": "done",   "text": "..."}   — full answer when complete
      {"type": "error",  "text": "..."}   — on failure
    """
    await ws.accept()
    try:
        raw = await ws.receive_text()
        data = json.loads(raw)
        body = AskRequest(**data)
        prompt = _build_prompt(body)

        token_queue: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_event_loop()
        full_answer: list[str] = []

        def _stream_worker() -> None:
            """Runs ask_ai_streaming in a thread; pushes tokens onto the queue."""
            try:
                # Monkey-patch the overlay token emission to push into the queue
                def _emit(token: str) -> None:
                    full_answer.append(token)
                    loop.call_soon_threadsafe(token_queue.put_nowait, token)

                # Temporarily redirect token output
                original_append = getattr(agent, "_ws_token_hook", None)
                agent._ws_token_hook = _emit
                answer = agent.ask_ai_streaming(prompt)
                agent._ws_token_hook = original_append
                # Signal completion
                loop.call_soon_threadsafe(token_queue.put_nowait, None)
            except Exception as exc:
                loop.call_soon_threadsafe(
                    token_queue.put_nowait, {"__error__": str(exc)})

        threading.Thread(target=_stream_worker, daemon=True).start()

        # Stream tokens to the WebSocket as they arrive
        while True:
            token = await token_queue.get()
            if token is None:
                # Stream complete
                await ws.send_text(json.dumps({
                    "type": "done",
                    "text": "".join(full_answer),
                }))
                break
            if isinstance(token, dict) and "__error__" in token:
                await ws.send_text(json.dumps({
                    "type": "error",
                    "text": token["__error__"],
                }))
                break
            await ws.send_text(json.dumps({"type": "token", "text": token}))

    except WebSocketDisconnect:
        log.info("WebSocket client disconnected")
    except Exception as exc:
        log.error(f"/ask/stream error: {exc}")
        try:
            await ws.send_text(json.dumps({"type": "error", "text": str(exc)}))
        except Exception:
            pass


@app.post("/transcribe", summary="Transcribe an uploaded audio file (WAV/WebM/OGG)")
async def transcribe(file: UploadFile = File(...)) -> JSONResponse:
    """
    Accept an audio blob from the browser (recorded via MediaRecorder API)
    and return the transcribed text.

    The browser should send:
      Content-Type: multipart/form-data
      file: <audio blob>   (WAV, WebM, or OGG — Whisper handles all three)

    Example fetch():
      const blob = new Blob(chunks, { type: "audio/webm" });
      const fd = new FormData();
      fd.append("file", blob, "recording.webm");
      const res = await fetch("/transcribe", { method: "POST", body: fd });
      const { text } = await res.json();
    """
    audio_bytes = await file.read()
    if len(audio_bytes) < 500:
        raise HTTPException(status_code=400, detail="Audio too short or empty")

    loop = asyncio.get_event_loop()
    try:
        text = await loop.run_in_executor(
            None,
            lambda: agent._transcribe_bytes(
                audio_bytes, sample_rate=16000, sample_width=2
            ),
        )
    except Exception as exc:
        log.error(f"/transcribe error: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    if not text:
        return JSONResponse({"text": "", "heard": False})

    return JSONResponse({"text": text.strip(), "heard": True})


@app.delete("/cache", summary="Clear the response cache")
async def clear_cache() -> JSONResponse:
    count = len(agent._response_cache)
    agent._response_cache.clear()
    agent._save_cache()
    return JSONResponse({"cleared": count})


# =============================================================================
#  Helpers
# =============================================================================

def _build_prompt(body: AskRequest) -> str:
    """Apply persona override and prepend template prefix if requested."""
    # Temporarily switch persona if caller specified one
    original_persona = agent.SETTINGS.get("persona")
    if body.persona and body.persona in agent.PERSONAS:
        agent.SETTINGS["persona"] = body.persona

    prompt = body.prompt.strip()

    # Prepend template prefix if requested
    if body.template and body.template in agent.PROMPT_TEMPLATES:
        prefix = agent.PROMPT_TEMPLATES[body.template]
        if isinstance(prefix, tuple):
            prefix = prefix[0]
        prompt = prefix + prompt

    # Restore persona
    if body.persona:
        agent.SETTINGS["persona"] = original_persona

    return prompt
