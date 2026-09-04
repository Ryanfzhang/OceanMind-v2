"""Frozen entry point for the Ocean Research Partner Desktop sidecar."""

from __future__ import annotations

import sys


if __name__ == "__main__" and len(sys.argv) > 1 and sys.argv[1] == "sandbox-probe":
    from ocean_partner.sandbox_probe_entry import main

    raise SystemExit(main(sys.argv[2:]))

from ocean_partner.cli import app


if __name__ == "__main__":
    app()
