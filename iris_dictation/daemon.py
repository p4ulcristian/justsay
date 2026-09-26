"""Push-to-talk dictation daemon.

Holds the speech model resident so a key press costs nothing but the audio
and about a tenth of a second of inference per second of speech.

Flow: hold the key -> record -> release -> transcribe -> paste into the
focused window.
"""

from __future__ import annotations

import logging
import os
import queue
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time

import evdev
import numpy as np

from . import config as cfgmod
from . import languages
from . import output as out
from .audio import HotRecorder, Recorder
from .mute import StreamMuter

log = logging.getLogger("iris-dictation")

SOCKET_NAME = "iris-dictation.sock"
LEVELS_SOCKET = "levels.sock"   # live state + voice level for the waveform overlay
NOTIFY_TAG = "iris-dictation-status"


def notify(summary: str, body: str = "", timeout: int = 2000) -> None:
    """One notification slot that replaces itself, so nothing piles up."""
    try:
        subprocess.Popen(
            [
                "notify-send",
                "-a", "iris-dictation",
                "-t", str(timeout),
                "-h", f"string:x-canonical-private-synchronous:{NOTIFY_TAG}",
                "-h", f"string:x-dunst-stack-tag:{NOTIFY_TAG}",
                summary,
                body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


class LevelServer(threading.Thread):
    """Broadcasts one line per event to every connected client (the Omarchy
    shell's waveform overlay): "recording", "level 0.42" about every 20 ms
    while recording (0..1, voice loudness), "transcribing", "text <result>",
    "typing <chars>" (0 = instant paste), "nothing", "idle". Replaces the Recording/Transcribing notifications."""

    def __init__(self, path: str) -> None:
        super().__init__(daemon=True)
        self.path = path
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()

    def run(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        os.chmod(self.path, 0o600)
        srv.listen(4)
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            conn.settimeout(0.05)
            with self._lock:
                self._clients.append(conn)

    def send(self, line: str) -> None:
        data = (line.replace("\n", " ") + "\n").encode()
        with self._lock:
            for c in list(self._clients):
                try:
                    c.sendall(data)
                except OSError:
                    self._clients.remove(c)
                    c.close()

    def level(self, chunk: bytes) -> None:
        """Loudness of one raw chunk as 0..1 on a dB scale: -54 dB (the
        silence on a typical headset) is 0, -40 dB (normal talking on a quiet mic) is 1."""
        n = len(chunk) // 2
        if n == 0:
            return
        x = np.frombuffer(chunk[: n * 2], dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(x * x))) + 1e-9
        db = 20 * np.log10(rms)
        self.send(f"level {min(1.0, max(0.0, (db + 54) / 14)):.3f}")


class KeyWatcher(threading.Thread):
    """Reads evdev keyboards and pushes ('down'|'up') onto a queue.

    evdev rather than a Hyprland bind: it sees the physical key regardless of
    how xkb maps it, it gives a real release event, and it keeps working when
    another window holds a keyboard grab.

    Devices are rescanned periodically so a keyboard that reconnects (a
    wireless receiver waking up, a USB replug) starts working again on its
    own, and a device that dies is dropped without disturbing the others.
    """

    RESCAN_SECONDS = 5.0

    def __init__(self, keycode: int, devices: list[str], events: queue.Queue) -> None:
        super().__init__(daemon=True)
        self.keycode = keycode
        self.devices = devices      # fixed paths from the config; empty = discover
        self.events = events
        self._stop = threading.Event()
        self._open: dict[str, evdev.InputDevice] = {}
        self._sel = selectors.DefaultSelector()

    def stop(self) -> None:
        self._stop.set()

    def _wanted(self) -> list[str]:
        return self.devices or discover_keyboards(self.keycode)

    def _drop(self, path: str) -> None:
        dev = self._open.pop(path, None)
        if dev is None:
            return
        try:
            self._sel.unregister(dev)
        except Exception:
            pass
        try:
            dev.close()
        except Exception:
            pass
        log.info("dropped %s", path)

    def _rescan(self) -> None:
        wanted = set(self._wanted())
        for path in list(self._open):
            if path not in wanted:
                self._drop(path)
        for path in wanted - set(self._open):
            try:
                dev = evdev.InputDevice(path)
            except Exception as exc:
                log.debug("cannot open %s: %s", path, exc)
                continue
            self._open[path] = dev
            self._sel.register(dev, selectors.EVENT_READ)
            log.info("watching %s (%s)", dev.name, path)

    def prepare(self) -> int:
        """Open the devices up front so no press is missed at startup."""
        self._rescan()
        return len(self._open)

    def run(self) -> None:
        last_scan = time.monotonic() if self._open else 0.0
        warned_empty = False
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_scan >= self.RESCAN_SECONDS:
                self._rescan()
                last_scan = now
                if not self._open and not warned_empty:
                    log.error("no readable keyboard exposes the key yet")
                    warned_empty = True
                elif self._open:
                    warned_empty = False
            if not self._open:
                time.sleep(0.5)
                continue
            try:
                ready = self._sel.select(timeout=1)
            except Exception:
                time.sleep(0.2)
                continue
            for key, _ in ready:
                dev = key.fileobj
                try:
                    for ev in dev.read():
                        if ev.type != evdev.ecodes.EV_KEY or ev.code != self.keycode:
                            continue
                        if ev.value == 1:
                            log.debug("key down on %s", dev.path)
                            self.events.put("down")
                        elif ev.value == 0:
                            log.debug("key up on %s", dev.path)
                            self.events.put("up")
                        # value 2 is autorepeat, ignore it
                except OSError:
                    # Only this device went away. Keep the others live and let
                    # the next rescan pick it up again if it comes back.
                    log.warning("%s disappeared", dev.path)
                    self._drop(dev.path)
                    last_scan = 0.0


class ControlServer(threading.Thread):
    """Unix socket so iris-dictation can drive the same state machine."""

    def __init__(self, path: str, events: queue.Queue, status) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.events = events
        self.status = status
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        if os.path.exists(self.path):
            os.unlink(self.path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.path)
        os.chmod(self.path, 0o600)
        srv.listen(8)
        srv.settimeout(1)
        while not self._stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    cmd = conn.recv(65536).decode().strip()
                except Exception:
                    continue
                reply = self.handle(cmd)
                try:
                    conn.sendall(reply.encode())
                except Exception:
                    pass
        srv.close()

    def ask(self, kind: str, *args) -> str:
        """Queue an event and wait for the transcript it produces."""
        box: queue.Queue = queue.Queue()
        self.events.put((kind, *args, box))
        try:
            return box.get(timeout=60)
        except queue.Empty:
            return ""

    def handle(self, cmd: str) -> str:
        verb, _, arg = cmd.partition(" ")
        if verb == "start":
            self.events.put("down")
            return "ok"
        if verb == "stop":
            self.events.put("up")
            return "ok"
        if verb == "stop-return":
            # Stop and hand the transcript back on this socket instead of typing
            # it (omarchy-controller sends it to Iris). Empty reply = nothing heard.
            return self.ask("up-return")
        if verb == "toggle":
            self.events.put("up" if self.status() == "recording" else "down")
            return "ok"
        if verb == "status":
            return self.status()
        if verb == "transcribe":
            # Replies with the text instead of typing it, so tests and scripts
            # can check what was heard.
            return self.ask("file", arg.strip())
        if verb == "ping":
            return "pong"
        if verb == "quit":
            self.events.put("quit")
            return "ok"
        return f"unknown command: {verb}"


class Daemon:
    def __init__(self, cfg: cfgmod.Config) -> None:
        self.cfg = cfg
        self.events: queue.Queue = queue.Queue()
        self.state = "idle"
        if cfg.preroll_ms > 0:
            self.recorder = HotRecorder(cfg.sample_rate, cfg.max_seconds,
                                        cfg.audio_source, cfg.preroll_ms)
        else:
            self.recorder = Recorder(cfg.sample_rate, cfg.max_seconds,
                                     cfg.audio_source)
        self.muter = StreamMuter(cfg.mute_apps)
        self.model = None
        self.state_file = cfgmod.runtime_dir() / "state"
        self.levels = LevelServer(str(cfgmod.runtime_dir() / LEVELS_SOCKET))
        self.recorder.listener = self.levels.level

    def notify(self, summary: str, body: str = "", timeout: int = 2000) -> None:
        if self.cfg.notify:
            notify(summary, body, timeout)

    def set_state(self, state: str) -> None:
        self.state = state
        self.levels.send(state)
        try:
            self.state_file.write_text(state + "\n")
        except Exception:
            pass

    def load_model(self) -> None:
        import onnx_asr

        t0 = time.time()
        providers = None
        if self.cfg.device == "cuda":
            import onnxruntime as ort

            # CUDA and cuDNN come from pip wheels, not the system. Load them
            # before the first session so onnxruntime finds them.
            ort.preload_dlls()
            # Grow the memory pool only by what is asked for, not in doubling
            # steps: about 300 MB less VRAM held, same speed.
            providers = [
                ("CUDAExecutionProvider", {"arena_extend_strategy": "kSameAsRequested"}),
                "CPUExecutionProvider",
            ]

        kwargs = {}
        if self.cfg.quantization:
            kwargs["quantization"] = self.cfg.quantization
        if self.cfg.model_path:
            kwargs["path"] = os.path.expanduser(self.cfg.model_path)
        if providers:
            kwargs["providers"] = providers
        if self.cfg.device == "cpu" and self.cfg.threads:
            import onnxruntime as ort

            so = ort.SessionOptions()
            so.intra_op_num_threads = self.cfg.threads
            kwargs["sess_options"] = so

        self.model = onnx_asr.load_model(self.cfg.model, **kwargs)
        log.info("model loaded in %.2fs (%s)", time.time() - t0, self.cfg.device)
        if self.cfg.languages and "whisper" in self.cfg.model:
            languages.restrict(self.model, self.cfg.languages)
            log.info("languages: %s", ", ".join(self.cfg.languages))

        # Warm up so the first real press does not pay for lazy allocation.
        t0 = time.time()
        self.model.recognize(np.zeros(self.cfg.sample_rate, dtype=np.float32),
                             sample_rate=self.cfg.sample_rate)
        log.info("warmup %.2fs", time.time() - t0)

    def transcribe(self, samples: np.ndarray, rate: int) -> str:
        assert self.model is not None
        text = self.model.recognize(samples, sample_rate=rate)
        return (text or "").strip()

    def on_down(self) -> None:
        if self.state != "idle":
            log.debug("ignoring key down while %s", self.state)
            return
        self.set_state("recording")
        self.muter.mute()
        try:
            self.recorder.start()
        except Exception as exc:
            log.exception("could not start recording")
            self.muter.restore()
            self.set_state("idle")
            self.notify("Dictation error", str(exc))

    def on_up(self, box: queue.Queue | None = None) -> None:
        if self.state != "recording":
            log.debug("ignoring key up while %s", self.state)
            if box:
                box.put("")
            return
        self.set_state("transcribing")
        samples = self.recorder.stop()
        self.muter.restore()
        seconds = len(samples) / self.cfg.sample_rate
        if seconds < self.cfg.min_seconds:
            log.info("too short (%.2fs), ignored", seconds)
            self.set_state("idle")
            if box:
                box.put("")
            return
        self.finish(samples, self.cfg.sample_rate, box)

    def finish(self, samples: np.ndarray, rate: int,
               box: queue.Queue | None = None) -> None:
        """Transcribe and type the text, or put it in `box` for a caller
        waiting on the control socket (stop-return, transcribe)."""
        seconds = len(samples) / rate
        t0 = time.time()
        try:
            text = self.transcribe(samples, rate)
        except Exception as exc:
            log.exception("transcription failed")
            self.set_state("idle")
            self.notify("Dictation error", str(exc), timeout=4000)
            if box:
                box.put("")
            return
        peak = loudest_rms(samples)
        if peak < self.cfg.silence_rms and is_silence_phrase(text):
            log.info("silence phrase %r (peak rms %.4f), ignored", text, peak)
            text = ""
        text = tidy_short(text, self.cfg.short_words)
        elapsed = time.time() - t0
        log.info("%.1fs audio (peak rms %.4f) -> %.2fs infer (rtf %.3f): %r",
                 seconds, peak, elapsed, elapsed / max(seconds, 0.01), text)
        if not text:
            self.levels.send("nothing")
            self.set_state("idle")
            if box:
                box.put("")
            return
        self.levels.send(f"text {text}")
        if box:
            self.set_state("idle")
            box.put(text)
            return
        payload = text + (" " if self.cfg.trailing_space else "")
        # Typing a long result takes seconds (wtype, ~220 chars/s); the overlay
        # shows a progress bar for it.
        self.levels.send(f"typing {len(payload) if self.cfg.output == 'type' else 0}")
        method = out.deliver(payload, self.cfg.output, self.cfg.output_fallback)
        self.set_state("idle")
        if method == "clipboard" and self.cfg.output != "clipboard":
            self.notify("Dictation: on clipboard", "Could not type it, press ctrl+v", timeout=4000)

    def on_file(self, path: str, box: queue.Queue) -> None:
        """Transcribe a wav from disk. Used for testing the pipeline."""
        import wave

        try:
            with wave.open(path) as w:
                rate = w.getframerate()
                raw = w.readframes(w.getnframes())
        except (OSError, wave.Error) as exc:
            log.warning("cannot read %s: %s", path, exc)
            box.put("")
            return
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        self.set_state("transcribing")
        self.finish(samples, rate, box)

    def run(self) -> int:
        self.load_model()

        keycode = getattr(evdev.ecodes, self.cfg.key, None)
        if keycode is None:
            log.error("unknown key %s", self.cfg.key)
            return 2
        watcher = KeyWatcher(keycode, self.cfg.devices, self.events)
        if watcher.prepare() == 0:
            # Not fatal: the watcher keeps looking, so a keyboard plugged in
            # later still works, and start/stop over the socket work anyway.
            log.error("no readable keyboard exposes %s yet; is this user in the "
                      "input group or granted uaccess on /dev/input?",
                      self.cfg.key)
        watcher.start()
        control = ControlServer(str(cfgmod.runtime_dir() / SOCKET_NAME),
                                self.events, lambda: self.state)
        control.start()
        self.levels.start()

        if isinstance(self.recorder, HotRecorder):
            self.recorder.open()
            log.info("microphone held open for %d ms of pre-roll",
                     self.cfg.preroll_ms)

        self.set_state("idle")
        log.info("ready, hold %s to dictate", self.cfg.key)

        try:
            while True:
                event = self.events.get()
                if event == "quit":
                    break
                if event == "down":
                    self.on_down()
                elif event == "up":
                    self.on_up()
                elif isinstance(event, tuple) and event[0] == "up-return":
                    self.on_up(event[1])
                elif isinstance(event, tuple) and event[0] == "file":
                    self.on_file(event[1], event[2])
        except KeyboardInterrupt:
            pass
        finally:
            self.muter.restore()
            watcher.stop()
            control.stop()
            if isinstance(self.recorder, HotRecorder):
                self.recorder.close()
            try:
                self.state_file.unlink()
            except Exception:
                pass
        return 0


# What Whisper writes for a clip with nobody talking in it (from its subtitle
# training data). Dropped only when the clip is also quiet, so saying
# "thank you" out loud still works.
SILENCE_PHRASES = {
    "thank you", "thanks", "thank you very much", "thanks for watching",
    "thank you for watching", "thanks for watching and see you next time", "bye",
    "you", "köszönöm", "köszönöm szépen", "köszönöm a figyelmet",
}


def is_silence_phrase(text: str) -> bool:
    norm = text.lower().strip().strip(".,!?…").strip()
    return norm in SILENCE_PHRASES or norm.startswith(("feliratok", "feliratozta"))


def tidy_short(text: str, max_words: int) -> str:
    """A one-to-few word result without the sentence dressing Whisper adds."""
    words = text.split()
    if not words or len(words) > max_words:
        return text
    text = text.rstrip(".,!?;:… ")
    first = text.split()[0] if text else ""
    if first and not (len(first) > 1 and first.isupper()):
        text = first.lower() + text[len(first):]     # "YouTube" -> "youtube"
    return text


def loudest_rms(samples: np.ndarray, window: int = 480) -> float:
    """RMS of the loudest 30 ms (at 16 kHz) in the clip."""
    n = len(samples) // window * window
    if n == 0:
        return 0.0
    frames = samples[:n].reshape(-1, window)
    return float(np.sqrt((frames ** 2).mean(axis=1)).max())


def discover_keyboards(keycode: int) -> list[str]:
    found = []
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except Exception:
            continue
        keys = dev.capabilities().get(evdev.ecodes.EV_KEY, [])
        # A real keyboard, not a mouse that happens to expose a few keys.
        if keycode in keys and evdev.ecodes.KEY_A in keys:
            found.append(path)
        dev.close()
    return found


def _raise_interrupt(*_) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=logging.DEBUG if "--debug" in argv else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    # systemctl stop/restart sends SIGTERM. Turn it into the same clean exit
    # as ctrl+c so a stream muted mid-dictation gets unmuted.
    signal.signal(signal.SIGTERM, _raise_interrupt)
    cfg = cfgmod.load()
    if "--cuda" in argv:
        cfg.device = "cuda"
    if "--cpu" in argv:
        cfg.device = "cpu"
    if "--fp32" in argv:
        cfg.quantization = ""
    return Daemon(cfg).run()


if __name__ == "__main__":
    raise SystemExit(main())
