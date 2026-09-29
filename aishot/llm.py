"""Клиент локальной нейросети (OpenAI-совместимый API: ollama / llama.cpp / LM Studio / vLLM).

vision=true  -> отправляем картинку как base64 data URL (chat completions с image_url)
vision=false -> отправляем только OCR-текст
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from .logger import LOG


class LlmError(Exception):
    pass


def _post(url: str, payload: dict, headers: dict, timeout: int, proxy: str = "", no_proxy: str = "") -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    handlers: list[urllib.request.BaseHandler] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy, "no": no_proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        raise LlmError(f"HTTP {e.code} от {url}: {body}") from e
    except urllib.error.URLError as e:
        raise LlmError(
            f"Не удалось связаться с {url} ({e.reason}). "
            "Проверь, что локальный сервер запущен (например `ollama serve`) и модель загружена."
        ) from e


def ask(cfg: dict, prompt: str, img_bytes: bytes | None = None) -> str:
    llm = cfg["llm"]
    base = str(llm["base_url"]).rstrip("/")
    url = f"{base}/chat/completions"
    timeout = int(llm.get("timeout_sec", 300))
    headers = {"Authorization": f"Bearer {llm.get('api_key', '')}"}

    proxy = str(cfg.get("network", {}).get("proxy", "") or "")
    no_proxy = str(cfg.get("network", {}).get("no_proxy", "") or "")

    messages: list[dict] = []
    lang = cfg["general"].get("language")
    if lang:
        messages.append({"role": "system", "content": f"Отвечай на языке '{lang}' если пользователь не просит иначе."})

    if llm.get("vision", True) and img_bytes:
        b64 = base64.b64encode(img_bytes).decode()
        fmt = str(cfg["grim"].get("output_format", "png")).lower().replace("jpeg", "jpg")
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/{fmt};base64,{b64}"}},
        ]
        messages.append({"role": "user", "content": content})
        LOG.info("LLM: отправляю картинку + промт (%d КБ) модели %s", len(img_bytes) // 1024, llm["model"])
    else:
        messages.append({"role": "user", "content": prompt})
        LOG.info("LLM: отправляю текстовый промт (%d симв.) модели %s", len(prompt), llm["model"])

    payload = {
        "model": llm["model"],
        "messages": messages,
        "temperature": float(llm.get("temperature", 0.3)),
        "max_tokens": int(llm.get("max_tokens", 2048)),
        "stream": False,
    }

    data = _post(url, payload, headers, timeout, proxy, no_proxy)
    try:
        answer = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LlmError(f"Неожиданный ответ от {url}: {json.dumps(data)[:500]}") from e
    LOG.info("LLM: получен ответ (%d симв.)", len(answer))
    return answer.strip()
