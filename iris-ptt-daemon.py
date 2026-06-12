#!/usr/bin/env python3
"""
iris-ptt — push-to-talk speech-to-text.

Hold CapsLock (remapped to F13 by keyd) to record from the default mic.
On release the clip is POSTed to the iris-comms STT endpoint and the
transcribed text is delivered to the focused window — by clipboard paste
(default, instant) or simulated typing.

Listens on the keyd virtual keyboard so it coexists with the compositor
(no exclusive grab — if this daemon dies the keyboard is unaffected).
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

import evdev
from evdev import ecodes

# --- config -----------------------------------------------------------------
ENDPOINT = os.environ.get("IRIS_PTT_ENDPOINT", "http://10.99.0.2:4260/stt/transcribe")
API_KEY_FILE = os.path.expanduser("~/.config/iris-ptt/api_key")
TRIGGER_KEY = ecodes.KEY_F13          # keyd maps capslock -> f13
KBD_NAME = "keyd virtual keyboard"    # device that emits the remapped key
LANGUAGE = os.environ.get("IRIS_PTT_LANG", "")  # "" = let the server auto-detect
OUTPUT_MODE = os.environ.get("IRIS_PTT_OUTPUT", "paste")  # "paste" or "type"
MIN_HOLD_S = 0.25                     # ignore accidental taps
RECORD_PATH = os.path.join(tempfile.gettempdir(), "iris-ptt.wav")

# Window classes that paste with Ctrl+Shift+V instead of Ctrl+V.
TERMINAL_CLASSES = {
    "foot", "footclient", "kitty", "Alacritty", "alacritty", "wezterm",
    "org.wezfurlong.wezterm", "com.mitchellh.ghostty", "ghostty", "xterm",
    "st", "konsole", "Konsole", "wterm", "urxvt",
}


def notify(msg, urgency="low"):
    try:
        subprocess.run(["notify-send", "-u", urgency, "-t", "1500",
                        "🎙 iris-ptt", msg], check=False)
    except FileNotFoundError:
        pass


def load_api_key():
    with open(API_KEY_FILE) as f:
        return f.read().strip()


def find_keyboard():
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if d.name == KBD_NAME and TRIGGER_KEY in d.capabilities().get(ecodes.EV_KEY, []):
            return d
    for path in evdev.list_devices():       # fallback: any device with the key
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        if TRIGGER_KEY in d.capabilities().get(ecodes.EV_KEY, []):
            return d
    return None


def start_recording():
    return subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16",
         RECORD_PATH],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def stop_recording(proc):
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def transcribe(api_key):
    cmd = ["curl", "-sS", "-m", "60", "-X", "POST",
           "-H", f"X-API-Key: {api_key}",
           "-F", f"audio=@{RECORD_PATH}"]
    if LANGUAGE:
        cmd += ["-F", f"language={LANGUAGE}"]
    cmd.append(ENDPOINT)
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=70)
    if out.returncode != 0:
        raise RuntimeError(f"curl failed: {out.stderr.strip()}")
    return json.loads(out.stdout).get("text", "").strip()


def _active_is_terminal():
    try:
        out = subprocess.run(["hyprctl", "activewindow", "-j"],
                             capture_output=True, text=True, timeout=2)
        cls = (json.loads(out.stdout) or {}).get("class", "")
        return cls in TERMINAL_CLASSES
    except Exception:
        return False


def deliver(text):
    """Put text into the focused window — paste (default) or type."""
    if OUTPUT_MODE == "type":
        subprocess.run(["wtype", text], check=False)
        return
    # paste: stash clipboard, copy, send paste keystroke, restore
    try:
        prev = subprocess.run(["wl-paste", "-n"], capture_output=True,
                              text=True, timeout=2).stdout
    except Exception:
        prev = None
    subprocess.run(["wl-copy"], input=text, text=True, check=False)
    time.sleep(0.05)
    if _active_is_terminal():
        subprocess.run(["wtype", "-M", "ctrl", "-M", "shift", "v",
                        "-m", "shift", "-m", "ctrl"], check=False)
    else:
        subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], check=False)
    if prev:                                   # best-effort restore of text clip
        time.sleep(0.25)
        subprocess.run(["wl-copy"], input=prev, text=True, check=False)


def main():
    api_key = load_api_key()
    dev = find_keyboard()
    if dev is None:
        print("error: no input device emits the trigger key", file=sys.stderr)
        sys.exit(1)
    print(f"iris-ptt: listening on {dev.path} ({dev.name}); output={OUTPUT_MODE}",
          flush=True)

    rec = None
    press_t = 0.0
    for ev in dev.read_loop():
        if ev.type != ecodes.EV_KEY or ev.code != TRIGGER_KEY:
            continue
        if ev.value == 1:                       # key down
            press_t = time.time()
            try:
                rec = start_recording()
                notify("listening…")
            except Exception as e:               # noqa: BLE001
                notify(f"record error: {e}", "critical")
                rec = None
        elif ev.value == 0 and rec is not None:  # key up
            stop_recording(rec)
            rec = None
            if time.time() - press_t < MIN_HOLD_S:
                continue
            try:
                text = transcribe(api_key)
                if text:
                    deliver(text)
                else:
                    notify("(no speech)")
            except Exception as e:               # noqa: BLE001
                notify(f"stt error: {e}", "critical")
        # ev.value == 2 (autorepeat) ignored


if __name__ == "__main__":
    main()
