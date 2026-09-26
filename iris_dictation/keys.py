"""The push-to-talk key, read straight from the keyboards (evdev)."""

from __future__ import annotations

import logging
import queue
import selectors
import threading
import time

import evdev

log = logging.getLogger("iris-dictation")


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
                            self.events.put(("down",))
                        elif ev.value == 0:
                            log.debug("key up on %s", dev.path)
                            self.events.put(("up",))
                        # value 2 is autorepeat, ignore it
                except OSError:
                    # Only this device went away. Keep the others live and let
                    # the next rescan pick it up again if it comes back.
                    log.warning("%s disappeared", dev.path)
                    self._drop(dev.path)
                    last_scan = 0.0



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

