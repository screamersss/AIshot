#!/usr/bin/env python3
"""AiShot CLI — ИИ-помощник, решающий задачи по скриншоту (Linux/Wayland/niri).

Команды:
  aishot run ["вопрос"]          — выделить область (slurp), снять (grim), отправить в ЛЛМ, уведомить
  aishot file <путь> ["вопрос"]  — то же для уже готового файла скриншота
  aishot watch                   — демон: следит за папкой скриншотов, новые файлы кидает в ИИ
  aishot test                    — проверка окружения (grim/slurp/tesseract/LLM-сервер)
  aishot init                    — положить конфиг ~/.config/aishot/config.toml
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

# пакет можно запускать и без установки: python -m aishot / ./aishot.py
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aishot import pipeline  # noqa: E402
from aishot.config import default_config_path, ensure_dirs, expand, load_config  # noqa: E402
from aishot.logger import LOG, setup_logging  # noqa: E402
from aishot.screenshotter import ScreenshotCancelled, ToolMissing  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_CONFIG = REPO_ROOT / "config.toml"


def cmd_init(args: argparse.Namespace) -> int:
    dst = default_config_path()
    src = BUNDLED_CONFIG if BUNDLED_CONFIG.is_file() else None
    if dst.exists() and not args.force:
        print(f"Конфиг уже есть: {dst} (перезапись: aishot init --force)")
        return 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src:
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"✅ Конфиг создан: {dst}")
    else:
        print("⚠️ Шаблон config.toml не найден рядом с программой, конфиг не скопирован.")
        return 1
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    setup_logging(cfg)
    ensure_dirs(cfg)
    question = " ".join(args.question).strip() if args.question else ""
    if not question and not sys.stdin.isatty():
        question = sys.stdin.read().strip()
    try:
        answer = pipeline.run_once(cfg, question)
        print(answer)
        return 0
    except ScreenshotCancelled:
        return 0
    except (ToolMissing, RuntimeError) as e:
        LOG.error("%s", e)
        print(f"❌ {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        from aishot.llm import LlmError
        if isinstance(e, LlmError):
            LOG.error("%s", e)
            print(f"❌ {e}", file=sys.stderr)
            return 1
        LOG.exception("Непредвиденная ошибка")
        return 1


def cmd_file(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    setup_logging(cfg)
    ensure_dirs(cfg)
    path = expand(args.path)
    if not path.is_file():
        print(f"❌ Файл не найден: {path}", file=sys.stderr)
        return 1
    question = " ".join(args.question).strip() if args.question else ""
    try:
        answer = pipeline.process_image(cfg, path, question)
        print(answer)
        return 0
    except Exception as e:  # noqa: BLE001
        LOG.error("%s", e)
        print(f"❌ {e}", file=sys.stderr)
        return 1


def cmd_watch(args: argparse.Namespace) -> int:
    """Демон: раз в poll секунд смотрим новые файлы в папке скриншотов."""
    cfg = load_config(args.config)
    setup_logging(cfg)
    ensure_dirs(cfg)
    watch_dir = expand(cfg["general"]["screenshots_dir"])
    interval = float(cfg["general"].get("watch_interval", 2))
    LOG.info("Watch mode: слежу за %s каждые %.1fс (Ctrl+C — выход)", watch_dir, interval)

    seen: set[Path] = set(watch_dir.glob("*"))
    while True:
        try:
            time.sleep(interval)
            for f in sorted(watch_dir.iterdir()):
                if f in seen or not f.is_file():
                    continue
                if f.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                    seen.add(f)
                    continue
                seen.add(f)
                LOG.info("Обнаружен новый скриншот: %s", f.name)
                try:
                    pipeline.process_image(cfg, f, "")
                except Exception as e:  # noqa: BLE001
                    LOG.error("Ошибка обработки %s: %s", f, e)
        except KeyboardInterrupt:
            LOG.info("Watch остановлен")
            return 0


def cmd_test(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    setup_logging(cfg)
    import shutil

    ok = True
    for tool in ("grim", "slurp", "wl-copy", "notify-send", "tesseract"):
        found = shutil.which(tool)
        print(f"{'✅' if found else '⚠️ '} {tool:12s} {found or 'НЕ НАЙДЕН'}")
        if tool in ("grim", "slurp") and not found:
            ok = False

    base = str(cfg["llm"]["base_url"]).rstrip("/")
    import json
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(f"{base}/models", timeout=5) as r:
            data = json.loads(r.read())
            models = [m.get("name", "?") for m in data.get("data", [])]
            print(f"✅ LLM-сервер {base} отвечает. Модели: {', '.join(models) or '—'}")
            if cfg["llm"]["model"] not in models and models:
                print(f"⚠️ Модель '{cfg['llm']['model']}' не найдена среди загруженных/доступных")
    except Exception as e:  # noqa: BLE001
        print(f"❌ LLM-сервер {base} недоступен: {e}")
        ok = False

    noct = expand(cfg["notify"]["noctalia_socket"])
    has_cli = shutil.which("noctalia-shell") is not None
    if has_cli and noct.exists():
        print(f"✅ Noctalia     {noct} + CLI noctalia-shell — нативные OSD-уведомления")
    else:
        why = "нет CLI noctalia-shell в PATH" if not has_cli else f"нет сокета {noct}"
        print(f"ℹ️  Noctalia     {why} — auto использует notify-send (mako/dunst) или файл")
    print("\nИТОГ:", "ок" if ok else "есть проблемы — см. docs/troubleshooting.md")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aishot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-c", "--config", help="путь к config.toml (по умолчанию ~/.config/aishot/config.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="скриншот области -> ИИ -> уведомление")
    sp.add_argument("question", nargs="*", help="вопрос/задача (или stdin)")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("file", help="обработать готовый файл скриншота")
    sp.add_argument("path")
    sp.add_argument("question", nargs="*")
    sp.set_defaults(fn=cmd_file)

    sp = sub.add_parser("watch", help="демон: следить за папкой скриншотов")
    sp.set_defaults(fn=cmd_watch)

    sp = sub.add_parser("test", help="проверка окружения")
    sp.set_defaults(fn=cmd_test)

    sp = sub.add_parser("init", help="создать конфиг в ~/.config/aishot/")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(fn=cmd_init)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
