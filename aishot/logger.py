"""Логирование: файл (настраиваемый путь/уровень) + консоль.

Формат короткий, чтобы не конфликтовать с пошаговым выводом Ui:
    2026-09-29 14:05:12 | INFO    | ШАГ 2/4: OCR...
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from .config import expand

LOG = logging.getLogger("aishot")
_configured = False


class _PlainFormatter(logging.Formatter):
    """Без имени логгера — чище выглядит в файле и консоли."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(cfg: dict) -> None:
    global _configured
    if _configured:
        return
    lc = cfg["logging"]
    level = getattr(logging, str(lc.get("level", "INFO")).upper(), logging.INFO)
    fmt = _PlainFormatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")

    log_file = expand(lc["file"])
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(fh)

    if lc.get("console", True):
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    _configured = True
    LOG.info("Лог: %s (уровень %s)", log_file, logging.getLevelName(level))


def get_logger(name: str = "aishot") -> logging.Logger:
    return logging.getLogger(name)
