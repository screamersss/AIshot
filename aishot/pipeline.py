"""Пайплайн AiShot: скриншот -> контекст (vision/OCR) -> промт из конфига -> локальная LLM -> уведомление."""
from __future__ import annotations

import time
from pathlib import Path

from . import llm, notifier, ocr, screenshotter
from .logger import LOG


def build_prompt(cfg: dict, text: str, question: str) -> str:
    tpl = str(cfg["prompt"]["task"])
    return (
        tpl.replace("{text}", text or "(текст не распознан — смотри картинку)")
        .replace("{question}", question or str(cfg["prompt"]["default_question"]))
    )


def process_image(cfg: dict, img_path: Path, question: str) -> str:
    """Основная работа с готовым файлом скриншота. Возвращает ответ ИИ."""
    t0 = time.monotonic()
    LOG.info("═══ Обработка: %s ═══", img_path)

    img_bytes = img_path.read_bytes()

    # 1. Контекст: OCR-текст всегда полезен (и vision-модели как подсказка),
    #    при vision=false — единственный канал.
    text = ocr.ocr_image(cfg, img_path)
    if not cfg["llm"].get("vision", True) and not text:
        raise RuntimeError(
            "OCR вернул пустой текст, а vision-модель выключена — отправлять нечего. "
            "Включи [llm] vision=true или проверь tesseract ([ocr])."
        )

    # 2. Промт из конфига
    prompt = build_prompt(cfg, text, question)

    # 3. Локальная нейросеть
    answer = llm.ask(cfg, prompt, img_bytes if cfg["llm"].get("vision", True) else None)

    # 4. Уведомление + сохранение ответа рядом со скриншотом
    notifier.send(cfg, "Ответ AiShot", answer, related_file=img_path)

    LOG.info("═══ Готово за %.1fс ═══", time.monotonic() - t0)
    return answer


def run_once(cfg: dict, question: str, file: Path | None = None) -> str:
    """Один цикл: (файл | slurp+grim) -> ИИ -> уведомление."""
    if file is not None:
        shot = screenshotter.Shot(path=file, img_bytes=file.read_bytes(), geometry="file")
        LOG.info("Внешний файл вместо скриншота: %s", file)
    else:
        try:
            shot = screenshotter.capture(cfg)
        except screenshotter.ScreenshotCancelled:
            LOG.info("Пользователь отменил выбор области")
            raise
        screenshotter.copy_to_clipboard(cfg, shot)
    return process_image(cfg, shot.path, question)
