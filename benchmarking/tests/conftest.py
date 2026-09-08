"""Keep standalone benchmark scripts importable without importing the application."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for name in ("download", "server", "evaluation"):
    sys.path.insert(0, str(ROOT / name))
