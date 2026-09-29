"""Красивые сообщения в терминале + пошаговый лог действий (шаги 1..N).

Использование:
    ui = Ui(cfg)            # создаёт "спиннер"-строку и печатает заголовок шага
    ui.step("Скриншот снят")  # завершает текущий шаг галочкой, готовит следующий
    ui.fail("нет grim")       # завершает текущий шаг крестиком
    ui.finish()               # убирает спиннер в конце
"""
from __future__ import annotations

import os
import sys
import threading
import time

from .logger import LOG

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _COLOR else s


GREEN = lambda s: _c("32", s)   # noqa: E731
RED = lambda s: _c("31", s)     # noqa: E731
DIM = lambda s: _c("2", s)      # noqa: E731
BOLD = lambda s: _c("1", s)     # noqa: E731


class Ui:
    """Терминальный прогресс: одна живая строка со спиннером + нумерованные шаги."""

    SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, cfg: dict | None = None, total_steps: int = 4):
        self.enabled = sys.stdout.isatty()
        self.total = total_steps
        self.step_no = 0
        self._label = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_len = 0
        self._t0 = time.monotonic()

    # ── внутреннее ────────────────────────────────────────────────
    def _erase(self) -> None:
        if self.enabled and self._last_len:
            sys.stdout.write("\r" + " " * self._last_len + "\r")
            sys.stdout.flush()
            self._last_len = 0

    def _spin_loop(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self.SPIN[i % len(self.SPIN)]
            text = f"  {DIM(frame + ' ')}{self._label}"
            pad = max(0, self._last_len - len(text))
            sys.stdout.write("\r" + text + " " * pad)
            sys.stdout.flush()
            self._last_len = len(text)
            i += 1
            time.sleep(0.08)

    # ── публичное API ─────────────────────────────────────────────
    def start(self, label: str) -> None:
        """Начать новый шаг (или запустить спиннер под уже начатый)."""
        self.step_no += 1
        prefix = BOLD(f"[{self.step_no}/{self.total}] ")
        self._label = f"{prefix}{label}"
        LOG.info("ШАГ %d/%d: %s", self.step_no, self.total, label)
        if self.enabled:
            self._stop.clear()
            self._thread = threading.Thread(target=self._spin_loop, daemon=True)
            self._thread.start()

    def step(self, done_label: str = "") -> None:
        """Завершить текущий шаг успешно; опционально — подпись результата."""
        self._finish_current(GREEN("✔"), done_label)

    def fail(self, reason: str = "") -> None:
        """Завершить текущий шаг ошибкой."""
        self._finish_current(RED("✘"), reason)

    def info(self, msg: str) -> None:
        """Просто строка в терминал + в лог, не ломая спиннер."""
        self._erase()
        print(f"    {DIM('›')} {msg}")
        LOG.info("%s", msg)

    def finish(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
        self._erase()

    # ── служебное ─────────────────────────────────────────────────
    def _finish_current(self, mark: str, detail: str = "") -> None:
        elapsed = time.monotonic() - self._t0
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)
            self._thread = None
        self._erase()
        line = f"  {mark} {self._label}"
        if detail:
            line += f" {DIM(detail)}"
        line += DIM(f"  ({elapsed:.1f}с)")
        print(line)
        sys.stdout.flush()
        self._t0 = time.monotonic()
        self._label = ""
