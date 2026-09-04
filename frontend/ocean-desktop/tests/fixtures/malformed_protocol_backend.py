"""A deliberately malformed Desktop protocol producer for main-process boundary tests."""

from __future__ import annotations

import argparse
import signal
import threading


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--client-kind", required=True)
    parser.parse_args()
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    print('OHJSON:{"type":"system.ready","type":"request.completed"}', flush=True)
    stopped.wait(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
