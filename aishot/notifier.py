"""Уведомления: desktop (notify-send / dunst / mako под niri), Noctalia IPC (эксперимент) или файл."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from .config import expand
from .logger import LOG


def _sanitize(text: str) -> str:
    """Для shell-команды: без кавычек/обратных ковычек/доллара/бэктиков."""
    return text.replace('"', "'").replace("`", "'").replace("$", "\\$").replace("\\n", " ")


def notify_desktop(cfg: dict, title: str, body: str) -> bool:
    n = cfg["notify"]
    cmd_tpl = str(n.get("desktop_command", ""))
    if not cmd_tpl:
        return False
    # безопасная подстановка: экранируем тело и заголовок
    safe_body = _sanitize(body)
    safe_title = _sanitize(title)
    cmd = cmd_tpl.replace("%body%", safe_body).replace("%title%", safe_title)
    if shutil.which(cmd.split()[0]) is None:
        LOG.warning("Утилита уведомлений '%s' не найдена", cmd.split()[0])
        return False
    try:
        subprocess.run(cmd, shell=True, check=False)
        LOG.info("Отправлено desktop-уведомление")
        return True
    except Exception as e:  # noqa: BLE001
        LOG.warning("notify-send упал: %s", e)
        return False


def notify_noctalia(cfg: dict, title: str, body: str) -> bool:
    """Экспериментально: Noctalia Shell слушает JSON-события на своём IPC-сокете/FIFO.

    Документированный интерфейс ночталии со временем меняется — если не сработало,
    переключись на method = "desktop" (mako/dunst видят уведомления и под niri).
    """
    sock_path = expand(cfg["notify"].get("noctalia_socket", "~/.noctalia-shell/ipc"))
    if not sock_path.exists():
        LOG.warning("Noctalia IPC не найден по пути %s", sock_path)
        return False
    event = {
        "command": "notification.add",
        "app": "aishot",
        "title": title,
        "body": body,
        "urgency": "normal",
    }
    try:
        with open(sock_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        LOG.info("Событие записано в Noctalia IPC")
        return True
    except Exception as e:  # noqa: BLE001
        LOG.warning("Noctalia IPC запись не удалась: %s", e)
        return False


def notify_file(cfg: dict, title: str, body: str, out_path: Path) -> bool:
    p = out_path.with_suffix(".answer.txt")
    p.write_text(f"{title}\n{'=' * 40}\n{body}\n", encoding="utf-8")
    LOG.info("Ответ сохранён в %s", p)
    return True


def send(cfg: dict, title: str, body: str, related_file: Path | None = None) -> None:
    if not cfg["notify"].get("enabled", True):
        LOG.info("Уведомления отключены в конфиге")
        return
    max_chars = int(cfg["notify"].get("max_body_chars", 0) or 0)
    text = body if max_chars <= 0 else body[:max_chars] + ("…" if len(body) > max_chars else "")

    method = str(cfg["notify"].get("method", "auto")).lower()
    sent = False
    if method in ("auto", "noctalia"):
        sent = notify_noctalia(cfg, title, text)
    if not sent and method in ("auto", "desktop"):
        sent = notify_desktop(cfg, title, text)
    if not sent and related_file is not None:
        notify_file(cfg, title, text, related_file)
    elif not sent:
        LOG.error("Ни один способ уведомления не сработал")
