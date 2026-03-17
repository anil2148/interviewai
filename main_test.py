from __future__ import annotations

import json
import queue
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

import ollama
import pyttsx3
import speech_recognition as sr

import config

try:
    from pynput import keyboard as pynput_keyboard
except Exception:
    pynput_keyboard = None

try:
    import pvporcupine
except Exception:
    pvporcupine = None

# Tkinter (more reliable than PyQt5 for basic overlay)
import tkinter as tk
from tkinter import font as tkfont

recognizer = sr.Recognizer()
command_queue: "queue.Queue[str]" = queue.Queue()
shutdown_event = threading.Event()

_tts_lock = threading.Lock()
_tts_engine = pyttsx3.init()
_tts_engine.setProperty("rate", config.TTS_RATE)
_tts_engine.setProperty("volume", config.TTS_VOLUME)

_pynput_listener = None
_overlay_window = None


def log(msg: str) -> None:
    if config.DEBUG:
        print(f"[HiddenAIAssistant] {msg}")


# ============================================================================
# SIMPLE OVERLAY (Tkinter - Always Works)
# ============================================================================

class SimpleOverlay:
    """Simple, reliable overlay using Tkinter."""
    
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Assistant")
        
        # Make window stay on top
        self.root.attributes("-topmost", True)
        
        # Start with some transparency
        self.root.attributes("-alpha", 0.0)
        
        # Remove window decorations
        self.root.overrideredirect(True)
        
        # Position in CENTER of screen (easy to see for testing)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        window_width = 500
        window_height = 300
        
        x = (screen_width - window_width) // 2
        y = (screen_height - window_height) // 2
        
        print(f"[DEBUG] Screen: {screen_width}x{screen_height}")
        print(f"[DEBUG] Window: {window_width}x{window_height} at ({x}, {y})")
        
        self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
        self.root.configure(bg='black')
        
        # Container
        self.container = tk.Frame(
            self.root,
            bg='#1a1a1a',
            highlightbackground='#00ff00',  # Bright green border for testing
            highlightthickness=3
        )
        self.container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Status
        self.status_label = tk.Label(
            self.container,
            text="● OVERLAY IS VISIBLE",
            font=("Helvetica", 14, "bold"),
            fg='#00ff00',
            bg='#1a1a1a'
        )
        self.status_label.pack(pady=10)
        
        # Prompt
        self.prompt_text = tk.Text(
            self.container,
            height=3,
            font=("Helvetica", 12),
            fg='#ffffff',
            bg='#2a2a2a',
            relief=tk.FLAT,
            wrap=tk.WORD
        )
        self.prompt_text.pack(fill=tk.X, padx=10, pady=5)
        
        # Response
        self.response_text = tk.Text(
            self.container,
            height=8,
            font=("Helvetica", 12),
            fg='#00ff88',
            bg='#2a2a2a',
            relief=tk.FLAT,
            wrap=tk.WORD
        )
        self.response_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Close button
        close_btn = tk.Button(
            self.container,
            text="HIDE",
            command=self.hide,
            bg='#ff4444',
            fg='white',
            font=("Helvetica", 10, "bold")
        )
        close_btn.pack(pady=5)
        
        self.is_visible = False
        
        print("[DEBUG] Overlay created!")
    
    def show(self):
        print("[DEBUG] show() called")
        if not self.is_visible:
            self.is_visible = True
            self.root.attributes("-alpha", 0.95)
            print("[DEBUG] Opacity set to 0.95")
    
    def hide(self):
        print("[DEBUG] hide() called")
        if self.is_visible:
            self.is_visible = False
            self.root.attributes("-alpha", 0.0)
    
    def set_status(self, text, color="#00ff00"):
        self.status_label.config(text=f"● {text}", fg=color)
    
    def set_prompt(self, text):
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", text)
        self.show()
    
    def set_response(self, text):
        self.response_text.delete("1.0", tk.END)
        self.response_text.insert("1.0", text)
    
    def run(self):
        print("[DEBUG] Starting Tkinter mainloop...")
        self.root.mainloop()


def create_overlay_ui() -> Optional[SimpleOverlay]:
    """Create simple Tkinter overlay."""
    global _overlay_window
    
    try:
        _overlay_window = SimpleOverlay()
        return _overlay_window
    except Exception as exc:
        print(f"[ERROR] Failed to create overlay: {exc}")
        import traceback
        traceback.print_exc()
        return None


# (Include all the other functions from the original main.py: listen_once, ask_ai, etc.)
# ... [rest of code is same as original]


def main() -> None:
    print("\n" + "="*60)
    print("STARTING AI ASSISTANT WITH SIMPLE OVERLAY")
    print("="*60 + "\n")
    
    # Create overlay
    overlay = create_overlay_ui()
    
    if overlay:
        # Start UI in thread
        ui_thread = threading.Thread(target=overlay.run, daemon=True)
        ui_thread.start()
        time.sleep(1.0)
        
        # FORCE SHOW FOR TESTING
        print("\n[TEST] Forcing overlay to show...")
        overlay.set_prompt("TEST: Can you see this overlay?")
        overlay.set_response("If you can read this, the overlay is working!")
        overlay.set_status("VISIBLE", "#00ff00")
        overlay.show()
        
        print("[TEST] Overlay should be visible in CENTER of screen")
        print("[TEST] Look for a window with GREEN border")
        print("="*60 + "\n")
        
        time.sleep(5)  # Keep it visible for 5 seconds
    
    # Start worker threads
    worker = threading.Thread(target=process_commands, daemon=True)
    worker.start()
    
    # Register hotkey
    hotkey_ok = register_hotkey()
    
    # Main input loop
    typed_input_loop()
    
    shutdown_event.set()
    cleanup_hotkeys()


if __name__ == "__main__":
    main()