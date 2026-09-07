"""Frozen entry point for the Ocean Research Partner Desktop sidecar."""

from __future__ import annotations

import sys


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "sandbox-probe":
    from oceanx.sandbox_probe_entry import main

    raise SystemExit(main(sys.argv[2:]))

from oceanx.cli import app


if __name__ == "__main__":
    app()
