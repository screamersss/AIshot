"""Загрузка и валидация конфигурации AiShot (единый config.toml)."""
from __future__ import annotations

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

APP_NAME = "aishot"

DEFAULT_CONFIG: dict[str, Any] = {
    "general": {
        "screenshots_dir": "~/Pictures/AiShot",
        "language": "ru",
        "filename_template": "shot_{time:%Y-%m-%d_%H-%M-%S}.png",
    },
    "slurp": {
        "selection_color": "b4befe40",
        "border_color": "cba6f7",
        "border_weight": 2,
        "show_dimensions": True,
        "format": "%x,%y %wx%h",
    },
    "grim": {
        "scale": 1,
        "output_format": "png",
        "quality": 90,
    },
    "clipboard": {"enabled": True},
    "ocr": {"enabled": True, "languages": "rus+eng", "psm": 3},
    "llm": {
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "ollama",
        "model": "qwen2.5vl:7b",
        "vision": True,
        "temperature": 0.3,
        "max_tokens": 2048,
        "timeout_sec": 300,
    },
    "prompt": {
        "task": (
            "Ты — системный ИИ-ассистент AiShot. Пользователь прислал скриншот.\n\n"
            'Контекст со скриншота (распознанный текст):\n"""{text}"""\n\n'
            "Задача пользователя: {question}\n\n"
            "Ответь коротко, по делу и на языке пользователя."
        ),
        "default_question": (
            "Проанализируй контекст скриншота и предложи, какую задачу тут можно решить. Реши её."
        ),
    },
    "network": {"proxy": "", "no_proxy": "127.0.0.1,localhost"},
    "notify": {
        "enabled": True,
        "method": "auto",
        "desktop_command": "notify-send 'AiShot' '%body%' -u normal -t 15000",
        "noctalia_socket": "~/.noctalia/sock",
        "max_body_chars": 1500,
    },
    "logging": {
        "file": "~/.local/state/aishot/aishot.log",
        "level": "INFO",
        "console": True,
    },
    "hotkey": {"note": "Super+Shift+A -> aishot run"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def default_config_path() -> Path:
    """~/.config/aishot/config.toml (XDG)."""
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return Path(xdg) / APP_NAME / "config.toml"


def load_config(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Грузит конфиг: переданный путь -> $AISHOT_CONFIG -> ~/.config/aishot/config.toml
    -> ./config.toml рядом с запуском. Недостающие секции добираются дефолтами."""
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    env = os.environ.get("AISHOT_CONFIG")
    if env:
        candidates.append(Path(env))
    candidates.append(default_config_path())
    candidates.append(Path.cwd() / "config.toml")

    cfg_file = next((p for p in candidates if p.is_file()), None)
    user_cfg: dict[str, Any] = {}
    if cfg_file is not None:
        with open(cfg_file, "rb") as f:
            user_cfg = tomllib.load(f)

    cfg = _deep_merge(DEFAULT_CONFIG, user_cfg)
    cfg["_config_file"] = str(cfg_file) if cfg_file else "<встроенные дефолты>"
    return cfg


def expand(p: str | os.PathLike) -> Path:
    return Path(os.path.expanduser(str(p)))


def ensure_dirs(cfg: dict[str, Any]) -> None:
    expand(cfg["general"]["screenshots_dir"]).mkdir(parents=True, exist_ok=True)
    expand(cfg["logging"]["file"]).parent.mkdir(parents=True, exist_ok=True)
