# 📸 AiShot — ИИ-помощник, решающий задачи через скриншот

**Linux (Wayland) + niri + Noctalia Shell.** Выделяешь область экрана → `grim` делает снимок →
локальная нейросеть смотрит на картинку и читает контекст → решение приходит **уведомлением**.
Всё офлайн, никаких облаков.

```
slurp (выбор области) → grim (PNG в папку из конфига) → OCR (tesseract) + vision-LLM (ollama)
        → ответ → notify-send / Noctalia / .answer.txt   +   лог каждого действия
```

## Быстрый старт

```bash
# 1. Системные зависимости (Arch):
sudo pacman -S grim slurp wl-clipboard tesseract tesseract-data-rus tesseract-data-eng libnotify
#    (Fedora: dnf install grim slurp wl-clipboard tesseract tesseract-langpack-rus libnotify)

# 2. Локальная vision-модель (самый простой путь — ollama):
ollama pull qwen2.5vl:7b   # или llama3.2-vision, minicpm-v ...
ollama serve &

# 3. Установка самого AiShot (зависимости от Python-пакетов НЕТ, нужен Python >= 3.11):
pip install .              # даст команду `aishot`
#   либо без установки:    python aishot.py ...

# 4. Конфиг:
aishot init                # копирует config.toml в ~/.config/aishot/
nano ~/.config/aishot/config.toml

# 5. Проверка окружения и запуск:
aishot test
aishot run "Объясни эту ошибку и реши её"     # выделишь область мышью
echo "переведи на английский" | aishot run    # вопрос можно из stdin
aishot file ~/Pictures/shot.png "что тут?"    # готовый файл вместо скриншота
aishot watch                                  # демон: сам обрабатывает новые скриншоты в папке
```

## Хоткей под niri (`~/.config/niri/config.kdl`)

```kdl
Key {
    Super+Shift+A { Spawn { command "aishot run"; } }   // без вопроса — дефолтный промт из конфига
}
```

## Настройки — все в `config.toml`

| Секция        | Что настраивать |
|---|---|
| `[general]`   | папка скриншотов, язык ответа, шаблон имени файла |
| `[slurp]`     | цвет/рамка/размеры оверлея выделения, формат геометрии |
| `[grim]`      | scale для HiDPI/4K, png/jpeg, качество |
| `[clipboard]` | копировать ли снимок в буфер (wl-copy) |
| `[ocr]`       | языки tesseract (`rus+eng`), psm, вкл/выкл |
| `[llm]`       | адрес сервера (ollama/LM Studio/llama.cpp/vLLM), модель, `vision`, temp, таймаут |
| `[prompt]`    | **главный промт-задача** с плейсхолдерами `{text}` и `{question}` |
| `[network]`   | proxy/no_proxy до LLM-сервера |
| `[notify]`    | способ: `auto` → Noctalia IPC → notify-send → файл; длина тела |
| `[logging]`   | путь лога, уровень, вывод в консоль |

Логи действий: `~/.local/state/aishot/aishot.log`. Ответ всегда дублируется в `<скриншот>.answer.txt`.

## Архитектура

```
aishot/
├── cli.py           # команды: run / file / watch / test / init
├── config.py        # единый config.toml + дефолты (tomllib, stdlib-only)
├── screenshotter.py # slurp+grim обвязка (отмена Esc, HiDPI, clipboard)
├── ocr.py           # tesseract (запасной канал контекста)
├── llm.py           # OpenAI-совместимый клиент (картинка base64 + промт)
├── pipeline.py      # скриншот → контекст → промт → ИИ → уведомление
├── notifier.py      # Noctalia IPC / notify-send / файл
└── logger.py        # лог действий
docs/                # служебные заметки (AIShot.md, slurp_grim.md, troubleshooting.md...)
```

## Troubleshooting

См. `docs/troubleshooting.md` и `aishot test`. Частое:
- **HiDPI/4K**: координаты slurp логические, пиксели физические → поставь `[grim] scale = 2`.
- **Noctalia не показывает уведомления**: метод `auto` сам падает на `notify-send` (mako/dunst работают под niri).
- **Модель не видит картинку**: нужна vision-модель (`qwen2.5vl`, `llama3.2-vision`); текстовую модель переключи `vision = false` — тогда уйдёт только OCR-текст.
