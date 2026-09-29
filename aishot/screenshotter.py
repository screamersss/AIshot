"""Скриншот области через slurp + grim (Wayland / niri).

Картинка получается из stdout grim прямо в память (без временных файлов),
затем сохраняется в папку из конфига. Отмена по Esc обрабатывается корректно.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import expand
from .logger import LOG


class ScreenshotCancelled(Exception):
    """Пользователь нажал Esc в slurp."""


class ToolMissing(Exception):
    """grim/slurp/wl-copy не установлены."""


@dataclass
class Shot:
    path: Path
    img_bytes: bytes
    geometry: str


_HEX_RE = re.compile(r"^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$")


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise ToolMissing(
            f"Утилита '{tool}' не найдена в PATH. Установи её (см. README, раздел Зависимости)."
        )


def build_slurp_cmd(cfg: dict) -> list[str]:
    s = cfg["slurp"]
    cmd = ["slurp"]
    color = str(s.get("selection_color", "b4befe40"))
    if not _HEX_RE.match(color):
        LOG.warning("Некорректный selection_color '%s', беру дефолт", color)
        color = "b4befe40"
    cmd += ["-c", f"#{color}"]                # цвет маски выделения (RRGGBBAA, альфа = сила затемнения)
    border = str(s.get("border_color", "cba6f7"))
    if _HEX_RE.match(border):
        cmd += ["-b", f"#{border[:6]}"]        # slurp-wlroots хочет RRGGBB без альфы
    cmd += ["-w", str(int(s.get("border_weight", 2)))]
    if s.get("show_dimensions", True):
        cmd.append("-d")
    cmd += ["-f", str(s.get("format", "%x,%y %wx%h"))]
    return cmd


def build_grim_cmd(cfg: dict, geometry: str, out_path: Path) -> list[str]:
    g = cfg["grim"]
    cmd = ["grim"]
    scale = float(g.get("scale", 1))
    if scale != 1:
        cmd += ["-s", str(scale)]
    fmt = str(g.get("output_format", "png")).lower()
    if fmt in ("jpg", "jpeg"):
        cmd += ["-t", "jpeg", "-q", str(int(g.get("quality", 90)))]
    cmd += ["-g", geometry, str(out_path)]
    return cmd


def select_region(cfg: dict) -> str:
    """Выбор области.

    Порядок:
      1. Если overlay_mode = v2 и доступны pywayland+Pillow — собственный
         оверлей «прожектор»: фон затемнён (настраивается), ВНУТРИ рамки
         картинка остаётся яркой и полностью видимой.
      2. Иначе — обычный slurp с ПРОЗРАЧНОЙ маской (#RRGGBBAA00): рамка и
         размеры видны, но экран под ними не закрашивается вообще.
    Возвращает геометрию 'X,Y WxH'. Бросает ScreenshotCancelled на Esc.
    """
    mode = str(cfg["slurp"].get("overlay_mode", "v2")).lower()
    if mode in ("v2", "2", "overlay"):
        try:
            from . import overlay
            if overlay.available(cfg):
                return overlay.select_region_overlay(cfg)
            LOG.debug("Оверлей v2 недоступен (нет pywayland/Pillow) — беру slurp")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Оверлей v2 не запустился (%s) — перехожу на slurp", exc)
    _require("slurp")
    _require("grim")
    res = subprocess.run(build_slurp_cmd(cfg), capture_output=True, text=True)
    if res.returncode == 1:
        raise ScreenshotCancelled("Выбор отменён (Esc)")
    if res.returncode != 0:
        raise RuntimeError(f"slurp завершился с кодом {res.returncode}: {res.stderr.strip()}")
    geometry = res.stdout.strip()
    if not geometry:
        raise ScreenshotCancelled("Пустой ответ slurp")
    LOG.debug("Выбрана область: %s", geometry)
    return geometry


def capture(cfg: dict, geometry: str | None = None) -> Shot:
    """slurp (если нужно) -> grim -> файл в screenshots_dir. Возвращает Shot."""
    if geometry is None:
        geometry = select_region(cfg)

    general = cfg["general"]
    fmt = str(cfg["grim"].get("output_format", "png")).lower()
    if fmt in ("jpg", "jpeg"):
        fmt = "jpg"
    template = str(general.get("filename_template", "shot_{time:%Y-%m-%d_%H-%M-%S}.png"))
    name = template.replace("{ext}", fmt)
    if not name.endswith("." + fmt):
        name = re.sub(r"\.(png|jpe?g)$", "", name, flags=re.I) + f".{fmt}"
    filename = dt.datetime.now().strftime(name)

    out_dir = expand(general["screenshots_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / filename

    cmd = build_grim_cmd(cfg, geometry, out_path)
    LOG.debug("Команда захвата: %s", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0 or not out_path.is_file():
        raise RuntimeError(f"grim завершился с кодом {res.returncode}: {res.stderr.strip()}")

    data = out_path.read_bytes()
    LOG.info("Скриншот сохранён: %s (%d КБ)", out_path, len(data) // 1024)
    return Shot(path=out_path, img_bytes=data, geometry=geometry)


def copy_to_clipboard(cfg: dict, shot: Shot) -> None:
    if not cfg["clipboard"].get("enabled", True):
        return
    if shutil.which("wl-copy") is None:
        LOG.warning("wl-clipboard не установлен — копирование в буфер пропущено")
        return
    fmt = shot.path.suffix.lstrip(".").replace("jpg", "jpeg")
    try:
        subprocess.run(
            ["wl-copy", "--type", f"image/{fmt}"], input=shot.img_bytes, check=True
        )
        LOG.info("Скриншот скопирован в буфер обмена (wl-copy)")
    except subprocess.CalledProcessError as e:
        LOG.warning("Не удалось скопировать в буфер: %s", e)
