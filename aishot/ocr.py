"""OCR через tesseract: извлечение текста со скриншота (запасной канал, если модель без vision)."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from .logger import LOG


class OcrUnavailable(Exception):
    pass


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


def ocr_image(cfg: dict, img_path: Path) -> str:
    """Распознаёт текст с картинки. Пустая строка — если текста нет или OCR отключён/недоступен."""
    oc = cfg["ocr"]
    if not oc.get("enabled", True):
        LOG.info("OCR отключён в конфиге")
        return ""
    if not tesseract_available():
        LOG.warning("tesseract не установлен — OCR пропущен (vision-модель всё равно увидит картинку)")
        return ""

    langs = str(oc.get("languages", "rus+eng"))
    psm = int(oc.get("psm", 3))
    cmd = ["tesseract", str(img_path), "stdout", "-l", langs, "--psm", str(psm)]
    LOG.debug("OCR команда: %s", " ".join(cmd))
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        LOG.error("OCR: таймаут 120с")
        return ""
    if res.returncode != 0:
        LOG.error("OCR завершился с кодом %s: %s", res.returncode, res.stderr.strip())
        return ""
    text = res.stdout.strip()
    LOG.info("OCR: распознано %d символов", len(text))
    return text
