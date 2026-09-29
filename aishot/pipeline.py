"""Пайплайн AiShot: скриншот -> контекст (vision/OCR) -> промт из конфига -> локальная LLM -> уведомление.

Каждый шаг дублируется: в терминал (Ui: спиннер + ✔/✘ + время) и в лог-файл (LOG).
"""
from __future__ import annotations

import time
from pathlib import Path

from . import llm, notifier, ocr, screenshotter
from .logger import LOG
from .ui import Ui


def build_prompt(cfg: dict, text: str, question: str) -> str:
    tpl = str(cfg["prompt"]["task"])
    return (
        tpl.replace("{text}", text or "(текст не распознан — смотри картинку)")
        .replace("{question}", question or str(cfg["prompt"]["default_question"]))
    )


def process_image(cfg: dict, img_path: Path, question: str, ui: Ui | None = None) -> str:
    """Основная работа с готовым файлом скриншота. Возвращает ответ ИИ."""
    own_ui = ui is None
    if own_ui:
        ui = Ui(cfg)
    t0 = time.monotonic()
    LOG.info("═══ Обработка: %s ═══", img_path)
    ui.info(f"Файл: {img_path.name}")

    img_bytes = img_path.read_bytes()

    # 1. Контекст: OCR-текст всегда полезен (и vision-модели как подсказка),
    #    при vision=false — единственный канал.
    ui.start("Распознаю текст со скриншота (OCR)...")
    try:
        text = ocr.ocr_image(cfg, img_path)
        ui.step(f"{len(text)} симв." if text else "текст не найден")
    except Exception as e:  # noqa: BLE001
        ui.step("пропущен")
        LOG.warning("OCR недоступен: %s", e)
    if not cfg["llm"].get("vision", True) and not text:
        ui.fail("пустой OCR + vision выключен")
        raise RuntimeError(
            "OCR вернул пустой текст, а vision-модель выключена — отправлять нечего. "
            "Включи [llm] vision=true или проверь tesseract ([ocr])."
        )

    # 2. Промт из конфига
    prompt = build_prompt(cfg, text, question)
    LOG.info("Промт собран (%d симв.), вопрос: %r", len(prompt), question[:80])

    # 3. Локальная нейросеть
    model = cfg["llm"]["model"]
    mode = "картинка+текст (vision)" if cfg["llm"].get("vision", True) else "только текст (OCR)"
    ui.start(f"Спрашиваю модель «{model}» [{mode}]...")
    try:
        answer = llm.ask(cfg, prompt, img_bytes if cfg["llm"].get("vision", True) else None)
        ui.step(f"ответ {len(answer)} симв.")
    except Exception as e:  # noqa: BLE001
        ui.fail(str(e).split("\n")[0][:100])
        raise

    # 4. Уведомление + сохранение ответа рядом со скриншотом
    ui.start("Отправляю уведомление...")
    try:
        notifier.send(cfg, "Ответ AiShot", answer, related_file=img_path)
        ui.step("готово")
    except Exception as e:  # noqa: BLE001
        ui.step(f"⚠ {e}")
        LOG.warning("Уведомление не доставлено: %s", e)

    LOG.info("═══ Готово за %.1fс ═══", time.monotonic() - t0)
    if own_ui:
        ui.finish()
    return answer


def run_once(cfg: dict, question: str, file: Path | None = None, ui: Ui | None = None) -> str:
    """Один цикл: (файл | slurp+grim) -> ИИ -> уведомление."""
    if ui is None:
        ui = Ui(cfg)
    if file is not None:
        shot = screenshotter.Shot(path=file, img_bytes=file.read_bytes(), geometry="file")
        LOG.info("Внешний файл вместо скриншота: %s", file)
    else:
        ui.start("Выдели область на экране (slurp → grim)...")
        try:
            shot = screenshotter.capture(cfg)
        except screenshotter.ScreenshotCancelled:
            ui.fail("отменено (Esc)")
            LOG.info("Пользователь отменил выбор области")
            ui.finish()
            raise
        ui.step(f"{shot.path.name}, {shot.geometry}")
        screenshotter.copy_to_clipboard(cfg, shot)
    result = process_image(cfg, shot.path, question, ui=ui)
    ui.finish()
    return result
