#!/usr/bin/env python3
"""Точка входа без установки: ./aishot.py run "реши задачу со скриншота\""""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aishot.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
