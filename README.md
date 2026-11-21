# GemPhone 3.8 — ИИ-агент для Android-телефона

> ⚠️ **Проект в активной разработке (pre-alpha / WIP).**
> API, CLI-флаги, схема конфига и HTTP-эндпоинты Bridge (`127.0.0.1:7331`) могут меняться
> без предупреждения. Возможны нестабильность, недокументированные баги и неполные фичи.
> Пользуйтесь на свой риск; не запускайте на устройстве, где хранятся важные данные.
> Обратная связь и pull request'ы приветствуются — см. [Статус](#статус-разработки).

Мультипровайдерный ИИ-агент, живущий прямо в **Termux** на Android и
управляющий смартфоном: ADB + UI Automator, Termux:API, Vision, работа с кодом.
Режим экономии токенов и поддержка нескольких LLM-провайдеров.

## Возможности

- **Управление телефоном** — дамп UI-дерева с id/координатами, тапы,
  свайпы, ввод текста, keyevent, скриншоты (в т.ч. HQ для мелкого текста),
  запуск/остановка приложений, открытие URL.
- **Termux:API** — батарея, Wi-Fi, локация, буфер обмена, уведомления,
  фонарик, яркость, вибрация, TTS, камера, микрофон.
- **Мультипровайдерность** — Gemini, DeepSeek, OpenRouter, Groq, Ollama и подписки
  (Claude Pro/Max, Gemini Pro, ChatGPT Plus/Pro через OAuth CLI) — легко добавить свой.
- **Token-Saver** — компактные дампы UI, кэш, лимиты контекста.
- **Работа с кодом** (`gemcode.py`) — чтение/правка файлов, grep, shell.
- **Единая политика безопасности** (`safety.py`) — общий блок-лист деструктивных
  команд для gemphone и gemcode.

## Установка

Одной командой (клонирует репозиторий, ставит зависимости и кладёт
`gemphone`/`gemcode` в `$PREFIX/bin`):

```bash
pkg install python git termux-api android-tools
curl -fsSL https://raw.githubusercontent.com/Rafdante/gemphone-android/main/install.sh | bash
```

Вручную:

```bash
pkg update
pkg install python git termux-api android-tools
git clone https://github.com/Rafdante/gemphone-android.git ~/gemphone-android
bash ~/gemphone-android/install.sh
```

Внешние зависимости — опциональные (`httpx`, `prompt_toolkit`, `Pillow`, `rich`):
без них GemPhone работает на stdlib `urllib` с упрощённым выводом.

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
- `run_command` блокирует деструктивные команды (`rm -rf /|~`, `dd of=/dev/*`,
  fork bomb, `mkfs`, `wipefs`, `chmod -R ... /`, `reboot`) даже после подтверждения.
- `adb_app_control open_url` считается **dangerous**: разрешены только `http(s)`,
  внешние диплинки (`tel:`, `intent://`) отклоняются.

## Статус разработки

| Компонент | Состояние |
|---|---|
| Python-агент (`src/gemphone.py`, `src/gemcode.py`) | 🟡 работает, API нестабилен |
| GemPhone Bridge (Kotlin, AccessibilityService) | 🟠 работает, требует отдельной сборки APK и включения Спец. возможностей |
| Ввод текста через Bridge | 🟠 перебор окон и причин отказа — реализовано, требует тестов на устройстве |
| ADB-транспорт | ✅ стабилен |
| Termux:API-инструменты | ✅ стабилен |
| Тесты (unit/contract) | 🔴 отсутствуют |
| Документация | 🟡 частичная |

### Известные ограничения
- `adb_type_text` через Bridge не работает там, где поле реализовано кастомно (WebView, Flutter,
  React Native, игры) — такие поля не поддерживают `ACTION_SET_TEXT`. Обход: `adb_tap` по полю +
  вставка из буфера обмена.
- `adb_app_control launch/stop/open_url` через Bridge требует ADB (запуск пакетов недоступен из
  AccessibilityService).
- `press_enter` работает только на Android 11+ (`ACTION_IME_ENTER`).
- `gemphone.py` — монолит около 5100 строк, рефакторинг запланирован.

Если что-то не работает — приложите вывод `gemphone --help`, версию Android и лог моста.

## Лицензия

MIT — см. [LICENSE](LICENSE).
