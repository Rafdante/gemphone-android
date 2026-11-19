# GemPhone — ИИ-агент для Android-телефона

Мультипровайдерный ИИ-агент, живущий прямо в **Termux** на Android и
управляющий смартфоном: ADB + UI Automator, Termux:API, Vision, работа с кодом.
Режим экономии токенов и поддержка нескольких LLM-провайдеров.

## Возможности

- **Управление телефоном** — дамп UI-дерева с id/координатами, тапы,
  свайпы, ввод текста, keyevent, скриншоты (в т.ч. HQ для мелкого текста),
  запуск/остановка приложений, открытие URL.
- **Termux:API** — батарея, Wi-Fi, локация, буфер обмена, уведомления,
  фонарик, яркость, вибрация, TTS, камера, микрофон.
- **Мультипровайдерность** — Gemini, DeepSeek, OpenRouter (легко добавить свой).
- **Token-Saver** — компактные дампы UI, кэш, лимиты контекста.
- **Работа с кодом** (`gemcode.py`) — чтение/правка файлов, grep, shell.

## Установка

```bash
pkg update
pkg install python git termux-api android-tools
pip install -r requirements.txt
mkdir -p ~/bin
cp src/gemphone.py src/gemcode.py ~/bin/
chmod +x ~/bin/gemphone.py ~/bin/gemcode.py
```

## Настройка API-ключей

⚠️ **Ключи никогда не попадают в репозиторий.** Они хранятся только локально
в `~/.gemphone/config.json` (путь исключён в `.gitignore`).

1. Скопируйте шаблон:
   ```bash
   mkdir -p ~/.gemphone
   cp config.example.json ~/.gemphone/config.json
   chmod 600 ~/.gemphone/config.json
   ```
2. Впишите ключи в `api_key` или (предпочтительно) задайте их через переменные
   окружения:
   ```bash
   export GEMINI_API_KEY="..."
   export DEEPSEEK_API_KEY="..."
   export OPENROUTER_API_KEY="..."
   ```

Агент берёт ключ из `api_key`, а если он пуст — из переменной `api_key_env`.

## ADB

Для управления интерфейсом нужен доступ по ADB (беспроводная отладка):
подключитесь `adb connect <ip>:<port>` один раз, затем запускайте агента.

## Запуск

```bash
gemphone.py   # агент управления телефоном
gemcode.py    # агент для работы с кодом
```

## Безопасность

- В коде нет захардкоженных ключей и токенов.
- `.gitignore` исключает `config.json`, `.env*`, `*.key`, историю промптов.
- Публикуя форк — не коммитьте свой `~/.gemphone/config.json`.

## Лицензия

MIT — см. [LICENSE](LICENSE).
