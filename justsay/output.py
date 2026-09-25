"""Getting transcribed text into the focused window.

Two methods, both via wtype:

  type   wtype sends the characters one at a time. Works in every window the
         same way and needs no paste shortcut, which matters because
         terminals use ctrl+shift+v while everything else uses ctrl+v.
         Measured at about 220 characters per second with nothing dropped.
  paste  wl-copy the text, then send the paste shortcut for whatever window
         is focused. Instant regardless of length, but it depends on
         recognising the window and it borrows the clipboard for a moment.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time

# Window classes that paste with ctrl+shift+v instead of ctrl+v.
TERMINAL_CLASSES = {
    "foot", "footclient", "alacritty", "kitty", "com.mitchellh.ghostty",
    "org.wezfurlong.wezterm", "wezterm", "xterm", "urxvt", "st",
    "org.gnome.console", "konsole", "terminator", "tilix", "wave",
}


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=15, **kw)


def focused_class() -> str:
    try:
        r = _run(["hyprctl", "activewindow", "-j"])
        return (json.loads(r.stdout) or {}).get("class", "") or ""
    except Exception:
        return ""


def _clipboard_read() -> bytes | None:
    try:
        r = _run(["wl-paste", "--no-newline"])
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _clipboard_write(data: bytes) -> None:
    subprocess.run(["wl-copy"], input=data, timeout=10)


def type_text(text: str) -> bool:
    try:
        return _run(["wtype", "--", text]).returncode == 0
    except Exception:
        return False


def paste_text(text: str, restore_clipboard: bool = True) -> bool:
    shift = focused_class().lower() in TERMINAL_CLASSES
    previous = _clipboard_read() if restore_clipboard else None
    try:
        _clipboard_write(text.encode())
    except Exception:
        return False
    # Let the compositor publish the new selection before the window asks.
    time.sleep(0.06)
    if shift:
        keys = ["wtype", "-M", "ctrl", "-M", "shift", "-k", "v", "-m", "shift", "-m", "ctrl"]
    else:
        keys = ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"]
    try:
        ok = _run(keys).returncode == 0
    except Exception:
        ok = False
    if ok and previous:
        def _restore() -> None:
            time.sleep(0.6)
            try:
                _clipboard_write(previous)
            except Exception:
                pass

        threading.Thread(target=_restore, daemon=True).start()
    return ok


def deliver(text: str, mode: str, fallback: bool = True) -> str:
    """Send the text out. Returns the method that worked, or "" on failure."""
    if mode == "clipboard":
        try:
            _clipboard_write(text.encode())
            return "clipboard"
        except Exception:
            return ""

    order = ["paste", "type"] if mode == "paste" else ["type", "paste"]
    if not fallback:
        order = order[:1]
    for method in order:
        if paste_text(text) if method == "paste" else type_text(text):
            return method
    # Last resort: leave it on the clipboard so the words are not lost.
    try:
        _clipboard_write(text.encode())
        return "clipboard"
    except Exception:
        return ""
