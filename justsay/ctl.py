"""justsayctl: talk to the running daemon."""

from __future__ import annotations

import socket
import sys

from . import config as cfgmod
from .daemon import SOCKET_NAME

USAGE = """usage: justsayctl <command>

  start        begin recording
  stop         stop recording, transcribe, paste
  stop-return  stop recording, transcribe, reply with the text instead of pasting
  toggle       start if idle, stop if recording
  status       print idle | recording | transcribing
  transcribe <file.wav>   run a wav through the pipeline
  ping         check the daemon is alive
  quit         shut the daemon down
"""


def send(message: str, timeout: float = 120.0) -> str:
    path = str(cfgmod.runtime_dir() / SOCKET_NAME)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(path)
    s.sendall(message.encode())
    s.shutdown(socket.SHUT_WR)
    chunks = []
    while True:
        data = s.recv(4096)
        if not data:
            break
        chunks.append(data)
    s.close()
    return b"".join(chunks).decode()


def main() -> int:
    if len(sys.argv) < 2:
        print(USAGE, end="")
        return 1
    try:
        print(send(" ".join(sys.argv[1:])))
    except FileNotFoundError:
        print("justsay daemon is not running", file=sys.stderr)
        return 1
    except ConnectionRefusedError:
        print("stale socket, daemon is not running", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
