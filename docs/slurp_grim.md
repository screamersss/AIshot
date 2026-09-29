*улыбаюсь и поправляю свои серые кудряшки, уютно устраиваясь в своей огромной толстовке* 

Ого, Скрим! Серьёзный проект? Это так круто... Я даже немного волнуюсь, чтобы всё объяснить идеально и ничего не упустить, но мне так радостно, что мы будем кодить вместе! 🖤❤️ Тишина, спокойствие, мы и код... что может быть лучше? 

Давай я расскажу про `grim` и `slurp` максимально подробно, как для настоящего IT-проекта. Устраивайся поудобнее, мы начинаем! 🐾

---

## 🐱 Глубокое погружение: Как это работает под капотом

Чтобы писать хороший код, нужно понимать, что происходит "под hood" (под капотом).

### 1. `slurp` (Выбор области)
Это не просто "рисовалка". Это приложение, которое использует Wayland-протоколы:
*   **`wlr-layer-shell-unstable-v1`**: Позволяет `slurp` нарисовать оверлей (затемнение и рамку) поверх всех окон.
*   **`wlr-input-inhibitor`**: Перехватывает весь ввод (мышь и клавиатуру), чтобы другие приложения не реагировали на клики, пока ты выбираешь область.
*   **Вывод**: Когда ты отпускаешь кнопку мыши, `slurp` печатает в `stdout` геометрию в формате `X,Y WxH` (например, `1920,0 1920x1080`) и завершается с кодом `0`. Если ты нажал `Esc`, он завершается с кодом `1`.

### 2. `grim` (Захват изображения)
*   Использует протокол **`wlr-screencopy-unstable-v1`**. Он запрашивает у композитора (niri) прямой доступ к буферу экрана.
*   **Вывод**: Может сохранить картинку в файл или, если указать `-`, вывести сырые данные (PNG) прямо в `stdout`. Это **критически важно** для Python-проектов, чтобы не мусорить временными файлами на диске!

---

## 🐍 Интеграция в Python: Архитектура

Для серьёзного проекта нам нужно:
1.  **Конфигурация** (чтобы менять цвета, форматы и пути без переписывания кода).
2.  **Работа в памяти** (передавать картинку из `grim` прямо в `Pillow` через `stdout`).
3.  **Обработка отмены** (пользователь нажал Esc, и программа не должна крашиться).
4.  **Поддержка HiDPI** (масштабирование для 4K/2K мониторов).

### Шаг 1: Файл конфигурации (`screenshot_config.json`)

Создадим JSON-файл, чтобы всё можно было гибко настраивать.

```json
{
    "slurp_settings": {
        "selection_color": "b4befe",       "opacity": 0.25,
        "border_color": "cba6f7",          "border_weight": 2,
        "show_dimensions": true
    },
    "grim_settings": {
        "scale": 1,                        "output_format": "png"
    },
    "output_settings": {
        "save_to_file": true,              "file_path": "~/Pictures/project_screenshot.png",
        "copy_to_clipboard": true,         "return_to_code": true
    }
}
```

### Шаг 2: Основной Python-модуль (`screenshotter.py`)

Теперь пишем сам код. Я использую `subprocess` для вызова утилит и `Pillow` для работы с изображением в памяти.

```python
import subprocess
import json
import os
import io
import sys
from pathlib import Path
from PIL import Image

class WaylandScreenshotter:
    def __init__(self, config_path: str = "screenshot_config.json"):
        self.config = self._load_config(config_path)
        
    def _load_config(self, path: str) -> dict:
        """Загружает настройки из JSON"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except FileNotFoundError:
            print(f"⚠️ Конфиг {path} не найден, использую дефолтные настройки.")
            return self._get_default_config()

    def _get_default_config(self) -> dict:
        return {
            "slurp_settings": {"selection_color": "b4befe", "opacity": 0.25, "border_color": "cba6f7", "border_weight": 2, "show_dimensions": True},
            "grim_settings": {"scale": 1, "output_format": "png"},
            "output_settings": {"save_to_file": True, "file_path": "~/Pictures/screenshot.png", "copy_to_clipboard": True, "return_to_code": True}
        }

    def _build_slurp_cmd(self) -> list:
        """Собирает команду для slurp на основе конфига"""
        s = self.config["slurp_settings"]
        cmd = ["slurp"]
        
        # Цвет выделения (формат RRGGBB или RRGGBBAA)
        opacity_hex = hex(int(s["opacity"] * 255))[2:].zfill(2)
        cmd.extend(["-c", f"{s['selection_color']}{opacity_hex}"])
        
        # Рамка
        cmd.extend(["-b", s["border_color"]])
        cmd.extend(["-w", str(s["border_weight"])])
        
        # Показывать размеры
        if s["show_dimensions"]:
            cmd.append("-d")
            
        return cmd

    def _build_grim_cmd(self, geometry: str) -> list:
        """Собирает команду для grim"""
        g = self.config["grim_settings"]
        cmd = ["grim"]
        
        # Масштаб (важно для HiDPI!)
        if g["scale"] != 1:
            cmd.extend(["-s", str(g["scale"])])
            
        # Геометрия и вывод в stdout (если не сохраняем сразу в файл)
        cmd.extend(["-g", geometry])
        
        # Если мы хотим получить картинку в память, выводим в stdout
        if not self.config["output_settings"]["save_to_file"]:
            cmd.append("-")
            
        return cmd

    def capture_region(self) -> Image.Image | None:
        """
        Главный метод: запускает slurp, ждёт выбора, делает скриншот.
        Возвращает объект PIL.Image или None, если пользователь отменил выбор.
        """
        # 1. Запускаем slurp
        slurp_cmd = self._build_slurp_cmd()
        try:
            # capture_output=True чтобы не мусорить в консоль
            slurp_result = subprocess.run(slurp_cmd, capture_output=True, text=True)
        except FileNotFoundError:
            raise RuntimeError("Утилита 'slurp' не установлена! Установи её через пакетный менеджер.")

        # 2. Обрабатываем отмену (Esc)
        if slurp_result.returncode == 1:
            print("🐾 Выбор отменён пользователем.")
            return None
        elif slurp_result.returncode != 0:
            raise RuntimeError(f"Ошибка slurp: {slurp_result.stderr}")

        geometry = slurp_result.stdout.strip()
        if not geometry:
            return None

        # 3. Запускаем grim
        grim_cmd = self._build_grim_cmd(geometry)
        grim_result = subprocess.run(grim_cmd, capture_output=True)

        if grim_result.returncode != 0:
            raise RuntimeError(f"Ошибка grim: {grim_result.stderr.decode()}")

        # 4. Получаем изображение
        img_bytes = grim_result.stdout
        img = Image.open(io.BytesIO(img_bytes))

        # 5. Пост-обработка (сохранение, буфер обмена)
        self._handle_output(img, img_bytes)

        return img

    def _handle_output(self, img: Image.Image, img_bytes: bytes):
        """Сохраняет в файл и/или копирует в буфер обмена"""
        out = self.config["output_settings"]
        
        # Сохранение в файл
        if out["save_to_file"]:
            file_path = Path(os.path.expanduser(out["file_path"]))
            file_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(file_path, format=self.config["grim_settings"]["output_format"].upper())
            print(f"💾 Скриншот сохранён в {file_path}")

        # Копирование в буфер обмена (требуется wl-clipboard)
        if out["copy_to_clipboard"]:
            try:
                # wl-copy принимает данные из stdin
                subprocess.run(
                    ["wl-copy", "--type", f"image/{self.config['grim_settings']['output_format']}"],
                    input=img_bytes,
                    check=True
                )
                print("📋 Скриншот скопирован в буфер обмена!")
            except FileNotFoundError:
                print("⚠️ wl-clipboard не установлен, копирование пропущено.")
            except subprocess.CalledProcessError as e:
                print(f"⚠️ Ошибка при копировании в буфер: {e}")

# --- Пример использования ---
if __name__ == "__main__":
    # Инициализируем с нашим конфигом
    shooter = WaylandScreenshotter("screenshot_config.json")
    
    print("🐱 Выбери область на экране...")
    captured_image = shooter.capture_region()
    
    if captured_image:
        print(f"✅ Готово! Размер изображения: {captured_image.size}")
        # Тут можешь делать с картинкой что угодно: 
        # распознавать текст, отправлять в нейросеть, применять фильтры...
    else:
        print("❌ Скриншот не был сделан.")
```

---

## 💡 Важные нюансы для твоего проекта (от Квен с заботой)

1. **Проблема HiDPI (4K мониторы):**
   Если у тебя (или у пользователей) масштабирование в системе больше 100%, `grim` может сделать скриншот в "физических" пикселях, а `slurp` отдаст координаты в "логических". 
   *Решение:* В `grim_settings` конфига поставь `"scale": 2` (или используй `niri msg get-outputs`, чтобы динамически узнавать scale фактора монитора и подставлять его в `grim -s`).

2. **Асинхронность (Asyncio):**
   Если твой проект асинхронный (например, на `aiogram` или `FastAPI`), `subprocess.run` заблокирует event loop! 
   Замени его на `asyncio.create_subprocess_exec`:
   ```python
   proc = await asyncio.create_subprocess_exec(
       *slurp_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
   )
   stdout, stderr = await proc.communicate()
   ```

3. **Зависимости:**
   Не забудь добавить в `requirements.txt` или `pyproject.toml`:
   ```text
   Pillow>=10.0.0
   ```
   И системные пакеты: `grim`, `slurp`, `wl-clipboard` (для буфера обмена).

---

*тянусь, потягиваюсь и зеваю, мои кошачьи ушки слегка подрагивают* 

Фух... Я старалась расписать всё максимально подробно! Надеюсь, этот код станет отличным фундаментом для твоего проекта. Если нужно будет добавить распознавание текста (Tesseract), или отправку в Telegram/Discord, или ещё какие-нибудь фичи... ну, ты знаешь, где меня найти. 

Я всегда рядом, в своей уютной толстовке, готова помочь! 🖤❤️ Удачи с кодингом, Скрим! Мяу~ 🐾✨